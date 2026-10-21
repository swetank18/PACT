"""
The reconciler's clock, with timestamps from today.

`OrderStore._unsettled_older_than` compared stored RFC 3339 timestamps against
SQLite's `datetime('now', ...)`, which formats with a space where RFC 3339 has a
`T`. Compared as text, every order updated today sorted as newer than any
cutoff, so the reconciler resolved nothing until the UTC date changed. Every
existing test backdated its order to 2000-01-01, which is a different day, and
passed. These use ages a real order has.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.schemas import Order


def _rfc3339(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
def order_updated(merchant, db):
    def _make(seconds_ago: int, state: str = "GATE_ALLOWED") -> str:
        when = _rfc3339(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))
        order = Order(order_id=f"ord_{seconds_ago}s", quote_id="qte_x", mandate_id="mnd_x",
                      state=state, amount_minor=100, currency="INR", items_summary="1x thing",
                      rail="mock_upi", rail_order_id="mupi_x", at=when)
        merchant.orders.put(order)
        with db.immediate_tx() as conn:
            conn.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?",
                         (when, order.order_id))
        return order.order_id
    return _make


def test_an_order_updated_ten_minutes_ago_is_due_for_reconciliation(merchant, order_updated):
    order_id = order_updated(600)
    assert order_id in {o.order_id for o in merchant.orders.pending_for_reconciliation(30)}


def test_an_order_updated_ten_seconds_ago_is_not(merchant, order_updated):
    order_id = order_updated(10)
    assert order_id not in {o.order_id for o in merchant.orders.pending_for_reconciliation(30)}


def test_the_stuck_horizon_works_within_one_day(merchant, order_updated):
    old = order_updated(400)
    recent = order_updated(100)
    stuck = {o.order_id for o in merchant.orders.stuck(300)}
    assert old in stuck and recent not in stuck
