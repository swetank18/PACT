"""
PayPal webhooks, delivered the way PayPal delivers them: signed, sometimes twice,
sometimes late, sometimes forged.

Every delivery here comes from `FakePayPal.deliver`, so it is verified by the
same postback the real receiver makes. What is being shown is that a pending
capture settles on the event, exactly once, and that anything the receiver
cannot verify changes nothing.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from contracts.reason_codes import ReasonCode
from contracts.schemas import QuoteItemRequest
from rails.paypal.adapter import PayPalAdapter
from rails.paypal.client import Credentials, PayPalClient
from rails.paypal.webhooks import PayPalWebhookProcessor, build_app
from tests.fake_paypal import CLIENT_ID, CLIENT_SECRET, VAULT_ID, WEBHOOK_ID, FakePayPal


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("rails.paypal.client.time.sleep", lambda _s: None)


@pytest.fixture
def api() -> FakePayPal:
    fake = FakePayPal()
    fake.capture_status = "PENDING"
    return fake


@pytest.fixture
def rail(api, db):
    creds = Credentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                        webhook_id=WEBHOOK_ID, vault_id=VAULT_ID)
    return PayPalAdapter(PayPalClient(db, creds, transport=api.transport))


@pytest.fixture
def processor(db, rail, merchant):
    return PayPalWebhookProcessor(db, rail, settle=merchant.saga.settle_pending)


@pytest.fixture
def pending_order(quotes, make_mandate, authorize, merchant):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="FUR-LMP-01")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)
    assert result.final_state == "PAYMENT_PENDING"
    return merchant.orders.get(result.order_id), mandate, q


def _event(capture_id: str, event_type: str, event_id: str = "WH-EVT-0001") -> dict:
    return {"id": event_id, "event_type": event_type, "resource_type": "capture",
            "resource": {"id": capture_id, "status": event_type.rsplit(".", 1)[-1]}}


def _states(merchant, order_id):
    return [s["state"] for s in merchant.audit.list_steps(order_id)]


def test_a_completed_capture_fulfils_the_pending_order(api, processor, merchant, pending_order):
    order, _, _ = pending_order
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED"))

    outcome = processor.process(body, headers)

    assert outcome.applied and not outcome.duplicate
    assert _states(merchant, order.order_id)[-2:] == ["PAYMENT_CAPTURED", "FULFILLED"]


def test_a_denied_capture_gives_the_budget_back(api, processor, merchant, gate, pending_order):
    order, mandate, q = pending_order
    before = gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints)
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.DENIED"))

    assert processor.process(body, headers).applied
    last = merchant.audit.list_steps(order.order_id)[-1]
    assert last["state"] == "ROLLED_BACK"
    assert last["reason_code"] == ReasonCode.RAIL_CAPTURE_FAILED.value
    assert gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints) == before + q.total_minor


def test_a_redelivery_changes_nothing(api, processor, merchant, pending_order):
    order, _, _ = pending_order
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED"))
    processor.process(body, headers)

    again = processor.process(body, headers)

    assert again.duplicate and not again.applied
    assert _states(merchant, order.order_id).count("FULFILLED") == 1


def test_an_event_for_an_order_that_moved_on_is_acknowledged_and_ignored(
    api, processor, merchant, pending_order
):
    order, _, _ = pending_order
    first, h1 = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED", "WH-1"))
    processor.process(first, h1)
    late, h2 = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.DENIED", "WH-2"))

    outcome = processor.process(late, h2)

    assert not outcome.applied and "FULFILLED" in outcome.detail
    assert _states(merchant, order.order_id)[-1] == "FULFILLED", "never walks backwards"


def test_a_delivery_that_does_not_verify_changes_nothing(api, processor, merchant, db, pending_order):
    order, _, _ = pending_order
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED"))
    forged = body.replace(b"COMPLETED", b"COMPLETED ")

    with pytest.raises(PermissionError):
        processor.process(forged, headers)
    assert merchant.orders.get(order.order_id).state == "PAYMENT_PENDING"
    with db.read_tx() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM rail_events").fetchone()["n"] == 0


def test_an_event_we_do_not_act_on_is_acknowledged(api, processor, pending_order):
    order, _, _ = pending_order
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.REFUNDED"))
    outcome = processor.process(body, headers)
    assert not outcome.applied and outcome.detail == "not an event we act on"


def test_a_failed_apply_lets_the_redelivery_try_again(api, db, rail, merchant, pending_order):
    """
    The event is claimed before it is applied. If applying throws, the claim
    is released, so PayPal's redelivery is not mistaken for a duplicate of
    something that never happened.
    """
    order, _, _ = pending_order
    calls = {"n": 0}

    def flaky(order_id: str, meaning: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the database blinked")
        return merchant.saga.settle_pending(order_id, meaning)

    processor = PayPalWebhookProcessor(db, rail, settle=flaky)
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED"))
    with pytest.raises(RuntimeError):
        processor.process(body, headers)

    retried = processor.process(body, headers)
    assert retried.applied and not retried.duplicate
    assert _states(merchant, order.order_id)[-1] == "FULFILLED"


def test_over_http_a_good_delivery_is_200_and_a_forged_one_400(api, processor, pending_order):
    order, _, _ = pending_order
    client = TestClient(build_app(processor, path="/rail"))
    body, headers = api.deliver(_event(order.rail_payment_id, "PAYMENT.CAPTURE.COMPLETED"))

    forged = client.post("/rail", content=body.replace(b"COMPLETED", b"DENIED"), headers=headers)
    assert forged.status_code == 400

    good = client.post("/rail", content=body, headers=headers)
    assert good.status_code == 200 and good.json()["applied"] is True
    assert json.loads(good.content)["duplicate"] is False


@pytest.mark.parametrize(("profile", "keys", "title"), [
    ("paypal-agentic", True, "PACT PayPal webhook receiver"),
    ("razorpay-track01", False, "PACT webhook receiver"),
])
def test_the_deployed_build_mounts_the_receiver_for_its_rail(tmp_path, profile, keys, title):
    """
    One instance settles on one rail, so it mounts that rail's receiver. A
    PayPal instance answering webhooks with the Razorpay processor would read
    PayPal's five signature headers as one HMAC and refuse every delivery.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PACT_RAIL", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET")}
    env.update(PACT_PROFILE=profile, PACT_DB_URL="sqlite:///:memory:",
               PACT_GATE_KEY_PATH=str(tmp_path / "gate.hex"))
    if keys:
        env.update(PAYPAL_CLIENT_ID=CLIENT_ID, PAYPAL_CLIENT_SECRET=CLIENT_SECRET)
    done = subprocess.run(
        [sys.executable, "-c", "import deploy.app as d; print(d.webhook_app.title)"],
        env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    assert done.stdout.strip().splitlines()[-1] == title
