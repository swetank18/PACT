// @vitest-environment jsdom
/**
 * The principal's firewall surface.
 *
 * Two halves. The pure functions — health, threat summary, the export chain —
 * are tested directly, because they are the ones that would quietly produce a
 * plausible wrong number. The screen is mounted against a fake transport for
 * the things a unit test of a component cannot reach: that the drawer renders
 * the whole chain rather than the checks the engine happened to report, and
 * that the kill switch actually reaches the gate rather than setting a flag in
 * this browser.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import type { Decision, Headroom, Mandate } from "../src/lib/contracts";
import {
  effectiveHeadroom,
  healthFor,
  statusOf,
  threatSummary,
} from "../src/surfaces/firewall/derive";
import type { StoredMandate } from "../src/surfaces/firewall/state";

/* -------------------------------------------------------------- fixtures - */

const MANDATE: Mandate = {
  v: 1,
  mandate_id: "mnd_TEST",
  delegator: { vpa: "swetank@okaxis", pubkey: "pk" },
  delegate: { agent_id: "buyer_agent_v1", pubkey: "pk" },
  intent: "restock office supplies",
  constraints: {
    max_per_txn_paise: 300000,
    max_total_paise: 300000,
    max_count: 1,
    merchant_allowlist: ["deskkit@razorpay"],
    category_allowlist: ["stationery"],
    valid_from: new Date(Date.now() - 3600_000).toISOString(),
    valid_until: new Date(Date.now() + 3 * 3600_000).toISOString(),
  },
  issued_at: new Date(Date.now() - 3600_000).toISOString(),
};

const STORED: StoredMandate = {
  mandate: MANDATE,
  signature: "sig",
  created_at: MANDATE.issued_at,
};

const HEADROOM: Headroom = {
  mandate_id: "mnd_TEST",
  headroom_paise: 60000,
  max_per_txn_paise: 300000,
  payments_remaining: 1,
  categories_allowed: ["stationery"],
  valid_until: MANDATE.constraints.valid_until,
  merchant_in_scope: true,
  as_of: new Date().toISOString(),
  signature: "sig",
};

/** A block on the scope check, with everything after it never reached. */
const BLOCKED: Decision = {
  decision_id: "dec_BLOCKED",
  mandate_id: "mnd_TEST",
  verdict: "BLOCK",
  reason_code: "SCOPE_MERCHANT_NOT_ALLOWED",
  reason_detail: "giftcard-store@upi is not in the approved merchant list",
  payee_vpa: "giftcard-store@upi",
  amount_paise: 500000,
  elapsed_ms: 3.1,
  checks: [
    { name: "request_signature", status: "PASS", ms: 0.4 },
    { name: "mandate_signature", status: "PASS", ms: 0.1 },
    { name: "mandate_state", status: "PASS", ms: 0.1 },
    { name: "validity_window", status: "PASS", ms: 0.1 },
    { name: "freshness", status: "PASS", ms: 0.1 },
    { name: "replay", status: "PASS", ms: 0.3 },
    { name: "scope", status: "FAIL", ms: 0.2, detail: "payee is not on the allowlist" },
  ],
  at: new Date().toISOString(),
};

/* ------------------------------------------------------- fake transport --- */

const streams: FakeEventSource[] = [];

class FakeEventSource {
  readonly listeners = new Map<string, Set<(e: MessageEvent) => void>>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;

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
  close() {}
  emit(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners.get(type) ?? []) fn(ev);
  }
}

const posted: Array<{ url: string; init?: RequestInit }> = [];

/** Flipped by the test that checks what happens when the gate refuses one. */
let acceptMandates = true;

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (init?.method === "POST") posted.push({ url, init });

  const body = url.endsWith("/v1/mandates") && init?.method === "POST"
    ? { mandate_id: "mnd_NEW", accepted: acceptMandates, reason_code: "MANDATE_SIG_INVALID" }
    : url.includes("/headroom")
    ? HEADROOM
    : url.includes("/v1/decisions")
      ? { decisions: [BLOCKED] }
      : url.includes("/v1/stats")
        ? {}
        : url.includes("/v1/orders")
          ? { orders: [] }
          : url.includes("agent-commerce.json")
            ? { categories: ["stationery"], merchant_vpa: "deskkit@razorpay" }
            : { ok: true };

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
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  streams.length = 0;
  posted.length = 0;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  localStorage.clear();
  acceptMandates = true;
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

/**
 * Type into a controlled input the way a person does.
 *
 * Assigning `.value` directly is invisible to React — it tracks the last value
 * it set and skips the change event when the DOM already agrees. Going through
 * the prototype setter is what makes onChange fire.
 */
const type = async (selector: string, value: string) => {
  const el = container.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement | null;
  if (!el) throw new Error(`no element matching ${selector}`);
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")!.set!.call(el, value);
  await act(async () => {
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flush();
};

const click = async (text: string) => {
  const el = [...container.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").replace(/\s+/g, " ").includes(text),
  );
  if (!el) throw new Error(`no button reading ${JSON.stringify(text)}`);
  await act(async () => el.click());
  await flush();
};

/* ------------------------------------------------------- pure functions --- */

describe("the health score is a function of what the gate reports", () => {
  it("bands a mandate that is 80% spent with little time left as WATCH", () => {
    // 80% of the budget gone, three hours left of a four hour window, no
    // payments used, nothing blocked. Amber on two signals, green on two.
    const h = healthFor(STORED, HEADROOM, 0);
    expect(h.word).toBe("WATCH");
    expect(h.score).toBeGreaterThan(45);
    expect(h.score).toBeLessThan(80);
    expect(h.signals.map((s) => s.label)).toEqual([
      "Budget",
      "Time",
      "Payments",
      "Blocked attempts",
    ]);
  });

  it("is healthy when nothing has been spent and nothing was refused", () => {
    const fresh = { ...HEADROOM, headroom_paise: 300000 };
    expect(healthFor(STORED, fresh, 0).word).toBe("HEALTHY");
  });

  it("goes critical once the budget is gone and attempts are being refused", () => {
    const drained = { ...HEADROOM, headroom_paise: 0, payments_remaining: 0 };
    expect(healthFor(STORED, drained, 5).word).toBe("CRITICAL");
  });

  it("says the gate did not answer rather than reporting zero spend", () => {
    const h = healthFor(STORED, undefined, 0);
    expect(h.signals[0].detail).toContain("not reported");
  });
});

describe("mandate status", () => {
  it("is Spent, not Active, when the envelope reports nothing left", () => {
    expect(statusOf(STORED, { ...HEADROOM, headroom_paise: 0 })).toBe("Spent");
  });

  it("separates a kill-switch pause from a deliberate revocation", () => {
    const at = new Date().toISOString();
    expect(statusOf({ ...STORED, revoked_at: at, revoked_reason: "kill_switch" }, HEADROOM)).toBe(
      "Paused",
    );
    expect(statusOf({ ...STORED, revoked_at: at, revoked_reason: "user" }, HEADROOM)).toBe(
      "Revoked",
    );
  });

  it("is Expired once the window has closed", () => {
    const past = {
      ...STORED,
      mandate: {
        ...MANDATE,
        constraints: { ...MANDATE.constraints, valid_until: "2020-01-01T00:00:00Z" },
      },
    };
    expect(statusOf(past, HEADROOM)).toBe("Expired");
  });
});

describe("a revoked mandate is not reported as a spent one", () => {
  const at = new Date().toISOString();
  // The gate reports zero headroom on anything revoked, by design. Read
  // literally that is indistinguishable from "fully spent", which is a
  // different and false claim about a mandate the kill switch paused.
  const dead: Headroom = { ...HEADROOM, headroom_paise: 0, payments_remaining: 0 };

  it("reads a paused mandate from the remainder captured before the revoke", () => {
    const paused = {
      ...STORED,
      revoked_at: at,
      revoked_reason: "kill_switch" as const,
      remainder: { headroom_paise: 60000, payments_remaining: 1, max_per_txn_paise: 300000 },
    };
    const hr = effectiveHeadroom(paused, dead)!;
    expect(hr.headroom_paise).toBe(60000);
    expect(hr.payments_remaining).toBe(1);
    // And so the health dot describes the mandate rather than the revocation.
    expect(healthFor(paused, hr, 0).word).toBe("WATCH");
  });

  it("gives nothing at all for a mandate revoked outright", () => {
    const revoked = { ...STORED, revoked_at: at, revoked_reason: "user" as const };
    expect(effectiveHeadroom(revoked, dead)).toBeUndefined();
  });

  it("passes a live mandate's envelope straight through", () => {
    expect(effectiveHeadroom(STORED, HEADROOM)).toBe(HEADROOM);
  });
});

describe("the threat summary counts what was refused", () => {
  const injection: Decision = {
    ...BLOCKED,
    decision_id: "dec_INJ",
    reason_code: "INTENT_INJECTION_SUSPECTED",
    amount_paise: 100000,
  };
  const replay: Decision = {
    ...BLOCKED,
    decision_id: "dec_REP",
    reason_code: "NONCE_REPLAY",
    amount_paise: 250000,
  };
  const allowed: Decision = { ...BLOCKED, decision_id: "dec_OK", verdict: "ALLOW", amount_paise: 999 };

  it("sums only what was blocked, and attributes each reason", () => {
    const t = threatSummary([BLOCKED, injection, replay, allowed]);
    expect(t.blocks).toBe(3);
    expect(t.prevented_paise).toBe(500000 + 100000 + 250000);
    expect(t.injections).toBe(1);
    expect(t.replays).toBe(1);
    expect(t.top_payee?.vpa).toBe("giftcard-store@upi");
  });

  it("reports nothing rather than something reassuring when there is no data", () => {
    const t = threatSummary([]);
    expect(t.blocks).toBe(0);
    expect(t.prevented_paise).toBe(0);
    expect(t.window).toBeNull();
  });
});

/* ---------------------------------------------------------- the surface --- */

describe("the firewall surface", () => {
  it("renders the six tabs and the kill switch on every one of them", async () => {
    window.location.hash = "#/firewall";
    await mount();
    await flush();

    for (const label of [
      "Dashboard",
      "Mandates",
      "Transactions",
      "Analytics",
      "Agents",
      "Settings",
    ]) {
      expect(container.textContent, `missing tab ${label}`).toContain(label);
    }
    expect(container.textContent).toContain("PAUSE ALL AGENTS");

    await click("Settings");
    expect(container.textContent).toContain("PAUSE ALL AGENTS");
    expect(container.textContent).toContain("Signing key fingerprint");
  });

  it("puts a streamed decision on the live feed", async () => {
    window.location.hash = "#/firewall";
    await mount();
    await flush();

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    await act(async () => gate.emit("decision", { ...BLOCKED, decision_id: "dec_LIVE" }));
    await flush();

    expect(container.textContent).toContain("BLOCKED");
    expect(container.textContent).toContain("giftcard-store@upi");
  });

  it("renders the whole check chain in the drawer, including what was never reached", async () => {
    window.location.hash = "#/firewall/transactions";
    await mount();
    await flush();

    const row = container.querySelector("tbody tr") as HTMLElement | null;
    expect(row, "no transaction row").not.toBeNull();
    await act(async () => row!.click());
    await flush();

    const drawer = container.querySelector('[aria-label="Transaction detail"]')!;
    const text = drawer.textContent ?? "";

    // Seven checks ran; the engine never reported the last three, and the
    // drawer must show them as skipped rather than omitting them.
    expect(text).toContain("scope");
    expect(text).toContain("ceiling");
    expect(text).toContain("quote binding");
    expect(text).toContain("intent");
    expect(text).toContain("Skipped — blocked before reaching this check");
    expect(text).toContain("SCOPE_MERCHANT_NOT_ALLOWED");
    // Spec 3.5: a block, and only a block, offers the recovery.
    expect(text).toContain("Create mandate for this");
  });

  it("does not invent an intent confidence the engine never produced", async () => {
    window.location.hash = "#/firewall/transactions";
    await mount();
    await flush();

    const row = container.querySelector("tbody tr") as HTMLElement;
    await act(async () => row.click());
    await flush();

    const text = container.querySelector('[aria-label="Transaction detail"]')!.textContent ?? "";
    expect(text).toContain("The auditor answers yes or no, not a percentage");
    expect(text).toContain("Not reached");
  });

  it("revokes at the gate when the kill switch is pulled, rather than setting a local flag", async () => {
    localStorage.setItem("pact.firewall.mandates.v1", JSON.stringify([STORED]));
    window.location.hash = "#/firewall";
    await mount();
    await flush();

    await click("⏹ PAUSE ALL AGENTS");
    await click("Pause everything");

    const revokes = posted.filter((p) => p.url.includes("/revoke"));
    expect(revokes.length, "the kill switch did not reach the gate").toBe(1);
    expect(revokes[0].url).toContain("mnd_TEST");

    // And the switch now offers the way back.
    expect(container.textContent).toContain("RESUME ALL");
    expect(JSON.parse(localStorage.getItem("pact.firewall.kill.v1")!).engaged).toBe(true);
  });

  it("re-reads only the mandate a decision names, not every mandate it holds", async () => {
    // At demo pace a full sweep per decision is invisible. At the ~7 decisions
    // a second the soak sustains it is not: five mandates became ~35 headroom
    // requests a second, almost all for mandates nothing had happened to.
    const other: StoredMandate = {
      ...STORED,
      mandate: { ...MANDATE, mandate_id: "mnd_OTHER" },
    };
    localStorage.setItem("pact.firewall.mandates.v1", JSON.stringify([STORED, other]));
    window.location.hash = "#/firewall";
    await mount();
    await flush();

    const headroomCalls = () =>
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
        .map((c) => String(c[0]))
        .filter((u) => u.includes("/headroom"));

    const before = headroomCalls().length;

    const gate = streams.find((s) => s.url.includes("/api/gate"))!;
    await act(async () => {
      // Ten decisions against one held mandate, and one against a mandate this
      // device never signed.
      for (let i = 0; i < 10; i++) {
        gate.emit("decision", { ...BLOCKED, decision_id: `dec_${i}`, mandate_id: "mnd_TEST" });
      }
      gate.emit("decision", {
        ...BLOCKED,
        decision_id: "dec_ELSEWHERE",
        mandate_id: "mnd_SOMEONE_ELSE",
      });
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 600)); // past the coalescing window
    });
    await flush();

    const added = headroomCalls().slice(before);
    // One request, for the one mandate that actually moved.
    expect(added.length).toBe(1);
    expect(added[0]).toContain("mnd_TEST");
    expect(added.some((u) => u.includes("mnd_OTHER"))).toBe(false);
    expect(added.some((u) => u.includes("mnd_SOMEONE_ELSE"))).toBe(false);
  });

  it("keeps the kill switch state across a reload", async () => {
    localStorage.setItem(
      "pact.firewall.kill.v1",
      JSON.stringify({ engaged: true, at: new Date().toISOString(), paused: [] }),
    );
    window.location.hash = "#/firewall";
    await mount();
    await flush();
    expect(container.textContent).toContain("RESUME ALL");
  });

  it("does not call a mandate registered when the gate refused it", async () => {
    // The gate answers 200 with accepted:false for a signature it cannot
    // verify — it stores the mandate so the audit trail shows the attempt.
    // Reading that as success puts a mandate on screen as live that refuses
    // every payment made against it.
    acceptMandates = false;
    window.location.hash = "#/firewall/mandates";
    await mount();
    await flush();

    await click("+ Create mandate");
    await type("#fw-intent", "restock office supplies");

    await click("Next →"); // scope
    await type("#fw-vpa", "deskkit@razorpay");
    await click("Add");
    await click("Next →"); // caps
    await click("Next →"); // window
    await click("Next →"); // review
    await click("Sign & activate");
    await flush();

    expect(container.textContent).toContain("the gate refused it");
    expect(container.textContent).not.toContain("accepted by the gate");

    // And it stays visible afterwards, rather than only at the moment of signing.
    const stored = JSON.parse(localStorage.getItem("pact.firewall.mandates.v1")!);
    expect(stored[0].registered).toBe("rejected");
  });

  it("says so on the mandate itself when the gate never received it", async () => {
    localStorage.setItem(
      "pact.firewall.mandates.v1",
      JSON.stringify([{ ...STORED, registered: "unreachable", register_detail: "503" }]),
    );
    window.location.hash = "#/firewall/mandates";
    await mount();
    await flush();

    // The table marks it, so a row that will refuse every payment does not read
    // like an ordinary one.
    expect(container.querySelector("tbody")!.textContent).toContain("⚠️");

    await act(async () => (container.querySelector("tbody tr") as HTMLElement).click());
    await flush();
    expect(container.textContent).toContain("never received this mandate");
    expect(container.textContent).toContain("Hand it over again");
  });

  it("builds the ablation matrix out of the measured run, not a hand written table", async () => {
    window.location.hash = "#/firewall/analytics";
    await mount();
    await flush();

    const text = container.textContent ?? "";
    expect(text).toContain("Which check catches which attack");
    expect(text).toContain("atk_02");
    expect(text).toContain("python sim/run.py --all");
    // atk_06 needs the model auditor, which has no key here. It must read as
    // unmeasured rather than as a pass.
    expect(text).toContain("n/a");
  });
});
