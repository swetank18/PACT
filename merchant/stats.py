"""
The numbers on the merchant console's revenue strip.

Computed from the orders table and the upsell counters, never from a running
tally kept alongside. Lane B computes its metrics from the same source of truth,
so if the harness and this disagree, one of them has a bug rather than both
being "sort of right".

The load-bearing subtlety is what counts as revenue. An order that was captured
and then refunded is **not** GMV. Counting it would inflate the headline by
exactly the amount we just gave back, which is the most flattering possible bug
and therefore the one to be most careful about.
"""

from __future__ import annotations

from contracts.money import Paise
from contracts.schemas import MerchantStats
from core.db import Database
from merchant.upsell import UpsellEngine

#: States where the merchant actually keeps the money.
SETTLED_STATES = ("FULFILLED", "RECOVERED")


class StatsService:
    def __init__(self, db: Database, upsell: UpsellEngine) -> None:
        self.db = db
        self.upsell = upsell

    def compute(self) -> MerchantStats:
        with self.db.read_tx() as conn:
            settled = conn.execute(
                f"""
                SELECT COUNT(*) AS n, COALESCE(SUM(amount_paise), 0) AS gmv
                FROM orders WHERE state IN ({','.join('?' * len(SETTLED_STATES))})
                """,  # noqa: S608 - fixed tuple
                SETTLED_STATES,
            ).fetchone()

            recovered = conn.execute(
                f"""
                SELECT COUNT(*) AS n, COALESCE(SUM(amount_paise), 0) AS amt
                FROM orders
                WHERE state IN ({','.join('?' * len(SETTLED_STATES))})
                  AND recovered_from IS NOT NULL
                """,  # noqa: S608
                SETTLED_STATES,
            ).fetchone()

            attention = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE state = 'NEEDS_ATTENTION'"
            ).fetchone()

        orders = int(settled["n"])
        gmv: Paise = int(settled["gmv"])
        counters = self.upsell.counters

        return MerchantStats(
            gmv_paise=gmv,
            orders=orders,
            avg_order_value_paise=round(gmv / orders) if orders else 0,
            upsell_offers_made=counters.offers_made,
            upsell_offers_accepted=counters.offers_accepted,
            upsell_offers_filtered_by_headroom=counters.offers_filtered_by_headroom,
            upsell_attach_rate=(
                counters.offers_accepted / counters.offers_made if counters.offers_made else 0.0
            ),
            recovered_paise=int(recovered["amt"]),
            recovered_orders=int(recovered["n"]),
            needs_attention=int(attention["n"]),
        )
