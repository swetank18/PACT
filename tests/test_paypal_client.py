"""
The PayPal path, exercised.

Driven through `tests/fake_paypal.py`, which enforces `rails/paypal/API_NOTES.md`.
As with the Razorpay tests, what is mostly being checked is the **request**: that
money goes as a string with the currency's decimals, that the order names the
vaulted wallet as a merchant-initiated stored credential, that every write asks
for the representation, and that one saga key becomes a different
`PayPal-Request-Id` per call type.

The last section runs the real saga on this rail — forward to FULFILLED, and a
stockout through refund, budget release and an alternative — because a rail
that passes its own tests and fails under the saga is exactly what the Razorpay
client turned out to be.

The boundary: this verifies the client against the notes. A field the real
sandbox requires that the notes failed to record is still invisible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from contracts.schemas import QuoteItemRequest
from core.rail import RailAdapter
from rails.paypal.adapter import PayPalAdapter
from rails.paypal.client import (
    Credentials,
    PayPalClient,
    PayPalError,
    from_value,
    request_id,
    to_value,
)
from rails.razorpay.client import idempotency_key
from tests.fake_paypal import CLIENT_ID, CLIENT_SECRET, VAULT_ID, WEBHOOK_ID, FakePayPal

AMOUNT = 24_999  # $249.99
REPO = Path(__file__).resolve().parents[1]


def creds(**overrides) -> Credentials:
    base = dict(client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                webhook_id=WEBHOOK_ID, vault_id=VAULT_ID)
    base.update(overrides)
    return Credentials(**base)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """The retry backoff is real seconds; the paths it guards are what is tested."""
    monkeypatch.setattr("rails.paypal.client.time.sleep", lambda _s: None)


@pytest.fixture
def api() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def client(api, db) -> PayPalClient:
    return PayPalClient(db, creds(), transport=api.transport)


@pytest.fixture
def adapter(client) -> PayPalAdapter:
    return PayPalAdapter(client)


def calls(api: FakePayPal) -> list[str]:
    return [f"{method} {path}" for method, path, *_ in api.seen if path != "/v1/oauth2/token"]


def last(api: FakePayPal, path_fragment: str):
    return next(s for s in reversed(api.seen) if path_fragment in s[1])


@pytest.fixture
def captured(api, adapter):
    """An authorised order and its capture: where the refund tests start."""
    intent = adapter.create_intent(AMOUNT, ref="ord_ref", idem_key="k_order")
    result = adapter.capture(intent.raw["payment_id"], AMOUNT, "k_capture")
    assert result.ok
    return intent, result


# ------------------------------------------------------------------ setup ---


def test_the_adapter_satisfies_the_rail_protocol(adapter):
    assert isinstance(adapter, RailAdapter)


def test_anything_but_the_sandbox_is_refused_at_construction(db):
    """There is no key prefix to check, so the host is the guard."""
    with pytest.raises(RuntimeError, match="sandbox only"):
        PayPalClient(db, creds(base_url="https://api-m.paypal.com"))
    with pytest.raises(RuntimeError, match="sandbox only"):
        PayPalClient(db, creds(base_url="https://api-m.sandbox.paypal.com.evil.example"))


def test_rupees_are_refused_rather_than_sent_as_dollars(db):
    """
    PayPal does not list INR, and the catalog is priced in paise. Sending
    74900 paise as "749.00 USD" would be a confident wrong charge, so the rail
    will not start in a currency PayPal cannot settle.
    """
    with pytest.raises(RuntimeError, match="INR"):
        PayPalClient(db, creds(), currency="INR")


def test_the_merchant_will_not_boot_on_paypal_with_the_rupee_catalog():
    """
    The same refusal, one level up, where it matters: `PACT_RAIL=paypal` on
    the catalog as it stands stops the merchant at import instead of starting a
    service that would charge 74900 paise as $749.00. A subprocess, because the
    merchant builds its service when the module is imported.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PACT_RAIL="paypal", PACT_DB_URL="sqlite:///:memory:",
               PAYPAL_CLIENT_ID=CLIENT_ID, PAYPAL_CLIENT_SECRET=CLIENT_SECRET)
    done = subprocess.run([sys.executable, "-c", "import merchant.app"], env=env,
                          capture_output=True, text=True, cwd=REPO, timeout=60)
    assert done.returncode != 0
    assert "PayPal cannot settle INR" in done.stderr


def test_without_credentials_it_refuses_rather_than_pretending(db, api):
    client = PayPalClient(db, creds(client_id="", client_secret=""), transport=api.transport)
    with pytest.raises(PayPalError) as exc:
        client.get_order("ORD1")
    assert exc.value.code == "NO_CREDENTIALS"
    assert api.seen == []


# ------------------------------------------------------------------ money ---


@pytest.mark.parametrize(("minor", "currency", "value"), [
    (24_999, "USD", "249.99"),
    (5, "USD", "0.05"),
    (100, "USD", "1.00"),
    (1_200, "JPY", "1200"),
])
def test_minor_units_become_the_string_paypal_takes(minor, currency, value):
    assert to_value(minor, currency) == value
    assert from_value(value, currency) == minor


def test_money_never_goes_through_a_float():
    with pytest.raises(ValueError):
        to_value(249.99, "USD")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_value(True, "USD")  # type: ignore[arg-type]


def test_a_value_finer_than_the_currency_is_refused_not_rounded():
    """Rounding would invent or lose a fraction of a cent in a ledger that
    agrees to the unit."""
    with pytest.raises(ValueError):
        from_value("249.999", "USD")
    with pytest.raises(ValueError):
        from_value("12.5", "JPY")


# ------------------------------------------------------------------- auth ---


def test_one_token_serves_many_calls(api, adapter):
    adapter.create_intent(AMOUNT, ref="a", idem_key="k1")
    adapter.create_intent(AMOUNT, ref="b", idem_key="k2")

    token_calls = [s for s in api.seen if s[1] == "/v1/oauth2/token"]
    assert len(token_calls) == 1
    assert token_calls[0][3]["authorization"].startswith("Basic ")
    assert last(api, "/v2/checkout/orders")[3]["authorization"].startswith("Bearer ")


def test_a_revoked_token_is_replaced_once(api, client):
    with pytest.raises(PayPalError):
        client.get_order("ORD_missing")
    api.revoke_tokens()
    with pytest.raises(PayPalError) as exc:
        client.get_order("ORD_missing")
    # It got past the 401 to the real answer: a fresh token, then a 404.
    assert exc.value.status == 404
    assert sum(1 for s in api.seen if s[1] == "/v1/oauth2/token") == 2


def test_a_401_that_persists_is_an_error_not_a_loop(api, client, monkeypatch):
    monkeypatch.setattr(api, "tokens", _NeverValid())
    with pytest.raises(PayPalError) as exc:
        client.get_order("ORD1")
    assert exc.value.status == 401
    assert sum(1 for s in api.seen if s[1] == "/v1/oauth2/token") == 2


class _NeverValid(set):
    def __contains__(self, item):  # noqa: D105
        return False


# ------------------------------------------------------------ create_intent --


def test_the_order_authorises_against_the_vaulted_wallet(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="ord_123", idem_key="k")

    _, _, body, headers, _ = last(api, "/v2/checkout/orders")
    assert body["intent"] == "AUTHORIZE"
    unit = body["purchase_units"][0]
    assert unit["amount"] == {"currency_code": "USD", "value": "249.99"}
    assert unit["invoice_id"] == "ord_123"
    wallet = body["payment_source"]["paypal"]
    assert wallet["vault_id"] == VAULT_ID
    assert wallet["stored_credential"] == {
        "payment_initiator": "MERCHANT", "usage": "SUBSEQUENT",
        "usage_pattern": "UNSCHEDULED_POSTPAID",
    }
    assert headers["prefer"] == "return=representation"

    assert intent.status == "authorized"
    assert intent.amount_minor == AMOUNT
    assert intent.raw["payment_id"] in api.authorizations


def test_without_the_representation_there_is_no_authorization_to_capture(api, db):
    """
    Why every write sends `Prefer: return=representation`. The default is
    minimal, and a minimal create has no authorization id in it — the one
    thing the saga needs next.
    """
    client = PayPalClient(db, creds(), transport=api.transport)
    token = client._access_token()
    response = httpx.Client(transport=api.transport, base_url=client.credentials.base_url).post(
        "/v2/checkout/orders",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"intent": "AUTHORIZE",
              "purchase_units": [{"amount": {"currency_code": "USD", "value": "1.00"}}],
              "payment_source": {"paypal": {"vault_id": VAULT_ID}}},
    )
    assert set(response.json()) <= {"id", "status", "links"}


def test_a_denied_authorization_ends_the_purchase_at_create(api, adapter):
    """COMPLETED on the order is not payment. The authorization says that."""
    api.vaulted[VAULT_ID] = False
    with pytest.raises(PayPalError) as exc:
        adapter.create_intent(AMOUNT, ref="ord_denied", idem_key="k")
    assert exc.value.code == "AUTHORIZATION_DENIED"


def test_no_vaulted_wallet_is_refused_before_paypal_is_called(api, db):
    adapter = PayPalAdapter(PayPalClient(db, creds(vault_id=""), transport=api.transport))
    with pytest.raises(PayPalError) as exc:
        adapter.create_intent(AMOUNT, ref="x", idem_key="k")
    assert exc.value.code == "NO_VAULT_ID"
    assert calls(api) == []


def test_a_second_order_for_the_same_reference_is_refused_by_paypal_too(api, adapter):
    adapter.create_intent(AMOUNT, ref="ord_once", idem_key="k1")
    with pytest.raises(PayPalError) as exc:
        adapter.create_intent(AMOUNT, ref="ord_once", idem_key="k2")
    assert exc.value.code == "DUPLICATE_INVOICE_ID"


# ---------------------------------------------------------------- capture ---


def test_one_saga_key_is_a_different_request_id_per_call_type(api, adapter):
    """
    The saga passes one key to `create_intent` and to `capture`, and PayPal
    wants a request id unique per call type. The Razorpay client served the
    order back as the capture on exactly this; this one must not.
    """
    idem = idempotency_key("ord_saga", AMOUNT, 1)
    intent = adapter.create_intent(AMOUNT, ref="ord_saga", idem_key=idem)
    result = adapter.capture(intent.raw["payment_id"], AMOUNT, idem)

    assert result.ok and result.status == "captured" and not result.replayed
    ids = [s[3]["paypal-request-id"] for s in api.seen if "paypal-request-id" in s[3]]
    assert ids == [request_id("create_order", idem), request_id("capture", idem)]
    assert len(set(ids)) == 2


def test_capture_takes_exactly_the_amount_and_closes_the_authorization(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    adapter.capture(intent.raw["payment_id"], AMOUNT, "k_c")

    _, path, body, _, _ = last(api, "/capture")
    assert path == f"/v2/payments/authorizations/{intent.raw['payment_id']}/capture"
    assert body == {"amount": {"currency_code": "USD", "value": "249.99"}, "final_capture": True}


def test_a_pending_capture_counts_as_spent_and_says_it_is_pending(api, adapter):
    """See API_NOTES' open question: safe for the ceiling, named on screen."""
    api.capture_status = "PENDING"
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    result = adapter.capture(intent.raw["payment_id"], AMOUNT, "k_c")
    assert result.ok
    assert result.status == "capture_pending"


def test_a_declined_capture_fails_and_releases_the_hold(api, adapter):
    api.capture_status = "DECLINED"
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    aid = intent.raw["payment_id"]
    result = adapter.capture(aid, AMOUNT, "k_c")

    assert not result.ok
    assert result.error_code == "CAPTURE_DECLINED"
    assert "DECLINED_BY_RISK_FRAUD_FILTERS" in (result.error_detail or "")
    assert f"POST /v2/payments/authorizations/{aid}/void" in calls(api)
    assert api.authorizations[aid]["status"] == "VOIDED"


def test_capturing_more_than_was_authorised_is_refused(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    result = adapter.capture(intent.raw["payment_id"], AMOUNT + 1, "k_c")
    assert not result.ok
    assert result.error_code == "MAX_CAPTURE_AMOUNT_EXCEEDED"
    assert "debug_id" in (result.error_detail or "")


def test_the_same_key_never_reaches_paypal_twice(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    first = adapter.capture(intent.raw["payment_id"], AMOUNT, "same")
    before = len(api.seen)
    second = adapter.capture(intent.raw["payment_id"], AMOUNT, "same")

    assert len(api.seen) == before
    assert second.replayed and second.ref == first.ref and second.ok


def test_a_replay_after_the_refund_still_reports_the_capture(api, captured, adapter, db):
    """
    PayPal answers a repeated request id with the resource as it is *now*. A
    capture replayed after its refund would come back REFUNDED and read as a
    failed capture. The local table answers with what happened instead.
    """
    intent, capture = captured
    adapter.refund(capture.ref, AMOUNT, "k_refund")

    again = adapter.capture(intent.raw["payment_id"], AMOUNT, "k_capture")
    assert again.ok and again.status == "captured" and again.replayed

    # And this is what PayPal itself would have said, which is the reason.
    fresh = PayPalAdapter(PayPalClient(db.__class__("sqlite:///:memory:"), creds(),
                                       transport=api.transport))
    from_paypal = fresh.capture(intent.raw["payment_id"], AMOUNT, "k_capture")
    assert from_paypal.status == "refunded"
    assert not from_paypal.ok


def test_a_capture_already_made_on_another_key_converges(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    aid = intent.raw["payment_id"]
    first = adapter.capture(aid, AMOUNT, "k_first")

    second = adapter.capture(aid, AMOUNT, "k_second")
    assert second.ok and second.replayed
    assert second.ref == first.ref
    assert api.authorizations[aid]["status"] == "CAPTURED", "and nothing was voided"


def test_a_capture_whose_answer_was_lost_is_found_not_rolled_back(api, adapter):
    """
    The call that may have landed. Reporting it failed would roll the order
    back and release the budget while the money is gone.
    """
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    # Two refusals, then the attempt that lands and loses its answer, so the
    # retries run out and only reading the authorization back can find it.
    api.fail_with = [500, 500]
    api.drop_after_apply = True
    result = adapter.capture(intent.raw["payment_id"], AMOUNT, "k_c")

    assert result.ok, result.error_detail
    assert result.ref in api.captures
    assert len(api.captures) == 1


def test_an_unreachable_paypal_fails_the_capture_and_does_not_invent_one(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    api.fail_with = [500] * 20
    result = adapter.capture(intent.raw["payment_id"], AMOUNT, "k_c")
    assert not result.ok
    assert api.captures == {}


# ---------------------------------------------------------------- refunds ---


def test_a_refund_always_names_its_amount(api, captured, adapter):
    _, capture = captured
    result = adapter.refund(capture.ref, AMOUNT, "k_r")

    _, path, body, headers, _ = last(api, "/refund")
    assert path == f"/v2/payments/captures/{capture.ref}/refund"
    assert body == {"amount": {"currency_code": "USD", "value": "249.99"}}
    assert headers["paypal-request-id"] == request_id("refund", "k_r")
    assert result.ok and result.status == "completed"


def test_a_pending_refund_is_reported_as_pending(api, captured, adapter):
    _, capture = captured
    api.refund_status = "PENDING"
    result = adapter.refund(capture.ref, AMOUNT, "k_r")
    assert result.ok
    assert result.status == "pending", "a pending refund is not money returned"


def test_refunding_more_than_was_captured_is_refused(api, captured, adapter):
    _, capture = captured
    result = adapter.refund(capture.ref, AMOUNT + 1, "k_r")
    assert not result.ok
    assert result.error_code == "REFUND_AMOUNT_EXCEEDED"


def test_retries_are_bounded_and_failures_are_not_remembered(api, captured, adapter, db):
    _, capture = captured
    api.fail_with = [503, 503, 503]
    failed = adapter.refund(capture.ref, AMOUNT, "k_r")
    assert not failed.ok and failed.error_code == "TRANSPORT"

    with db.read_tx() as conn:
        assert not conn.execute(
            "SELECT 1 FROM idempotency WHERE operation = 'paypal.refund'").fetchall()

    retried = adapter.refund(capture.ref, AMOUNT, "k_r")
    assert retried.ok and not retried.replayed


def test_a_conflict_on_an_in_flight_request_is_waited_out(api, captured, adapter):
    """409 is "a previous call for the given resource is in progress"."""
    _, capture = captured
    api.fail_with = [409]
    assert adapter.refund(capture.ref, AMOUNT, "k_r").ok


# ----------------------------------------------------------------- status ---


def test_status_reports_captured_only_for_a_completed_capture(api, captured, adapter):
    intent, capture = captured
    status = adapter.status(intent.intent_id)
    assert status.status == "captured"
    assert status.payment_id == capture.ref
    assert status.amount_minor == AMOUNT


def test_status_counts_what_was_refunded(api, captured, adapter):
    intent, capture = captured
    adapter.refund(capture.ref, 10_000, "k_r")
    status = adapter.status(intent.intent_id)
    assert status.status == "partially_refunded"
    assert status.amount_refunded_minor == 10_000


def test_an_authorised_order_is_not_reported_as_captured(api, adapter):
    intent = adapter.create_intent(AMOUNT, ref="o", idem_key="k_o")
    status = adapter.status(intent.intent_id)
    assert status.status == "authorized"
    assert status.payment_id is None


# ------------------------------------------------------------------ vault ---


def test_a_wallet_vaulted_once_pays_for_the_orders_after_it(api, client, db):
    """
    The one step with a human in it. Setup token, the holder approves at
    PayPal, payment token — and the id that comes back is what every later
    order charges, with nobody at the approve button.
    """
    setup = client.create_setup_token("https://pact.example/ok", "https://pact.example/no", "k_v")
    _, _, body, _, _ = last(api, "/v3/vault/setup-tokens")
    assert body["payment_source"]["paypal"]["usage_type"] == "MERCHANT"
    approve = next(link["href"] for link in setup["links"] if link["rel"] == "approve")
    assert approve.startswith("https://www.sandbox.paypal.com/")

    api.approve(setup["id"])
    assert client.get_setup_token(setup["id"])["status"] == "APPROVED"
    vault_id = client.create_payment_token(setup["id"], "k_v")["id"]

    adapter = PayPalAdapter(client, vault_id_for=lambda _ref: vault_id)
    intent = adapter.create_intent(AMOUNT, ref="ord_after_vault", idem_key="k")
    assert last(api, "/v2/checkout/orders")[2]["payment_source"]["paypal"]["vault_id"] == vault_id
    assert adapter.capture(intent.raw["payment_id"], AMOUNT, "k").ok


def test_a_wallet_nobody_approved_is_not_vaulted(api, client):
    setup = client.create_setup_token("https://pact.example/ok", "https://pact.example/no", "k_v")
    with pytest.raises(PayPalError) as exc:
        client.create_payment_token(setup["id"], "k_v")
    assert exc.value.status == 422


# --------------------------------------------------------------- webhooks ---


def _event() -> dict:
    return {"id": "WH-EVT-1", "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": "CAP1", "status": "COMPLETED",
                         "amount": {"currency_code": "USD", "value": "249.99"}}}


def test_a_genuine_delivery_verifies(api, adapter):
    body, headers = api.deliver(_event())
    assert adapter.verify_callback(body, json.dumps(headers))


def test_the_event_goes_back_byte_for_byte(api, adapter):
    """
    Whitespace and all. A client that parsed and re-serialised the body would
    send PayPal a different event from the one it signed.
    """
    body, headers = api.deliver(_event(), pretty=True)
    assert b"\n" in body
    assert adapter.verify_callback(body, json.dumps(headers))
    assert body in last(api, "verify-webhook-signature")[4]


def test_a_tampered_body_is_rejected(api, adapter):
    body, headers = api.deliver(_event())
    tampered = body.replace(b"249.99", b"2.49")
    assert not adapter.verify_callback(tampered, json.dumps(headers))


def test_a_missing_header_is_rejected_without_asking_paypal(api, adapter):
    body, headers = api.deliver(_event())
    del headers["PAYPAL-TRANSMISSION-SIG"]
    before = len(api.seen)
    assert not adapter.verify_callback(body, json.dumps(headers))
    assert len(api.seen) == before


def test_no_webhook_id_rejects_everything(api, db):
    adapter = PayPalAdapter(PayPalClient(db, creds(webhook_id=""), transport=api.transport))
    body, headers = api.deliver(_event())
    assert not adapter.verify_callback(body, json.dumps(headers))


def test_paypal_unreachable_is_a_rejection_not_a_pass(api, adapter):
    body, headers = api.deliver(_event())
    api.fail_with = [500] * 10
    assert not adapter.verify_callback(body, json.dumps(headers))


@pytest.mark.parametrize("signature", ["", "not json", "[1, 2]", "null"])
def test_a_signature_that_is_not_the_header_bundle_is_rejected(adapter, signature):
    assert not adapter.verify_callback(b"{}", signature)


# ------------------------------------------------------------ under the saga --


@pytest.fixture
def paypal_rail(api, db):
    return PayPalAdapter(PayPalClient(db, creds(), transport=api.transport))


@pytest.fixture
def rail(paypal_rail):
    """Overrides conftest's mock rail, so `merchant` runs the saga on PayPal."""
    return paypal_rail


@pytest.fixture
def bought(quotes, make_mandate, authorize, merchant):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="FUR-LMP-01")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    assert decision.verdict == "ALLOW"
    return mandate, q, decision


def test_the_saga_reaches_fulfilled_on_paypal(api, bought, merchant):
    mandate, q, decision = bought
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)

    assert result.final_state == "FULFILLED"
    order = merchant.orders.get(result.order_id)
    assert order.rail == "paypal"
    assert order.rail_order_id in api.orders
    assert order.rail_payment_id in api.captures
    assert api.captures[order.rail_payment_id]["amount"]["value"] == to_value(q.total_minor, "USD")


def test_a_stockout_on_paypal_refunds_and_returns_the_budget(api, bought, merchant, gate):
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    before = gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints)

    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)

    states = [s["state"] for s in merchant.audit.list_steps(result.order_id)]
    assert states.index("PAYMENT_CAPTURED") < states.index("REFUND_ISSUED") \
        < states.index("BUDGET_RELEASED")
    assert gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints) \
        == before + q.total_minor
    (refund,) = api.refunds.values()
    assert refund["amount"]["value"] == to_value(q.total_minor, "USD")


def test_a_declined_wallet_rolls_the_saga_back_with_nothing_taken(api, bought, merchant):
    """
    On PayPal the order and its authorisation are one call, so a declined
    wallet fails create_intent. The saga used to let that exception out with
    the stock held.
    """
    from contracts.reason_codes import ReasonCode

    mandate, q, decision = bought
    api.vaulted[VAULT_ID] = False
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)

    assert result.final_state == "ROLLED_BACK"
    last = merchant.audit.list_steps(result.order_id)[-1]
    assert last["reason_code"] == ReasonCode.RAIL_AUTHORIZATION_FAILED.value
    assert "did not authorise" in last["detail"]
    assert api.captures == {}


def test_paypal_records_the_authorisation_before_the_capture(api, bought, merchant):
    mandate, q, decision = bought
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)
    steps = merchant.audit.list_steps(result.order_id)
    authorized = next(s for s in steps if s["state"] == "PAYMENT_AUTHORIZED")
    assert authorized["ref"] in api.authorizations
    assert [s["state"] for s in steps].index("PAYMENT_AUTHORIZED") \
        < [s["state"] for s in steps].index("PAYMENT_CAPTURED")


def test_a_pending_paypal_capture_waits_instead_of_shipping(api, bought, merchant):
    mandate, q, decision = bought
    api.capture_status = "PENDING"
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                               decision_id=decision.decision_id)
    assert result.final_state == "PAYMENT_PENDING"
    assert "FULFILLED" not in [s["state"] for s in merchant.audit.list_steps(result.order_id)]
