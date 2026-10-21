"""
The two payment outcomes the saga used to have no answer for.

**The payment never opens.** On PayPal the order and its authorisation are one
call, so a declined wallet fails `create_intent`, not `capture`. The saga called
it bare: the exception left with the stock held and the reservation alive until
the sweeper found it. Nothing was captured, so the right answer is a clean
rollback with no refund.

**The money is taken but has not arrived.** A PayPal capture can come back
PENDING. The budget is committed — the payer's money is committed, and
releasing it would risk an overspend if the capture lands — and fulfilment
waits, because shipping on an uncleared payment is what PayPal says not to do.
The reconciler finishes the order when the capture completes, or rolls it back
when it is refused.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.reason_codes import ReasonCode
from contracts.schemas import QuoteItemRequest

SKU = "FUR-LMP-01"


@pytest.fixture
def bought(quotes, make_mandate, authorize):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku=SKU)], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    assert decision.verdict == "ALLOW"
    return mandate, q, decision


def _run(merchant, bought):
    mandate, q, decision = bought
    return merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                             decision_id=decision.decision_id)


def _states(merchant, order_id):
    return [s["state"] for s in merchant.audit.list_steps(order_id)]


def _reservation(gate, decision_id):
    with gate.db.read_tx() as conn:
        return conn.execute("SELECT state FROM reservations WHERE decision_id = ?",
                            (decision_id,)).fetchone()["state"]


def _age(db, order_id, seconds: int = 60):
    """Past the reconciler's 30 seconds, well short of the 300 that parks an
    order as stuck. A real age, not the year 2000: see test_reconcile_clock."""
    when = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0)
    with db.immediate_tx() as conn:
        conn.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?",
                     (when.isoformat().replace("+00:00", "Z"), order_id))


# ------------------------------------------------- the payment never opens ---


def test_a_declined_payment_rolls_back_with_nothing_to_refund(merchant, rail, gate, bought):
    mandate, q, decision = bought
    stock_before = merchant.inventory.level(SKU)
    headroom_before = gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints)
    rail.failures.create_fails = True

    result = _run(merchant, bought)

    assert result.final_state == "ROLLED_BACK"
    last = merchant.audit.list_steps(result.order_id)[-1]
    assert last["action"] == "rail.create_intent"
    assert last["reason_code"] == ReasonCode.RAIL_AUTHORIZATION_FAILED.value
    assert "REFUND_ISSUED" not in _states(merchant, result.order_id)
    assert merchant.inventory.level(SKU) == stock_before, "the stock goes back"
    # `headroom_before` was read after the ALLOW reserved the amount; released,
    # the whole of it comes back.
    assert gate.ledger.headroom_minor(mandate.mandate_id, mandate.constraints) \
        == headroom_before + q.total_minor
    assert _reservation(gate, decision.decision_id) == "RELEASED"


# ---------------------------------------------- the money has not arrived ---


@pytest.fixture
def pending(merchant, rail, gate, bought):
    rail.failures.capture_pending = True
    result = _run(merchant, bought)
    return result, merchant.orders.get(result.order_id)


def test_a_pending_capture_commits_the_budget_and_does_not_ship(merchant, gate, bought, pending):
    mandate, q, decision = bought
    result, order = pending

    assert result.final_state == "PAYMENT_PENDING"
    states = _states(merchant, result.order_id)
    assert states[-1] == "PAYMENT_PENDING" and "FULFILLED" not in states
    assert merchant.audit.list_steps(result.order_id)[-1]["outcome"] == "PENDING"
    assert _reservation(gate, decision.decision_id) == "COMMITTED"


def test_a_pending_capture_that_lands_is_fulfilled_by_the_reconciler(merchant, rail, db, pending):
    result, order = pending
    rail.settle_pending(order.rail_order_id, completed=True)
    _age(db, result.order_id)

    assert merchant.saga.reconcile() == 1
    states = _states(merchant, result.order_id)
    assert states[-3:] == ["PAYMENT_PENDING", "PAYMENT_CAPTURED", "FULFILLED"]


def test_a_pending_capture_that_is_refused_gives_everything_back(
    merchant, rail, gate, db, bought, pending
):
    mandate, q, decision = bought
    result, order = pending
    stock_while_pending = merchant.inventory.level(SKU)
    rail.settle_pending(order.rail_order_id, completed=False)
    _age(db, result.order_id)

    assert merchant.saga.reconcile() == 1
    last = merchant.audit.list_steps(result.order_id)[-1]
    assert last["state"] == "ROLLED_BACK"
    assert last["reason_code"] == ReasonCode.RAIL_CAPTURE_FAILED.value
    assert "REFUND_ISSUED" not in _states(merchant, result.order_id), "nothing was taken"
    assert merchant.inventory.level(SKU) == stock_while_pending + 1
    assert _reservation(gate, decision.decision_id) == "RELEASED"


def test_a_capture_still_pending_is_left_alone(merchant, db, pending):
    result, _ = pending
    _age(db, result.order_id)

    assert merchant.saga.reconcile() == 0
    assert merchant.orders.get(result.order_id).state == "PAYMENT_PENDING"
