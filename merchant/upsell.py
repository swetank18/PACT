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
from collections import OrderedDict
from dataclasses import dataclass

from contracts.schemas import Addon, Headroom, Quote
from merchant.catalog import ADDON_REASON, BY_SKU, COMPLEMENTS, Inventory

log = logging.getLogger("pact.upsell")

MAX_OFFERS = 3

#: How many quotes' offers to remember, so `record_requote` can tell an
#: accepted offer from a basket the buyer assembled themselves.
#:
#: Bounded on purpose. The two-hour soak found the one memory leak in this
#: project — `rails/mock_upi/adapter.py` keeping every intent it ever created,
#: at 663 bytes a purchase — and the lesson is that a map keyed by something
#: that grows with traffic needs a cap written at the same time as the map, not
#: after a soak finds it. A quote_id and a handful of SKUs is well under 200
#: bytes, so this ceiling is a few hundred kilobytes, reached and then flat.
OFFER_MEMORY = 2048


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
        #: quote_id -> the SKUs this engine offered against it. Insertion
        #: ordered so the cap evicts the oldest, and popped on use so the same
        #: quote cannot be presented twice.
        self._offered: OrderedDict[str, frozenset[str]] = OrderedDict()

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
            self._remember(quote.quote_id, offers)

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
            self._remember(quote.quote_id, offers)
        return offers, 0

    # ------------------------------------------------------------ accepted ---

    def _remember(self, quote_id: str, offers: list[Addon]) -> None:
        """Caller holds the lock."""
        if not offers:
            return
        self._offered[quote_id] = frozenset(a.sku for a in offers)
        self._offered.move_to_end(quote_id)
        while len(self._offered) > OFFER_MEMORY:
            self._offered.popitem(last=False)

    def record_acceptance(self, count: int = 1) -> None:
        with self._lock:
            self.counters.offers_accepted += count

    def record_requote(self, previous: Quote, current: Quote) -> int:
        """
        Count the addons a re-quote actually accepted. Returns how many.

        Accepting an addon is not an endpoint. The console adds one by
        re-quoting the whole basket with the extra SKU on it — deliberately,
        because it does no arithmetic that reaches a payload — so for the life
        of this project `record_acceptance` had exactly one caller, the
        rollback recovery path, and the attach tile read "0 of 7 offers
        accepted · 0%" on runs where an addon had plainly been accepted and was
        sitting in the order line on the next panel. It is the growth feature's
        own number and it was measuring the wrong thing.

        The client names the quote it came from and nothing else. What counts
        is decided here: a SKU must be new to the basket **and** one this engine
        actually offered against that quote. The entry is consumed on first use,
        so presenting the same quote twice adds nothing.
        """
        added = {line.sku for line in current.items} - {
            line.sku for line in previous.items
        }
        if not added:
            return 0

        with self._lock:
            offered = self._offered.pop(previous.quote_id, frozenset())
            accepted = len(added & offered)
            self.counters.offers_accepted += accepted

        return accepted

    def reset(self) -> None:
        with self._lock:
            self.counters.reset()
            self._offered.clear()
