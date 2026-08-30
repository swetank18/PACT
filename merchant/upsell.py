"""
Headroom aware upsell. This is the growth feature.

The merchant reads the buyer's remaining authority and only offers what will be
approved. Approval rate on offered upsells is therefore 100 percent **by
construction**, and `tests/test_addons.py` asserts it by putting every suggested
addon through the real gate.

The naive variant is kept here rather than in a separate script, because Lane B
needs both behind one flag: `--upsell=naive` is what a reasonable team builds,
and the number it produces — offers the gate then rejects — is what makes arm C
look like arm C.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from contracts.schemas import Addon, Headroom, Quote
from merchant.catalog import ADDON_REASON, BY_SKU, COMPLEMENTS, Inventory

log = logging.getLogger("pact.upsell")

MAX_OFFERS = 3


@dataclass
class UpsellCounters:
    """
    Instrumentation Lane B needs to compute attach rate and the naive
    baseline's rejection rate. Three counters, incremented at the moment the
    thing actually happens rather than inferred later.
    """

    offers_made: int = 0
    offers_accepted: int = 0
    offers_filtered_by_headroom: int = 0

    def reset(self) -> None:
        self.offers_made = 0
        self.offers_accepted = 0
        self.offers_filtered_by_headroom = 0


class UpsellEngine:
    def __init__(self, inventory: Inventory, quotes) -> None:  # noqa: ANN001
        self.inventory = inventory
        #: The quote engine, so the filter can ask what the basket would
        #: actually cost with the addon on it. See `suggest`.
        self.quotes = quotes
        self.counters = UpsellCounters()
        self._lock = threading.Lock()

    def _candidates(self, quote: Quote) -> list[str]:
        in_cart = {line.sku for line in quote.items}
        out: list[str] = []
        for line in quote.items:
            for sku in COMPLEMENTS.get(line.sku, ()):
                if sku not in in_cart and sku not in out:
                    out.append(sku)
        return out

    def suggest(self, quote: Quote, headroom: Headroom) -> tuple[list[Addon], int]:
        """
        Returns (offers, filtered_out).

        The conditions are checked **before** the offer is made rather than
        after it is refused, and they are checked against the total the gate
        will actually see.

        That last point is the one that bites. The addon rides on this quote, so
        the number that matters is the **recombined total** — quote plus addon,
        repriced — not `quote.total + addon.price`. Tax applies to the addon,
        and adding it can cross the free-shipping threshold, so adding raw
        prices understates the real figure. Understating it by even one paisa is
        enough to offer something that then fails CEILING_PER_TXN or
        CEILING_TOTAL, which would falsify the whole claim.
        """
        offers: list[Addon] = []
        filtered = 0

        for sku in self._candidates(quote):
            product = BY_SKU.get(sku)
            if product is None:
                continue

            # What the gate will be asked to approve, priced by the same engine
            # that will price the real quote.
            combined_total = self.quotes.total_with_addon(quote, sku)

            approvable = (
                headroom.merchant_in_scope
                and product.category in headroom.categories_allowed
                and combined_total <= headroom.headroom_paise
                and combined_total <= headroom.max_per_txn_paise
                and headroom.payments_remaining > 0
                and self.inventory.level(sku) > 0
            )
            if not approvable:
                filtered += 1
                continue

            offers.append(
                Addon(
                    sku=product.sku,
                    name=product.name,
                    category=product.category,
                    price_paise=product.price_paise,
                    reason=ADDON_REASON.get(product.sku),
                )
            )
            if len(offers) == MAX_OFFERS:
                break

        with self._lock:
            self.counters.offers_made += len(offers)
            self.counters.offers_filtered_by_headroom += filtered

        return offers, filtered

    def suggest_naive(self, quote: Quote) -> tuple[list[Addon], int]:
        """
        The baseline: offer the complements, in stock, and let the gate sort it
        out. No authority reading at all.

        This is not a straw man — it is what you build when the buyer's
        authority is not legible to you, which is the situation every merchant
        is in today. Arm C runs this, and the offers it makes that the gate then
        rejects are the number the pitch turns on.
        """
        offers = [
            Addon(
                sku=BY_SKU[sku].sku,
                name=BY_SKU[sku].name,
                category=BY_SKU[sku].category,
                price_paise=BY_SKU[sku].price_paise,
                reason=ADDON_REASON.get(sku),
            )
            for sku in self._candidates(quote)
            if sku in BY_SKU and self.inventory.level(sku) > 0
        ][:MAX_OFFERS]

        with self._lock:
            self.counters.offers_made += len(offers)
        return offers, 0

    def record_acceptance(self, count: int = 1) -> None:
        with self._lock:
            self.counters.offers_accepted += count

    def reset(self) -> None:
        with self._lock:
            self.counters.reset()
