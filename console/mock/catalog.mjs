/**
 * DeskKit's catalog, and the complements table behind suggest_addons.
 *
 * The recommender is deliberately dumb — a hand built co-occurrence table. The
 * claim is that every offer made is provably approvable, and a dumb recommender
 * proves that claim exactly as well as a clever one.
 */

export const MERCHANT_NAME = "DeskKit";
export const MERCHANT_VPA = "deskkit@razorpay";

/** Prices are integer paise. Never floats, never rupees. */
export const CATALOG = [
  {
    sku: "STA-NB-A5",
    name: "A5 ruled notebook, 5 pack",
    category: "stationery",
    price_paise: 74900,
    in_stock: 40,
    description: "Soft-cover A5 notebooks, 96 pages, 5 per pack.",
    tags: ["notebook", "notebooks", "paper", "supplies", "stationery", "restock", "office"],
  },
  {
    sku: "STA-PEN-12",
    name: "Gel pens, 12 pack",
    category: "stationery",
    price_paise: 42000,
    in_stock: 60,
    description: "0.5mm gel pens, black, 12 per box.",
    tags: ["pen", "pens", "supplies", "stationery", "restock", "office"],
  },
  {
    sku: "STA-STK-01",
    name: "Sticky notes, assorted",
    category: "stationery",
    price_paise: 18500,
    in_stock: 80,
    description: "76mm sticky notes, four colours, 400 sheets.",
    tags: ["sticky", "notes", "supplies", "stationery", "office"],
  },
  {
    sku: "CBL-USBC-2M",
    name: "USB-C to USB-C cable, 2m",
    category: "cables",
    price_paise: 89900,
    in_stock: 25,
    description: "100W USB-C cable, braided, 2 metres.",
    tags: ["cable", "cables", "usb", "usb-c", "usbc", "charging", "hub"],
  },
  {
    sku: "CBL-HDMI-2M",
    name: "HDMI 2.1 cable, 2m",
    category: "cables",
    price_paise: 119900,
    in_stock: 18,
    description: "48Gbps HDMI 2.1, supports 4K120.",
    tags: ["cable", "cables", "hdmi", "monitor", "display"],
  },
  {
    sku: "CBL-HUB-7P",
    name: "7 port USB-C hub",
    category: "cables",
    price_paise: 349900,
    in_stock: 12,
    description: "HDMI, ethernet, SD, 3x USB-A, 100W passthrough.",
    tags: ["hub", "dock", "usb", "usb-c", "usbc", "cables", "desk"],
  },
  {
    sku: "FUR-LMP-01",
    name: "Adjustable desk lamp",
    category: "office_furniture",
    price_paise: 229900,
    in_stock: 20,
    description: "Warm-to-cool LED, 5 brightness steps, clamp mount.",
    tags: ["lamp", "lamps", "light", "desk", "furniture"],
  },
  {
    sku: "FUR-MON-ARM",
    name: "Single monitor arm",
    category: "office_furniture",
    price_paise: 419900,
    in_stock: 9,
    description: "Gas spring arm, VESA 75/100, up to 9kg.",
    tags: ["monitor", "arm", "desk", "furniture", "hire", "setup"],
  },
  {
    sku: "FUR-CHR-ERG",
    name: "Ergonomic task chair",
    category: "office_furniture",
    price_paise: 1249900,
    in_stock: 4,
    description: "Mesh back, adjustable lumbar and armrests.",
    tags: ["chair", "seat", "desk", "furniture", "setup", "hire"],
  },
  {
    sku: "FUR-MAT-DSK",
    name: "Desk mat, felt",
    category: "office_furniture",
    price_paise: 99900,
    in_stock: 35,
    description: "900x400mm recycled felt desk mat.",
    tags: ["mat", "desk", "furniture", "setup"],
  },
];

export const BY_SKU = new Map(CATALOG.map((p) => [p.sku, p]));

/**
 * Complements. Hand built, and that is the whole point — see the note above.
 * Order matters: the first entry that is in stock and fits headroom is offered
 * first.
 */
export const COMPLEMENTS = {
  "STA-NB-A5": ["STA-PEN-12", "STA-STK-01", "FUR-LMP-01"],
  "STA-PEN-12": ["STA-NB-A5", "STA-STK-01"],
  "STA-STK-01": ["STA-PEN-12", "STA-NB-A5"],
  "CBL-USBC-2M": ["CBL-HUB-7P", "CBL-HDMI-2M", "FUR-MAT-DSK"],
  "CBL-HDMI-2M": ["CBL-HUB-7P", "FUR-MON-ARM"],
  "CBL-HUB-7P": ["CBL-HDMI-2M", "CBL-USBC-2M"],
  "FUR-LMP-01": ["FUR-MAT-DSK", "STA-STK-01"],
  "FUR-MON-ARM": ["CBL-HDMI-2M", "FUR-MAT-DSK"],
  "FUR-CHR-ERG": ["FUR-MAT-DSK", "FUR-LMP-01"],
  "FUR-MAT-DSK": ["FUR-LMP-01", "STA-STK-01"],
};

export const ADDON_REASON = {
  "STA-PEN-12": "Pens run out before notebooks do",
  "STA-STK-01": "Goes with the notebooks",
  "CBL-HUB-7P": "One cable is rarely enough for a desk",
  "CBL-HDMI-2M": "Pairs with the hub",
  "FUR-MAT-DSK": "Finishes the desk",
  "FUR-LMP-01": "Most desks in this order have no task light",
  "FUR-MON-ARM": "Frees the desk the monitor is sitting on",
};

/** Naive keyword search. The catalog is ten items; anything cleverer is theatre. */
export function search(query, category) {
  const terms = String(query || "")
    .toLowerCase()
    .split(/[^a-z0-9-]+/)
    .filter((t) => t.length > 2);

  const scored = CATALOG.map((p) => {
    if (category && p.category !== category) return { p, score: -1 };
    const hay = [p.name, p.category, p.description, ...p.tags].join(" ").toLowerCase();
    let score = 0;
    for (const t of terms) if (hay.includes(t)) score += 1;
    return { p, score };
  })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || a.p.price_paise - b.p.price_paise);

  // An empty result set makes the demo look broken rather than honest, so an
  // unmatched query falls back to the cheapest stationery — the same thing a
  // real catalog search would do with a relevance floor.
  if (scored.length === 0) {
    return CATALOG.filter((p) => p.category === "stationery").slice(0, 3);
  }
  return scored.map((x) => x.p);
}
