/**
 * REST client for the gate (8000), the merchant (8100) and the simulation
 * runner (8300).
 *
 * Everything goes through the Vite proxy under /api/* so the app is same
 * origin. That removes CORS from the list of things that can break on stage.
 */
import type {
  AgentCommerceManifest,
  Addon,
  Decision,
  Headroom,
  Mandate,
  MerchantStats,
  Order,
  Quote,
  SagaStep,
} from "./contracts";

export const GATE = "/api/gate";
export const MERCHANT = "/api/merchant";
export const SIM = "/api/sim";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    readonly body: string,
  ) {
    super(`${status} ${url}: ${body.slice(0, 200)}`);
    this.name = "ApiError";
  }
}

async function req<T>(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), init?.timeoutMs ?? 6000);
  try {
    const res = await fetch(url, {
      ...init,
      signal: ctl.signal,
      headers: {
        accept: "application/json",
        ...(init?.body ? { "content-type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    const text = await res.text();
    if (!res.ok) throw new ApiError(res.status, url, text);
    return (text ? JSON.parse(text) : null) as T;
  } finally {
    clearTimeout(timer);
  }
}

const get = <T,>(url: string) => req<T>(url);
const post = <T,>(url: string, body?: unknown) =>
  req<T>(url, { method: "POST", body: body === undefined ? "{}" : JSON.stringify(body) });

/* --------------------------------------------------------------- gate ----- */

export const gate = {
  /** Section 7. The envelope the merchant is allowed to read. */
  headroom: (mandateId: string) => get<Headroom>(`${GATE}/v1/mandates/${mandateId}/headroom`),

  /** On reconnect we refetch rather than assume the stream buffered. */
  decisions: (limit = 50) => get<{ decisions: Decision[] }>(`${GATE}/v1/decisions?limit=${limit}`),

  decision: (decisionId: string) => get<Decision>(`${GATE}/v1/decisions/${decisionId}`),

  /** Registers the mandate the human just signed on this device. */
  registerMandate: (mandate: Mandate) =>
    post<{ mandate_id: string; accepted: boolean; reason_code?: string }>(
      `${GATE}/v1/mandates`,
      mandate,
    ),

  revokeMandate: (mandateId: string) => post<{ ok: boolean }>(`${GATE}/v1/mandates/${mandateId}/revoke`),

  /** Resolves a STEP_UP the human approved or refused in the modal. */
  resolveStepUp: (decisionId: string, approve: boolean, signature?: string) =>
    post<{ verdict: string; reason_code: string }>(`${GATE}/v1/decisions/${decisionId}/step_up`, {
      approve,
      signature,
    }),

  reset: () => post<{ ok: boolean }>(`${GATE}/v1/admin/reset`),
};

/* ----------------------------------------------------------- merchant ----- */

export const merchant = {
  manifest: () => get<AgentCommerceManifest>(`${MERCHANT}/.well-known/agent-commerce.json`),

  stats: () => get<MerchantStats>(`${MERCHANT}/v1/stats`),

  orders: (limit = 50) => get<{ orders: Order[] }>(`${MERCHANT}/v1/orders?limit=${limit}`),

  saga: (orderId: string) => get<{ steps: SagaStep[] }>(`${MERCHANT}/v1/orders/${orderId}/saga`),

  quote: (items: Array<{ sku: string; qty: number }>, mandateId?: string) =>
    post<Quote>(`${MERCHANT}/v1/quote`, { items, mandate_id: mandateId ?? null }),

  suggestAddons: (quoteId: string, mandateId: string) =>
    post<{ addons: Addon[]; filtered_out: number }>(`${MERCHANT}/v1/suggest_addons`, {
      quote_id: quoteId,
      mandate_id: mandateId,
    }),

  /** Bound to a key in the demo strip. Lane A's endpoint, Lane B also calls it. */
  forceStockout: (sku?: string) =>
    post<{ ok: boolean; sku: string }>(`${MERCHANT}/admin/force_stockout`, { sku: sku ?? null }),

  reset: () => post<{ ok: boolean }>(`${MERCHANT}/admin/reset`),
};

/* ---------------------------------------------------------------- sim ----- */

export const sim = {
  /** Utkarsh's six demo beats. One key press each, never a typed command. */
  beat: (n: number) => post<{ ok: boolean; beat: number }>(`${SIM}/demo/beat/${n}`),
  reset: () => post<{ ok: boolean }>(`${SIM}/admin/reset`),
};

/**
 * Reset must clear the gate, the merchant and the simulated wallet in under a
 * second, and it gets pressed forty times. Fire all three together and do not
 * let one dead service block the other two — a partial reset that reports
 * honestly beats a hang.
 */
export async function resetAll(): Promise<{ ok: boolean; failed: string[] }> {
  const targets: Array<[string, () => Promise<unknown>]> = [
    ["gate", gate.reset],
    ["merchant", merchant.reset],
    ["sim", sim.reset],
  ];
  const results = await Promise.allSettled(targets.map(([, fn]) => fn()));
  const failed = targets.filter((_, i) => results[i].status === "rejected").map(([n]) => n);
  return { ok: failed.length === 0, failed };
}
