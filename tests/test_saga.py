"""
The graceful failure the brief asks for, and the failure of the compensation
itself, which is the one worth volunteering unprompted.

Anyone can handle the failure they planned for. Handling the failure of your own
refund is what payments engineering actually looks like, so
`test_a_failed_refund_parks_rather_than_losing_money_quietly` is the test to
point at when someone asks what happens if the refund fails.
"""

from __future__ import annotations

import pytest

from contracts.reason_codes import ReasonCode, verdict_for
from contracts.schemas import QuoteItemRequest
from rails.razorpay.client import idempotency_key


@pytest.fixture
def bought(quotes, make_mandate, authorize, merchant):
    """A mandate, a quote, an ALLOW, and the saga ready to run."""
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="FUR-LMP-01")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    assert decision.verdict == "ALLOW"
    return mandate, q, decision


# ------------------------------------------------------------- happy path ---


def test_the_forward_path_reaches_fulfilled(bought, merchant):
    mandate, q, decision = bought
    result = merchant.saga.run(quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id)

    assert result.final_state == "FULFILLED"
    states = [s["state"] for s in merchant.audit.list_steps(result.order_id)]
    assert states == [
        "QUOTED", "RESERVED_STOCK", "GATE_ALLOWED", "PAYMENT_CAPTURED", "FULFILLED",
    ]


def test_a_captured_payment_commits_the_reservation(bought, merchant, gate):
    mandate, q, decision = bought
    merchant.saga.run(quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id)

    with gate.db.read_tx() as conn:
        state = conn.execute(
            "SELECT state FROM reservations WHERE decision_id = ?", (decision.decision_id,)
        ).fetchone()["state"]
    assert state == "COMMITTED"


# ----------------------------------------------------------- the rollback ---


def test_a_stockout_after_capture_refunds_and_returns_the_budget(bought, merchant, gate):
    """
    The brief's graceful failure. Capture succeeds, fulfilment does not, and
    every compensation runs in reverse with a row for each.
    """
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")

    before = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    after = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)

    states = [s["state"] for s in merchant.audit.list_steps(result.order_id)]
    assert "PAYMENT_CAPTURED" in states, "the money must move before the interesting failure"
    assert "FULFILMENT" in states
    assert "ROLLING_BACK" in states
    assert "REFUND_ISSUED" in states
    assert "BUDGET_RELEASED" in states

    # The compensations run in reverse: refund before budget release.
    assert states.index("REFUND_ISSUED") < states.index("BUDGET_RELEASED")

    assert after == before + q.total_paise, "the headroom must come back"


def test_the_refund_carries_the_rails_reference(bought, merchant):
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    refund = next(
        s for s in merchant.audit.list_steps(result.order_id) if s["state"] == "REFUND_ISSUED"
    )
    assert refund["ref"], "a refund with no reference is not auditable"


def test_an_alternative_is_offered_and_it_fits_the_remaining_headroom(
    bought, merchant, gate
):
    """Step five of the rollback, and the growth move. A failure becomes an
    offer rather than a lost sale."""
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    assert result.final_state == "ALTERNATIVE_OFFERED"
    order = merchant.orders.get(result.order_id)
    assert order.alternative is not None

    headroom = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert order.alternative.price_paise <= headroom.headroom_paise
    assert order.alternative.category in headroom.categories_allowed


def test_the_replacement_quote_goes_back_through_the_gate(bought, merchant, authorize, gate):
    """
    The merchant holds no key and must not be able to spend on the buyer's
    behalf. Accepting an alternative yields a quote the buyer signs — it does
    not complete a purchase.
    """
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    offered = merchant.saga.offer_replacement_quote(result.order_id)
    assert offered is not None
    replacement, original = offered
    assert original.order_id == result.order_id

    # Nothing has been bought yet.
    assert merchant.orders.get(result.order_id).state == "ALTERNATIVE_OFFERED"

    # The buyer authorises it like any other purchase.
    replacement_decision = authorize(mandate, replacement)
    assert replacement_decision.verdict == "ALLOW"

    recovered = merchant.saga.run(
        quote=replacement,
        mandate_id=mandate.mandate_id,
        decision_id=replacement_decision.decision_id,
    )
    merchant.saga.link_recovery(recovered.order_id, result.order_id)

    assert merchant.orders.get(recovered.order_id).state == "RECOVERED"
    assert merchant.orders.get(recovered.order_id).recovered_from == result.order_id


def test_the_refunded_order_is_not_counted_as_revenue(bought, merchant, authorize):
    """
    The most flattering possible bug: counting an order that was captured and
    then refunded. It would inflate GMV by exactly the amount we gave back.
    """
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    stats = merchant.stats.compute()
    assert stats.gmv_paise == 0, "a refunded order is not GMV"
    assert stats.orders == 0

    replacement, original = merchant.saga.offer_replacement_quote(result.order_id)
    d2 = authorize(mandate, replacement)
    rec = merchant.saga.run(
        quote=replacement, mandate_id=mandate.mandate_id, decision_id=d2.decision_id
    )
    merchant.saga.link_recovery(rec.order_id, original.order_id)

    stats = merchant.stats.compute()
    assert stats.orders == 1
    assert stats.gmv_paise == replacement.total_paise
    assert stats.recovered_paise == replacement.total_paise
    assert stats.recovered_orders == 1


# ------------------------------------- the compensation itself failing ------


def test_a_failed_refund_parks_rather_than_losing_money_quietly(bought, merchant, rail):
    """
    chs_05. The one to volunteer unprompted.

    Three attempts, then NEEDS_ATTENTION. Never a silent swallow, and never a
    budget release that pretends the money came back.
    """
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    rail.failures.refund_fails = True

    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )

    assert result.final_state == "NEEDS_ATTENTION"
    steps = merchant.audit.list_steps(result.order_id)
    states = [s["state"] for s in steps]

    assert "NEEDS_ATTENTION" in states
    assert "BUDGET_RELEASED" not in states, (
        "releasing the budget after a failed refund would claim money came back "
        "when it did not"
    )
    # Every attempt is on the record.
    attempts = [s for s in steps if s["action"] == "rail.refund" and s["outcome"] == "FAIL"]
    assert len(attempts) >= 3, "all three attempts must appear in the audit trail"


def test_needs_attention_is_surfaced_in_the_stats(bought, merchant, rail):
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")
    rail.failures.refund_fails = True
    merchant.saga.run(quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id)

    assert merchant.stats.compute().needs_attention == 1


def test_a_failed_capture_rolls_back_without_charging(bought, merchant, rail, gate):
    mandate, q, decision = bought
    rail.failures.capture_fails = True

    before = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)
    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    after = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)

    assert result.final_state == "ROLLED_BACK"
    assert after == before + q.total_paise
    # Stock must go back too, or a failed capture quietly destroys inventory.
    assert merchant.inventory.level("FUR-LMP-01") == 20


# ----------------------------------------------- when the gate does not answer ---


def test_an_unreachable_gate_is_not_reported_as_an_invalid_token():
    """
    Found by scripts/load.py at 32 concurrent buyers.

    HttpGateClient.redeem catches any transport failure and refuses the order,
    which is right — no order without a redeemed token. It reported
    TOKEN_INVALID, which is not right. That code means "that settlement token is
    not valid" and reads as a forgery, so a burst of them in the audit trail
    after a load spike sends someone hunting an attacker who does not exist.

    Failing closed and telling the truth about why are not in tension.
    """
    from merchant.gate_client import HttpGateClient

    # A port nothing is listening on. Short timeout so the test is not slow.
    client = HttpGateClient(base_url="http://127.0.0.1:9", timeout=0.25)

    ok, code, decision_id = client.redeem("stl_anything", 100)

    assert ok is False, "an unreachable gate must never approve a settlement"
    assert code == ReasonCode.GATE_UNAVAILABLE.value
    assert decision_id is None
    # And it still blocks, because a gate that cannot be reached cannot approve.
    assert verdict_for(ReasonCode.GATE_UNAVAILABLE) == "BLOCK"


# --------------------------------------------------- the trail is machine readable ---


def test_the_audit_trail_carries_reason_codes_not_only_prose(bought, merchant, rail):
    """
    A rollback must say why in the contract's vocabulary, not only in English.

    `detail` is prose and may be reworded at any time — it is presentation. The
    reason code is what Lane B asserts on and what Lane C colours by. For three
    releases the saga wrote a sentence into `detail` and no code at all, so the
    three settlement-side codes existed in the enum and were emitted by nothing:
    a console branch that had never rendered and an assertion that could never
    fire.
    """
    mandate, q, decision = bought
    rail.failures.capture_fails = True

    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    steps = merchant.audit.list_steps(result.order_id)

    failed = [s for s in steps if s["outcome"] == "FAIL"]
    assert failed, "a failed capture produced no failing step"
    assert all(s["reason_code"] for s in failed), (
        "a failing step with no reason code: " + repr(failed)
    )
    assert ReasonCode.RAIL_CAPTURE_FAILED.value in {s["reason_code"] for s in failed}

    # And the successful ones stay quiet. A code on every row is noise, and it
    # would make "has a reason code" useless as a filter.
    assert all(s["reason_code"] is None for s in steps if s["outcome"] == "OK")


def test_a_stockout_rollback_names_the_stockout(bought, merchant):
    mandate, q, decision = bought
    merchant.inventory.force_stockout("FUR-LMP-01")

    result = merchant.saga.run(
        quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id
    )
    codes = {
        s["reason_code"] for s in merchant.audit.list_steps(result.order_id)
        if s["reason_code"]
    }
    assert ReasonCode.STOCK_UNAVAILABLE.value in codes
    assert ReasonCode.SAGA_ROLLED_BACK.value in codes


# ------------------------------------------------------------ idempotency ---


def test_a_double_capture_charges_once(merchant, rail):
    """
    The rail has no idempotency header, so this is entirely on our table.
    Calling twice must not charge twice, and we must be able to demo it.
    """
    intent = rail.create_intent(100_000, ref="ord_x", idem_key="k1")
    first = rail.capture(intent.raw["payment_id"], 100_000, "k1")
    second = rail.capture(intent.raw["payment_id"], 100_000, "k1")

    assert first.ok and second.ok
    assert second.replayed, "the second capture must be served from the idempotency record"
    assert first.ref == second.ref


def test_a_double_refund_refunds_once(rail):
    intent = rail.create_intent(100_000, ref="ord_y", idem_key="k2")
    rail.capture(intent.raw["payment_id"], 100_000, "k2")

    first = rail.refund(intent.raw["payment_id"], 100_000, "k3")
    second = rail.refund(intent.raw["payment_id"], 100_000, "k3")

    assert first.ref == second.ref
    assert second.replayed
    assert rail.status(intent.intent_id).amount_refunded_paise == 100_000


def test_the_idempotency_key_is_the_contracts_formula():
    a = idempotency_key("ord_1", 249900, 1)
    assert a == idempotency_key("ord_1", 249900, 1)
    assert a != idempotency_key("ord_1", 249900, 2)
    assert a != idempotency_key("ord_2", 249900, 1)
    assert a != idempotency_key("ord_1", 249901, 1)
    assert len(a) == 64  # sha256 hex


def test_a_settlement_token_can_only_be_spent_once(bought, gate):
    """One ALLOW must not pay for two orders."""
    mandate, q, decision = bought

    ok, code, _ = gate.redeem_token(decision.settlement_token, amount_paise=q.total_paise)
    assert ok and code is ReasonCode.OK

    ok2, code2, _ = gate.redeem_token(decision.settlement_token, amount_paise=q.total_paise)
    assert not ok2
    assert code2 is ReasonCode.TOKEN_ALREADY_USED


def test_a_token_cannot_be_redeemed_for_a_different_amount(bought, gate):
    mandate, q, decision = bought
    ok, code, _ = gate.redeem_token(decision.settlement_token, amount_paise=q.total_paise + 1)
    assert not ok
    assert code is ReasonCode.QUOTE_AMOUNT_MISMATCH


def test_an_unknown_token_is_refused(gate):
    ok, code, _ = gate.redeem_token("stl_NOTREAL", amount_paise=1)
    assert not ok
    assert code is ReasonCode.TOKEN_INVALID


# ------------------------------------------------------------- restocking ---


def test_restock_refills_stock_without_disarming_a_forced_stockout(merchant):
    """
    `restock_all` exists for `scripts/soak.py`, which buys for hours against a
    catalog holding forty of one SKU.

    It is deliberately not `reset`. Reset also clears the armed stockout, and
    the soak restocks every five seconds — so if these were the same call, a
    soak running while somebody pressed `s` on the console would silently
    disarm beat 5 between the press and the purchase.
    """
    merchant.inventory.reserve("FUR-LMP-01", 20)
    merchant.inventory.force_stockout("FUR-LMP-01")
    assert merchant.inventory.level("FUR-LMP-01") == 0

    levels = merchant.inventory.restock_all()

    assert levels["FUR-LMP-01"] == 20
    assert merchant.inventory.level("FUR-LMP-01") == 20
    # Still armed: the next fulfilment of that SKU must still fail.
    assert merchant.inventory.consume_forced("FUR-LMP-01") is True
