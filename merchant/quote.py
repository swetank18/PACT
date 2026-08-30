"""
The quote engine. Deterministic, integer paise, server side.

The single most defensible thing in the merchant plane. Every price, tax and
shipping figure is computed here. The model never does arithmetic that reaches a
payload — it quotes what this returns, and the gate refuses any payment whose
amount does not equal the quote it references.

That is what makes a hallucinated price structurally impossible rather than
unlikely, and `QUOTE_AMOUNT_MISMATCH` is the reason code that proves it.

Determinism is a property we test: `tests/test_quote.py` runs the same input a
hundred times and asserts the totals are identical. The only non-deterministic
fields are the id and the timestamps, which is why they are excluded there.
"""

from __future__ import annotations

import json
from datetime import timedelta

from contracts.ids import new_id
from contracts.money import Paise, gst
from contracts.schemas import (
    Headroom,
    HeadroomFit,
    LineItem,
    Quote,
    QuoteItemRequest,
    parse_rfc3339,
    utcnow,
)
from core.db import Database
from merchant.catalog import BY_SKU

#: 18% GST, in basis points so the whole calculation stays integer.
GST_BPS = 1800
QUOTE_TTL_SECONDS = 300
FREE_SHIPPING_OVER_PAISE = 100_000
SHIPPING_PAISE = 9_900


class QuoteEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def price(items: list[QuoteItemRequest]) -> tuple[list[LineItem], Paise, Paise, Paise, Paise]:
        """
        Pure pricing. Returns (lines, subtotal, tax, shipping, total).

        Separated from `build` so callers can ask what something *would* cost
        without persisting a quote. The upsell engine needs exactly this: an
        addon's effect on the total is not its sticker price, because tax
        applies to it and adding it can cross the free-shipping threshold.
        Adding raw prices understates the real total and is how a filter ends up
        offering something the gate then refuses.
        """
        lines: list[LineItem] = []
        for req in items:
            product = BY_SKU.get(req.sku)
            if product is None:
                raise KeyError(f"unknown sku: {req.sku}")
            lines.append(
                LineItem(
                    sku=product.sku,
                    name=product.name,
                    qty=req.qty,
                    unit_paise=product.price_paise,
                    line_total_paise=product.price_paise * req.qty,
                    category=product.category,
                )
            )
        subtotal: Paise = sum(line.line_total_paise for line in lines)
        tax: Paise = gst(subtotal, GST_BPS)
        shipping: Paise = 0 if subtotal >= FREE_SHIPPING_OVER_PAISE else SHIPPING_PAISE
        return lines, subtotal, tax, shipping, subtotal + tax + shipping

    def total_with_addon(self, quote: Quote, addon_sku: str) -> Paise:
        """The true total if this addon joins the quote. Never quote.total + price."""
        items = [QuoteItemRequest(sku=l.sku, qty=l.qty) for l in quote.items]
        items.append(QuoteItemRequest(sku=addon_sku, qty=1))
        return self.price(items)[4]

    def build(
        self,
        items: list[QuoteItemRequest],
        *,
        mandate_id: str | None = None,
        headroom: Headroom | None = None,
    ) -> Quote:
        # A quote for a SKU we do not sell raises rather than silently dropping
        # the line, which would produce a total the caller cannot explain.
        lines, subtotal, tax, shipping, total = self.price(items)

        now = utcnow()
        expires = (
            parse_rfc3339(now) + timedelta(seconds=QUOTE_TTL_SECONDS)
        ).isoformat().replace("+00:00", "Z")

        fit = None
        if headroom is not None:
            fit = HeadroomFit(
                fits=(
                    total <= headroom.headroom_paise
                    and total <= headroom.max_per_txn_paise
                    and headroom.payments_remaining > 0
                    and headroom.merchant_in_scope
                ),
                headroom_paise=headroom.headroom_paise,
                headroom_after_paise=max(0, headroom.headroom_paise - total),
                categories_ok=all(
                    line.category in headroom.categories_allowed for line in lines
                ),
            )

        quote = Quote(
            quote_id=new_id("qte"),
            items=lines,
            subtotal_paise=subtotal,
            tax_paise=tax,
            shipping_paise=shipping,
            total_paise=total,
            expires_at=expires,
            headroom_fit=fit,
        )
        self._persist(quote, mandate_id, now)
        return quote

    def _persist(self, quote: Quote, mandate_id: str | None, now: str) -> None:
        """
        The gate reads this table for its quote binding check. Persisting here
        rather than handing the quote to the gate in the request is deliberate:
        an agent that could supply its own quote could supply its own total,
        and the whole check would be circular.
        """
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quotes
                    (quote_id, mandate_id, total_paise, body_json, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.quote_id,
                    mandate_id,
                    quote.total_paise,
                    json.dumps(quote.model_dump(), separators=(",", ":")),
                    quote.expires_at,
                    now,
                ),
            )

    def get(self, quote_id: str) -> Quote | None:
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT body_json FROM quotes WHERE quote_id = ?", (quote_id,)
            ).fetchone()
        return Quote.model_validate(json.loads(row["body_json"])) if row else None

    def is_live(self, quote: Quote) -> bool:
        return parse_rfc3339(utcnow()) <= parse_rfc3339(quote.expires_at)
