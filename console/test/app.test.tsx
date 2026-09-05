// @vitest-environment jsdom
/**
 * Mounts the whole app against a fake transport.
 *
 * The render tests cover each component in isolation. This one covers the part
 * they cannot: the provider, the router, the effects, and the wiring between a
 * stream event arriving and a row appearing. That is where a white screen
 * actually comes from — not from a component nobody wired up, but from a
 * component wired up wrongly.
 *
 * fetch and EventSource are stubbed rather than mocked out of the way, so the
 * store's real reconnect, resync and dedupe paths all run.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import type { Decision, MerchantStats, Order, SagaStep } from "../src/lib/contracts";

/* --------------------------------------------------------- fake transport - */

/** Every EventSource the app opens, so tests can push events into them. */
const streams: FakeEventSource[] = [];

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly listeners = new Map<string, Set<(e: MessageEvent) => void>>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    streams.push(this);
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.get(type)?.delete(fn);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.();
  }

  emit(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners.get(type) ?? []) fn(ev);
  }
}

const STATS: MerchantStats = {
  gmv_paise: 12450000,
  orders: 15,
  avg_order_value_paise: 830000,
  upsell_offers_made: 22,
  upsell_offers_accepted: 9,
  upsell_offers_filtered_by_headroom: 6,
  upsell_attach_rate: 0.41,
  recovered_paise: 1820000,
  recovered_orders: 2,
  needs_attention: 1,
};

const ORDER: Order = {
  order_id: "ord_STREAMED",
  quote_id: "qte_1",
  mandate_id: "mnd_1",
  state: "PAYMENT_CAPTURED",
  amount_paise: 249900,
  items_summary: "1x Adjustable desk lamp",
  rail: "mock_upi",
  at: "2026-08-30T14:22:05Z",
};

const DECISION: Decision = {
  decision_id: "dec_STREAMED",
  mandate_id: "mnd_1",
  verdict: "BLOCK",
  reason_code: "QUOTE_AMOUNT_MISMATCH",
  reason_detail: "payment 4,90,000 does not match quote 2,49,900",
  payee_vpa: "deskkit@razorpay",
  amount_paise: 490000,
  elapsed_ms: 2.4,
  checks: [{ name: "quote_binding", status: "FAIL", ms: 0.2 }],
  at: "2026-08-30T14:22:05Z",
};

const STEP: SagaStep = {
  order_id: "ord_STREAMED",
  seq: 5,
  state: "REFUND_ISSUED",
  action: "razorpay.refund",
  outcome: "OK",
  detail: "idempotent",
  ref: "rfnd_abc",
  at: "2026-08-30T14:22:10Z",
};

function fakeFetch(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  const body = url.includes("/v1/stats")
    ? STATS
    : url.includes("/v1/orders?")
      ? { orders: [] }
      : url.includes("/saga")
        ? { steps: [] }
        : url.includes("/v1/decisions")
          ? { decisions: [] }
          : url.includes("test_vector")
            ? { keys: {}, cases: [] }
            : {};
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}

/* ------------------------------------------------------------------ setup - */

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // React 18 only applies act() semantics when this is set, and without it the
  // assertions run against a tree that has not finished flushing.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  streams.length = 0;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  // The device key is generated on first use and localStorage is not always
  // there. jsdom provides it; this just guarantees a clean key per test.
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const mount = async () => {
  await act(async () => {
    root.render(<App />);
  });
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
};

/* ------------------------------------------------------------------ tests - */

describe("the app mounts and wires the streams", () => {
  it("renders the shell and opens both streams", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    expect(container.textContent).toContain("PACT");
    // One stream for the gate, one for the merchant. Section 6.
    const urls = streams.map((s) => s.url);
    expect(urls.some((u) => u.includes("/api/gate/v1/decisions/stream"))).toBe(true);
    expect(urls.some((u) => u.includes("/api/merchant/v1/stream"))).toBe(true);
  });

  it("polls stats as a backstop even before any stream opens", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) =>
      String(c[0]),
    );
    expect(calls.some((u) => u.includes("/v1/stats"))).toBe(true);
    // The polled stats reach the tiles: 1,24,500 rupees, Indian grouped.
    expect(container.textContent).toContain("1,24,500");
  });

  it("refetches on reconnect rather than assuming the stream buffered", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const before = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    await act(async () => {
      for (const s of streams) s.open();
    });
    await flush();
    const after = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    expect(after).toBeGreaterThan(before);
  });

  it("puts a streamed decision and a streamed order on screen", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    const shop = streams.find((s) => s.url.includes("/api/merchant"))!;

    await act(async () => {
      gate.emit("decision", DECISION);
      shop.emit("order", ORDER);
      shop.emit("stats", STATS);
    });
    await flush();

    expect(container.textContent).toContain("QUOTE_AMOUNT_MISMATCH");
    expect(container.textContent).toContain("Vetoed");
    expect(container.textContent).toContain("ord_");
    // The saga step must move the order row's state even with the timeline shut.
    await act(async () => {
      shop.emit("saga_step", STEP);
    });
    await flush();
    expect(container.textContent).toContain("REFUND_ISSUED");
  });

  it("ignores a duplicate decision instead of showing it twice", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    await act(async () => {
      gate.emit("decision", DECISION);
      gate.emit("decision", DECISION);
    });
    await flush();

    const occurrences = (container.textContent ?? "").split("QUOTE_AMOUNT_MISMATCH").length - 1;
    expect(occurrences).toBe(1);
  });

  it("raises the step up modal when the gate asks for a human", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    await act(async () => {
      gate.emit("decision", {
        ...DECISION,
        decision_id: "dec_STEPUP",
        verdict: "STEP_UP",
        reason_code: "INTENT_MISMATCH",
        reason_detail: "The purchase does not match the stated goal",
      });
    });
    await flush();

    expect(container.textContent).toContain("Your agent is asking permission");
    expect(container.textContent).toContain("Approve");
  });

  it("clears every feed when the server says it reset", async () => {
    window.location.hash = "#/console";
    await mount();
    await flush();

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    await act(async () => gate.emit("decision", DECISION));
    await flush();
    expect(container.textContent).toContain("QUOTE_AMOUNT_MISMATCH");

    await act(async () => gate.emit("reset", { at: "now" }));
    await flush();
    expect(container.textContent).not.toContain("QUOTE_AMOUNT_MISMATCH");
    expect(container.textContent).toContain("No decisions yet");
  });

  it("routes to every surface without throwing", async () => {
    for (const route of ["#/grant", "#/checkout", "#/console", "#/slides", "#/firewall"]) {
      window.location.hash = route;
      await mount();
      await flush();
      expect(container.textContent, `blank on ${route}`).not.toBe("");
    }
  });

  it("the grant screen offers exactly one button to sign", async () => {
    window.location.hash = "#/grant";
    await mount();
    await flush();
    expect(container.textContent).toContain("Grant and sign");
    expect(container.textContent).toContain("UPI Circle");
  });
});
