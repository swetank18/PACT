/**
 * Smoke render for every surface and every component that carries a demo beat.
 *
 * These are not snapshot tests and they assert almost nothing about layout.
 * They exist to catch the failure that actually happens under time pressure: a
 * component that throws on mount because a field was null in a shape nobody had
 * seen yet. On stage that is a white screen, and a white screen is unrecoverable.
 *
 * So the cases here feed the awkward input — an empty feed, a decision whose
 * check list the engine truncated, a saga with no steps, a headroom envelope
 * with nothing left — rather than the happy path.
 */
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GateResult, QuoteCard, UpsellCard } from "../src/components/CheckoutCards";
import { DecisionFeed } from "../src/components/DecisionFeed";
import { GateTrace } from "../src/components/GateTrace";
import { HeadroomBar } from "../src/components/HeadroomBar";
import { OrderFeed } from "../src/components/OrderFeed";
import { RevenueStrip } from "../src/components/RevenueStrip";
import { SagaTimeline } from "../src/components/SagaTimeline";
import type { Decision, Headroom, Order, Quote, SagaStep } from "../src/lib/contracts";
import { EMPTY_STATS } from "../src/lib/store";
import { Slides } from "../src/surfaces/slides/Slides";

const headroom: Headroom = {
  mandate_id: "mnd_TEST",
  headroom_paise: 890000,
  max_per_txn_paise: 500000,
  payments_remaining: 3,
  categories_allowed: ["stationery", "cables"],
  valid_until: "2026-08-31T10:00:00Z",
  merchant_in_scope: true,
  as_of: "2026-08-30T14:22:05Z",
  signature: "sig",
};

/** Deliberately truncated: only three of the ten checks are reported. */
const decision: Decision = {
  decision_id: "dec_TEST",
  mandate_id: "mnd_TEST",
  verdict: "BLOCK",
  reason_code: "QUOTE_AMOUNT_MISMATCH",
  reason_detail: "payment 4,90,000 does not match quote 2,49,900",
  payee_vpa: "deskkit@razorpay",
  amount_paise: 490000,
  elapsed_ms: 2.1,
  checks: [
    { name: "request_signature", status: "PASS", ms: 0.9 },
    { name: "scope", status: "PASS", ms: 0.2 },
    { name: "quote_binding", status: "FAIL", ms: 0.2, detail: "amount mismatch" },
  ],
  at: "2026-08-30T14:22:05Z",
};

const quote: Quote = {
  quote_id: "qte_TEST",
  items: [
    {
      sku: "STA-NB-A5",
      name: "A5 ruled notebook, 5 pack",
      qty: 2,
      unit_paise: 74900,
      line_total_paise: 149800,
      category: "stationery",
    },
  ],
  subtotal_paise: 149800,
  tax_paise: 26964,
  shipping_paise: 0,
  total_paise: 176764,
  expires_at: "2099-01-01T00:00:00Z",
  headroom_fit: null,
};

const order: Order = {
  order_id: "ord_TEST",
  quote_id: "qte_TEST",
  mandate_id: "mnd_TEST",
  state: "NEEDS_ATTENTION",
  amount_paise: 176764,
  items_summary: "2x A5 ruled notebook",
  rail: "mock_upi",
  at: "2026-08-30T14:22:05Z",
};

const steps: SagaStep[] = [
  {
    order_id: "ord_TEST",
    seq: 1,
    state: "PAYMENT_CAPTURED",
    action: "razorpay.capture",
    outcome: "OK",
    detail: "test mode",
    ref: "pay_x",
    at: "2026-08-30T14:22:08Z",
  },
  {
    order_id: "ord_TEST",
    seq: 2,
    state: "FULFILMENT",
    action: "fulfil",
    outcome: "FAIL",
    detail: "out of stock, concurrent sale",
    reason_code: "STOCK_UNAVAILABLE",
    at: "2026-08-30T14:22:09Z",
  },
];

describe("components render without throwing", () => {
  it("gate trace shows the full chain even when the engine truncates it", () => {
    const html = renderToString(<GateTrace decision={decision} />);
    for (const name of [
      "request signature",
      "mandate signature",
      "validity window",
      "freshness",
      "replay",
      "scope",
      "ceiling",
      "quote binding",
      "intent",
    ]) {
      expect(html, `missing "${name}"`).toContain(name);
    }
    expect(html).toContain("SKIPPED");
  });

  it("headroom bar survives a null envelope and a zero balance", () => {
    expect(renderToString(<HeadroomBar headroom={null} />)).toContain("No mandate yet");
    expect(
      renderToString(
        <HeadroomBar headroom={{ ...headroom, headroom_paise: 0 }} purchasePaise={1} />,
      ),
    ).toBeTruthy();
  });

  it("headroom bar warns when the purchase exceeds remaining authority", () => {
    const html = renderToString(
      <HeadroomBar headroom={headroom} purchasePaise={900000} totalBudgetPaise={1500000} />,
    );
    expect(html).toContain("the gate would refuse this");
  });

  it("feeds render both empty and populated", () => {
    expect(renderToString(<DecisionFeed decisions={[]} />)).toContain("No decisions yet");
    expect(renderToString(<DecisionFeed decisions={[decision]} />)).toContain(
      "QUOTE_AMOUNT_MISMATCH",
    );
    expect(renderToString(<OrderFeed orders={[]} saga={{}} onSelect={() => {}} />)).toContain(
      "No orders yet",
    );
    expect(renderToString(<OrderFeed orders={[order]} saga={{}} onSelect={() => {}} />)).toContain(
      "NEEDS_ATTENTION",
    );
  });

  it("revenue strip renders a cold start without dividing by zero", () => {
    const html = renderToString(<RevenueStrip stats={EMPTY_STATS} />);
    expect(html).toContain("GMV today");
    expect(html).not.toContain("NaN");
  });

  it("saga timeline renders empty and populated", () => {
    expect(renderToString(<SagaTimeline steps={[]} />)).toContain("No saga steps");
    expect(renderToString(<SagaTimeline steps={steps} animate={false} />)).toContain(
      "out of stock, concurrent sale",
    );
  });

  it("a failing saga row shows the reason code, not only the prose", () => {
    // The prose is presentation and may be reworded at any time. The code is
    // the contract, and it is what makes the trail on screen look like a
    // machine-readable record rather than a log file.
    const html = renderToString(<SagaTimeline steps={steps} animate={false} />);
    expect(html).toContain("STOCK_UNAVAILABLE");

    // And a successful row stays quiet, or the code stops meaning anything.
    const okOnly = renderToString(<SagaTimeline steps={[steps[0]]} animate={false} />);
    expect(okOnly).not.toContain("STOCK_UNAVAILABLE");
  });

  it("checkout cards carry the sentences the pitch depends on", () => {
    expect(renderToString(<QuoteCard quote={quote} />)).toContain(
      "Prices computed by the merchant",
    );
    expect(
      renderToString(
        <UpsellCard
          addons={[{ sku: "A", name: "Gel pens", category: "stationery", price_paise: 42000 }]}
          filteredOut={2}
          onAccept={() => {}}
          onDecline={() => {}}
        />,
      ),
    ).toContain("checked against the buyer&#x27;s remaining authority");
    expect(renderToString(<GateResult decision={decision} />)).toContain("Vetoed");
  });

  it("the deck renders", () => {
    expect(renderToString(<Slides />)).toContain("merchant reads");
  });
});
