"""
DeskKit's catalog, and the complements table behind `suggest_addons`.

The recommender is deliberately dumb: a hand built co-occurrence table. The
claim we are making is that **every offer is provably approvable**, and a dumb
recommender proves that exactly as well as a clever one. Four hours of
collaborative filtering would not move the claim a millimetre.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from contracts.money import Paise

MERCHANT_NAME = "DeskKit"
MERCHANT_VPA = "deskkit@razorpay"
CURRENCY = "INR"


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    name: str
    category: str
    price_paise: Paise
    description: str
    tags: tuple[str, ...]
    initial_stock: int


CATALOG: tuple[Product, ...] = (
    Product("STA-NB-A5", "A5 ruled notebook, 5 pack", "stationery", 74900,
            "Soft-cover A5 notebooks, 96 pages, 5 per pack.",
            ("notebook", "notebooks", "paper", "supplies", "stationery", "restock", "office"), 40),
    Product("STA-PEN-12", "Gel pens, 12 pack", "stationery", 42000,
            "0.5mm gel pens, black, 12 per box.",
            ("pen", "pens", "supplies", "stationery", "restock", "office"), 60),
    Product("STA-STK-01", "Sticky notes, assorted", "stationery", 18500,
            "76mm sticky notes, four colours, 400 sheets.",
            ("sticky", "notes", "supplies", "stationery", "office"), 80),
    Product("CBL-USBC-2M", "USB-C to USB-C cable, 2m", "cables", 89900,
            "100W USB-C cable, braided, 2 metres.",
            ("cable", "cables", "usb", "usb-c", "usbc", "charging"), 25),
    Product("CBL-HDMI-2M", "HDMI 2.1 cable, 2m", "cables", 119900,
            "48Gbps HDMI 2.1, supports 4K120.",
            ("cable", "cables", "hdmi", "monitor", "display"), 18),
    Product("CBL-HUB-7P", "7 port USB-C hub", "cables", 349900,
            "HDMI, ethernet, SD, 3x USB-A, 100W passthrough.",
            ("hub", "dock", "usb", "usb-c", "usbc", "cables", "desk"), 12),
    Product("FUR-LMP-01", "Adjustable desk lamp", "office_furniture", 229900,
            "Warm-to-cool LED, 5 brightness steps, clamp mount.",
            ("lamp", "lamps", "light", "desk", "furniture"), 20),
    Product("FUR-MON-ARM", "Single monitor arm", "office_furniture", 419900,
            "Gas spring arm, VESA 75/100, up to 9kg.",
            ("monitor", "arm", "desk", "furniture", "setup"), 9),
    Product("FUR-CHR-ERG", "Ergonomic task chair", "office_furniture", 1249900,
            "Mesh back, adjustable lumbar and armrests.",
            ("chair", "seat", "desk", "furniture", "setup", "hire"), 4),
    Product("FUR-MAT-DSK", "Desk mat, felt", "office_furniture", 99900,
            "900x400mm recycled felt desk mat.",
            ("mat", "desk", "furniture", "setup"), 35),
)

BY_SKU: dict[str, Product] = {p.sku: p for p in CATALOG}
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(p.category for p in CATALOG))

#: Hand built. See the module docstring for why this is the right amount of
#: effort. Order matters: the first entry that fits is offered first.
COMPLEMENTS: dict[str, tuple[str, ...]] = {
    "STA-NB-A5": ("STA-PEN-12", "STA-STK-01", "FUR-LMP-01"),
    "STA-PEN-12": ("STA-NB-A5", "STA-STK-01"),
    "STA-STK-01": ("STA-PEN-12", "STA-NB-A5"),
    "CBL-USBC-2M": ("CBL-HUB-7P", "CBL-HDMI-2M", "FUR-MAT-DSK"),
    "CBL-HDMI-2M": ("CBL-HUB-7P", "FUR-MON-ARM"),
    "CBL-HUB-7P": ("CBL-HDMI-2M", "CBL-USBC-2M"),
    "FUR-LMP-01": ("FUR-MAT-DSK", "STA-STK-01"),
    "FUR-MON-ARM": ("CBL-HDMI-2M", "FUR-MAT-DSK"),
    "FUR-CHR-ERG": ("FUR-MAT-DSK", "FUR-LMP-01"),
    "FUR-MAT-DSK": ("FUR-LMP-01", "STA-STK-01"),
}

ADDON_REASON: dict[str, str] = {
    "STA-PEN-12": "Pens run out before notebooks do",
    "STA-STK-01": "Goes with the notebooks",
    "CBL-HUB-7P": "One cable is rarely enough for a desk",
    "CBL-HDMI-2M": "Pairs with the hub",
    "FUR-MAT-DSK": "Finishes the desk",
    "FUR-LMP-01": "Most desks in this order have no task light",
    "FUR-MON-ARM": "Frees the desk the monitor is sitting on",
}


class Inventory:
    """
    Stock, and the deliberate stockout switch the failure demo needs.

    `force_stockout` makes the *next* fulfilment of a SKU fail after the payment
    has already been captured. That ordering is the whole point: the brief asks
    for a failure handled gracefully, and the interesting failure is the one
    where the money has already moved.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stock: dict[str, int] = {p.sku: p.initial_stock for p in CATALOG}
        self._forced: str | None = None

    def level(self, sku: str) -> int:
        with self._lock:
            return self._stock.get(sku, 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stock)

    def reserve(self, sku: str, qty: int) -> bool:
        with self._lock:
            if self._stock.get(sku, 0) < qty:
                return False
            self._stock[sku] -= qty
            return True

    def restore(self, sku: str, qty: int) -> None:
        with self._lock:
            self._stock[sku] = self._stock.get(sku, 0) + qty

    def force_stockout(self, sku: str | None) -> str:
        with self._lock:
            self._forced = sku or "*"
            return self._forced

    def consume_forced(self, sku: str) -> bool:
        """True if this fulfilment should fail. Single shot."""
        with self._lock:
            if self._forced is None:
                return False
            if self._forced in ("*", sku):
                self._forced = None
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._stock = {p.sku: p.initial_stock for p in CATALOG}
            self._forced = None


def search(query: str | None, category: str | None = None, max_price_paise: int | None = None) -> list[Product]:
    """
    Keyword search over ten items. Anything cleverer here would be theatre.
    """
    terms = [t for t in "".join(c if c.isalnum() or c == "-" else " " for c in (query or "").lower()).split() if len(t) > 2]

    scored: list[tuple[int, Product]] = []
    for p in CATALOG:
        if category and p.category != category:
            continue
        if max_price_paise is not None and p.price_paise > max_price_paise:
            continue
        hay = " ".join((p.name, p.category, p.description, *p.tags)).lower()
        score = sum(1 for t in terms if t in hay)
        if score:
            scored.append((score, p))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1].price_paise))
        return [p for _, p in scored]

    # An empty result makes a demo look broken rather than honest. Fall back the
    # way a real catalog search with a relevance floor would.
    pool = [p for p in CATALOG if (not category or p.category == category)]
    if max_price_paise is not None:
        pool = [p for p in pool if p.price_paise <= max_price_paise]
    return sorted(pool, key=lambda p: p.price_paise)[:3]
