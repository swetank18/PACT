"""
Webhooks: signature, duplicates, and out-of-order delivery.

An unverified webhook endpoint in a payments project is the first thing anyone
looks for, so the signature tests come first and the bad-signature case is
asserted to *raise*, not to be quietly ignored.
"""

from __future__ import annotations

import json

import pytest

from contracts.schemas import QuoteItemRequest, utcnow
from rails.razorpay.webhooks import WebhookProcessor


def envelope(event: str, payment_id: str, status: str = "captured") -> bytes:
    """The shape API_NOTES recorded from the live docs."""
    return json.dumps(
        {
            "entity": "event",
            "account_id": "acc_TEST",
            "event": event,
            "contains": ["payment"],
            "payload": {"payment": {"entity": {"id": payment_id, "status": status}}},
            "created_at": 1724990000,
        }
    ).encode()


@pytest.fixture
def processor(db, rail):
    applied: list[tuple[str, str]] = []
    p = WebhookProcessor(db, rail, on_applied=lambda oid, st, _e: applied.append((oid, st)))
    p.applied_log = applied  # type: ignore[attr-defined]
    return p


# ------------------------------------------------------------- signature ---


def test_a_bad_signature_is_rejected(processor):
    body = envelope("payment.captured", "pay_x")
    with pytest.raises(PermissionError):
        processor.process(body, "not-the-right-signature")


def test_an_absent_signature_is_rejected(processor):
    with pytest.raises(PermissionError):
        processor.process(envelope("payment.captured", "pay_x"), "")


def test_a_valid_signature_is_accepted(processor, rail):
    body = envelope("payment.captured", "pay_x")
    outcome = processor.process(body, rail.sign_callback(body))
    assert outcome.duplicate is False


def test_the_signature_is_over_the_raw_bytes_not_the_reparsed_json(processor, rail):
    """
    Re-serialising before verifying is the standard way this check silently
    stops working: the bytes change (key order, whitespace) and the HMAC no
    longer matches. This asserts we sign what was actually sent.
    """
    body = envelope("payment.captured", "pay_raw")
    signature = rail.sign_callback(body)

    reserialised = json.dumps(json.loads(body), sort_keys=True, indent=2).encode()
    assert reserialised != body
    assert processor.verify(body, signature) is True
    assert processor.verify(reserialised, signature) is False


# ------------------------------------------------------------- duplicates ---


def test_a_duplicate_event_is_a_no_op(processor, rail):
    body = envelope("payment.captured", "pay_dup")
    sig = rail.sign_callback(body)

    first = processor.process(body, sig)
    second = processor.process(body, sig)
    third = processor.process(body, sig)

    assert first.duplicate is False
    assert second.duplicate is True
    assert third.duplicate is True


def test_three_duplicates_write_one_row(processor, rail, db):
    body = envelope("payment.captured", "pay_three")
    sig = rail.sign_callback(body)
    for _ in range(3):
        processor.process(body, sig)

    with db.read_tx() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM rail_events WHERE payment_id = ?", ("pay_three",)
        ).fetchone()["n"]
    assert n == 1


# --------------------------------------------------------------- ordering ---


def test_out_of_order_delivery_converges(processor, rail, merchant, quotes, make_mandate, authorize):
    """
    `payment.authorized` arriving after `payment.captured` must not walk the
    order backwards. Transitions apply forwards only.
    """
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    order = merchant.orders.get(result.order_id)
    assert order.state == "FULFILLED"

    late = envelope("payment.authorized", order.rail_payment_id, status="authorized")
    outcome = processor.process(late, rail.sign_callback(late))

    assert outcome.applied is False
    assert merchant.orders.get(result.order_id).state == "FULFILLED"


def test_a_webhook_for_an_order_we_have_not_written_yet_is_survivable(processor, rail):
    """
    The webhook can beat our own database write. Dropping it is safe precisely
    because the reconciliation poller exists — the webhook is never the only
    path to the truth.
    """
    body = envelope("payment.captured", "pay_unknown_yet")
    outcome = processor.process(body, rail.sign_callback(body))
    assert outcome.applied is False


def test_an_unhandled_event_is_acknowledged_not_retried(processor, rail):
    """
    Returning a non-2xx for an event we do not care about would make the rail
    retry it forever.
    """
    body = envelope("payment.downtime.started", "pay_x")
    outcome = processor.process(body, rail.sign_callback(body))
    assert outcome.applied is False
    assert "not a handled event" in outcome.detail


# --------------------------------------------------------- reconciliation ---


def test_reconciliation_resolves_an_order_no_webhook_ever_covered(
    merchant, quotes, make_mandate, authorize, db
):
    """chs_02. Never trust the webhook as the only path."""
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    # Rewind the order to the state it would be in if the capture confirmation
    # never arrived, and age it past the reconciliation threshold.
    with db.immediate_tx() as conn:
        conn.execute(
            "UPDATE orders SET state = 'GATE_ALLOWED', updated_at = '2000-01-01T00:00:00Z' "
            "WHERE order_id = ?",
            (result.order_id,),
        )

    assert merchant.saga.reconcile() == 1
    assert merchant.orders.get(result.order_id).state == "PAYMENT_CAPTURED"


def test_reconciliation_is_idempotent(merchant, quotes, make_mandate, authorize, db):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    with db.immediate_tx() as conn:
        conn.execute(
            "UPDATE orders SET state = 'GATE_ALLOWED', updated_at = '2000-01-01T00:00:00Z' "
            "WHERE order_id = ?",
            (result.order_id,),
        )

    assert merchant.saga.reconcile() == 1
    # Second pass: the order is no longer pending, so nothing to resolve.
    assert merchant.saga.reconcile() == 0


def _age(db, order_id: str, state: str, when: str = "2000-01-01T00:00:00Z") -> None:
    with db.immediate_tx() as conn:
        conn.execute(
            "UPDATE orders SET state = ?, updated_at = ? WHERE order_id = ?",
            (state, when, order_id),
        )


def test_an_order_captured_but_never_fulfilled_is_parked_rather_than_polled_forever(
    merchant, quotes, make_mandate, authorize, db
):
    """
    The failure the reconciler exists for, one step further on.

    It resolves exactly one case: the rail captured and the merchant never
    recorded it. Everything else it found, it looked at and left. An order the
    process died on between capture and fulfilment therefore sat in
    PAYMENT_CAPTURED for good — the rail says captured, the order already says
    captured, so the branch never fires — and it came back on every pass, every
    thirty seconds, forever, doing nothing.

    Money taken, nothing dispatched, and nothing anywhere saying so. A failed
    refund gets parked in NEEDS_ATTENTION and raised in the console; this is the
    same situation and it was silent.
    """
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    _age(db, result.order_id, "PAYMENT_CAPTURED")

    assert merchant.saga.reconcile() == 1
    order = merchant.orders.get(result.order_id)
    assert order.state == "NEEDS_ATTENTION", (
        "an order captured and never fulfilled has to end up somewhere a human "
        f"looks, not in {order.state} on a poller"
    )

    # And it leaves the loop: NEEDS_ATTENTION is terminal for the reconciler.
    _age(db, result.order_id, "NEEDS_ATTENTION")
    assert merchant.saga.reconcile() == 0


def test_an_order_that_never_captured_is_parked_too_and_says_so(
    merchant, quotes, make_mandate, authorize, db, monkeypatch
):
    """
    The other half. Nothing moved, the reservation has been swept by now, and
    the order can never complete — so it is parked with what the rail actually
    reported rather than left in GATE_ALLOWED being re-polled forever.
    """
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    class NotCaptured:
        status = "created"
        payment_id = None

    monkeypatch.setattr(merchant.saga.rail, "status", lambda _ref: NotCaptured())
    _age(db, result.order_id, "GATE_ALLOWED")

    assert merchant.saga.reconcile() == 1
    assert merchant.orders.get(result.order_id).state == "NEEDS_ATTENTION"

    steps = merchant.audit.list_steps(result.order_id)
    parked = [s for s in steps if s["state"] == "NEEDS_ATTENTION"]
    assert parked, "parking an order must leave a step saying why"
    assert "created" in parked[-1]["detail"], (
        "the step has to record what the rail actually said, or a human has "
        "nothing to go on"
    )
