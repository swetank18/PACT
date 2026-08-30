/**
 * Surface two: conversational checkout, buyer side. LANE-C section 3.
 *
 * A conversation between the human and their buyer agent, with structured
 * cards inline rather than walls of text. The moment this surface is designed
 * around is: the upsell card appears, the headroom bar shows it fitting with
 * room to spare, the human taps accept, the bar advances.
 *
 * What this component is and is not. It is the buyer-side UI and the device
 * that holds the key — every authorize request leaving here is signed in the
 * browser. It is not the tool-calling agent; that is Lane B's, and when it
 * drives a session the same thread renders from the stream instead.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { gate, merchant, type AuthorizeRequest } from "../../lib/api";
import type { Addon, Decision, Headroom, Order, Quote, SagaStep } from "../../lib/contracts";
import { signPayload } from "../../lib/crypto";
import { getDeviceKey, newId } from "../../lib/device";
import { inr, nowRfc3339, shortId } from "../../lib/money";
import { GateResult, QuoteCard, UpsellCard } from "../../components/CheckoutCards";
import { HeadroomBar } from "../../components/HeadroomBar";
import { SagaTimeline } from "../../components/SagaTimeline";
import { Button, Empty } from "../../components/ui";
import type { GrantResult } from "../grant/Grant";
import s from "./Checkout.module.css";

type Card =
  | { kind: "quote"; quote: Quote }
  | { kind: "upsell"; addons: Addon[]; filteredOut: number }
  | { kind: "gate"; decision: Decision }
  | { kind: "order"; order: Order }
  | { kind: "saga"; orderId: string }
  | { kind: "recovery"; orderId: string; alternative: Addon };

/**
 * Omit<Turn, "id"> does not distribute over a union, so the body type is named
 * separately and the id is attached when the turn is appended.
 */
type TurnBody =
  | { role: "human"; text: string }
  | { role: "agent"; text: string; working?: boolean }
  | { role: "card"; card: Card };

type Turn = TurnBody & { id: string };

const SUGGESTIONS = [
  "restock office supplies for the month",
  "two desk lamps and a usb-c hub",
  "cables only, cheapest that work",
];

export function Checkout({
  grant,
  saga,
  orders,
  onWatchOrder,
}: {
  grant: GrantResult | null;
  saga: Record<string, SagaStep[]>;
  orders: Order[];
  onWatchOrder: (orderId: string) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [headroom, setHeadroom] = useState<Headroom | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [pendingAddon, setPendingAddon] = useState<Addon | null>(null);
  const [acceptedAddon, setAcceptedAddon] = useState<string | null>(null);

  const threadRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  /** Orders we have already surfaced a recovery offer for. */
  const offered = useRef<Set<string>>(new Set());
  const [recovering, setRecovering] = useState<string | null>(null);

  const say = useCallback((turn: TurnBody) => {
    const id = `t${seq.current++}`;
    setTurns((prev) => [...prev, { ...turn, id }]);
    return id;
  }, []);

  const replace = useCallback((id: string, turn: TurnBody) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...turn, id } : t)));
  }, []);

  // Follow the conversation. The upsell landing below the fold would lose the
  // moment the whole pitch is built on.
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const refreshHeadroom = useCallback(async () => {
    if (!grant) return null;
    try {
      const h = await gate.headroom(grant.mandate.mandate_id);
      setHeadroom(h);
      return h;
    } catch {
      return null;
    }
  }, [grant]);

  useEffect(() => {
    if (!grant) return;
    setTurns([]);
    setQuote(null);
    setAcceptedAddon(null);
    setPendingAddon(null);
    void refreshHeadroom();
    say({
      role: "agent",
      text: `I have your mandate. ${inr(
        grant.mandate.constraints.max_total_paise,
      )} across ${grant.mandate.constraints.max_count} purchases, ${grant.mandate.constraints.category_allowlist.join(
        ", ",
      )}. What do you need?`,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grant?.mandate.mandate_id]);

  /* ---------------------------------------------------------- the flow --- */

  async function runPurchase(goal: string) {
    if (!grant || busy) return;
    setBusy(true);
    setAcceptedAddon(null);
    setPendingAddon(null);
    say({ role: "human", text: goal });

    const working = say({ role: "agent", text: "Discovering the merchant", working: true });

    try {
      // 1. Cold discovery from the manifest, the way an unknown agent would.
      const manifest = await merchant.manifest();
      replace(working, {
        role: "agent",
        text: `Found ${manifest.merchant} at ${manifest.merchant_vpa}. Browsing the catalog over MCP`,
        working: true,
      });

      // 2. Search, then quote. The merchant computes every number.
      const { products } = await merchant.search(goal);
      if (products.length === 0) {
        replace(working, { role: "agent", text: "Nothing in the catalog matches that." });
        return;
      }
      const items = products.slice(0, 3).map((p) => ({ sku: p.sku, qty: 1 }));
      const q = await merchant.quote(items, grant.mandate.mandate_id);
      setQuote(q);

      replace(working, {
        role: "agent",
        text: `${q.items.length} item${q.items.length === 1 ? "" : "s"}, ${inr(
          q.total_paise,
        )} all in. The merchant priced this, not me.`,
      });
      say({ role: "card", card: { kind: "quote", quote: q } });

      const h = await refreshHeadroom();

      // 3. Headroom aware upsell. Only offers that will pass the gate.
      const { addons, filtered_out } = await merchant.suggestAddons(
        q.quote_id,
        grant.mandate.mandate_id,
      );
      if (addons.length > 0) {
        say({
          role: "agent",
          text: "The merchant offered these, and it already checked them against what you allowed.",
        });
        say({ role: "card", card: { kind: "upsell", addons, filteredOut: filtered_out } });
        setBusy(false);
        return; // Wait for the human. The bar advancing is the moment.
      }

      if (h && !h.merchant_in_scope) {
        say({ role: "agent", text: "This merchant is outside your allowlist, so I will stop here." });
        setBusy(false);
        return;
      }

      await authorizeAndOrder(q);
    } catch (e) {
      replace(working, {
        role: "agent",
        text: `I could not finish that: ${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setBusy(false);
    }
  }

  async function authorizeAndOrder(q: Quote, addon?: Addon | null) {
    if (!grant) return;
    setBusy(true);
    const working = say({ role: "agent", text: "Signing the payment and asking the gate", working: true });

    try {
      let finalQuote = q;
      if (addon) {
        // Re-quote rather than adding the addon price ourselves. The console
        // does not do arithmetic that reaches a payload.
        finalQuote = await merchant.quote(
          [...q.items.map((i) => ({ sku: i.sku, qty: i.qty })), { sku: addon.sku, qty: 1 }],
          grant.mandate.mandate_id,
        );
        setQuote(finalQuote);
        say({ role: "card", card: { kind: "quote", quote: finalQuote } });
      }

      const key = await getDeviceKey();
      const unsigned: AuthorizeRequest = {
        mandate_id: grant.mandate.mandate_id,
        quote_id: finalQuote.quote_id,
        amount_paise: finalQuote.total_paise,
        payee_vpa: grant.mandate.constraints.merchant_allowlist[0],
        nonce: newId("dec"),
        issued_at: nowRfc3339(),
        context: {
          page_excerpt: finalQuote.items.map((i) => i.name).join(", "),
          agent_reasoning: `Buying ${finalQuote.items
            .map((i) => `${i.qty}x ${i.name}`)
            .join(", ")} for: ${grant.mandate.intent}`,
        },
      };
      const signature = await signPayload(
        unsigned as unknown as Record<string, never>,
        key.privateKey,
      );

      const decision = await gate.authorize({ ...unsigned, signature });
      replace(working, { role: "agent", text: "The gate answered." });
      say({ role: "card", card: { kind: "gate", decision } });

      await refreshHeadroom();

      if (decision.verdict !== "ALLOW") {
        // A structured refusal is something to read, not something to crash on.
        say({
          role: "agent",
          text:
            decision.verdict === "STEP_UP"
              ? "This one needs your approval. Check the prompt."
              : "I will not push this through. Tell me what to change and I will requote.",
        });
        return;
      }

      const order = await merchant.createOrder(
        finalQuote.quote_id,
        decision.decision_id,
        (decision as unknown as { settlement_token?: string }).settlement_token ?? "",
      );
      onWatchOrder(order.order_id);
      say({ role: "card", card: { kind: "order", order } });
      say({ role: "card", card: { kind: "saga", orderId: order.order_id } });
      await refreshHeadroom();
    } catch (e) {
      replace(working, {
        role: "agent",
        text: `The purchase did not complete: ${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setBusy(false);
    }
  }

  /* ---------------------------------------------------- recovery flow --- */

  /**
   * The saga rolled an order back and the merchant put an alternative on the
   * table. This is the beat where a lost sale becomes a recovered one, so the
   * buyer is asked rather than told — a recovery the merchant grants itself is
   * not a recovery.
   */
  useEffect(() => {
    for (const order of orders) {
      if (!order.alternative || offered.current.has(order.order_id)) continue;
      offered.current.add(order.order_id);
      say({
        role: "agent",
        text: `That order was refunded — the item went out of stock after the payment cleared. Your ${inr(
          order.amount_paise,
        )} is already back in your budget. The merchant has something in stock that fits.`,
      });
      say({
        role: "card",
        card: { kind: "recovery", orderId: order.order_id, alternative: order.alternative },
      });
    }
  }, [orders, say]);

  async function acceptAlternative(orderId: string) {
    setRecovering(orderId);
    try {
      const recovered = await merchant.acceptAlternative(orderId);
      onWatchOrder(recovered.order_id);
      say({ role: "human", text: "Take the alternative." });
      say({ role: "card", card: { kind: "order", order: recovered } });
      say({ role: "card", card: { kind: "saga", orderId: recovered.order_id } });
      await refreshHeadroom();
    } catch (e) {
      say({
        role: "agent",
        text: `The alternative could not be placed: ${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setRecovering(null);
    }
  }

  /* -------------------------------------------------------------- view --- */

  if (!grant) {
    return (
      <div className={s.page}>
        <div className={s.noMandate}>
          <div className={s.noMandateTitle}>No mandate on this device yet</div>
          <p className="dim" style={{ maxWidth: "56ch" }}>
            Grant authority first. The agent cannot quote, let alone pay, without a signed mandate,
            and that is the point rather than a limitation.
          </p>
          <Button variant="primary" onClick={() => (window.location.hash = "#/grant")}>
            Grant authority
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={s.page}>
      <div className={s.headroom}>
        <div className={s.headroomInner}>
          <HeadroomBar
            headroom={headroom}
            totalBudgetPaise={grant.totalBudgetPaise}
            purchasePaise={quote?.total_paise ?? 0}
            addonPaise={pendingAddon?.price_paise ?? 0}
          />
        </div>
      </div>

      <div className={s.thread} ref={threadRef}>
        <div className={s.threadInner}>
          {turns.length === 0 && <Empty>Tell the agent what you need.</Empty>}

          {turns.map((t) => {
            if (t.role === "human") {
              return (
                <div key={t.id} className={`${s.turn} ${s.human} row-enter`}>
                  <span className={s.who}>You</span>
                  <div className={s.humanBubble}>{t.text}</div>
                </div>
              );
            }
            if (t.role === "agent") {
              return (
                <div key={t.id} className={`${s.turn} row-enter`}>
                  <span className={s.who}>Buyer agent</span>
                  <div className={`${s.agentText} ${t.working ? s.working : ""}`}>
                    {t.text}
                    {t.working && <span className={s.dots} />}
                  </div>
                </div>
              );
            }

            const c = t.card;
            return (
              <div key={t.id} className="row-enter">
                {c.kind === "quote" && <QuoteCard quote={c.quote} />}
                {c.kind === "upsell" && (
                  <UpsellCard
                    addons={c.addons}
                    filteredOut={c.filteredOut}
                    accepted={acceptedAddon}
                    busy={busy}
                    onAccept={(a) => {
                      setAcceptedAddon(a.sku);
                      setPendingAddon(a);
                      say({ role: "human", text: `Add the ${a.name}.` });
                      if (quote) void authorizeAndOrder(quote, a);
                    }}
                    onDecline={() => {
                      say({ role: "human", text: "No thanks." });
                      if (quote) void authorizeAndOrder(quote, null);
                    }}
                  />
                )}
                {c.kind === "gate" && <GateResult decision={c.decision} />}
                {c.kind === "order" && (
                  <div className={s.orderDone}>
                    <div className={s.orderTitle}>Paid {inr(c.order.amount_paise)}</div>
                    <div className={s.orderMeta}>
                      <span>{c.order.order_id}</span>
                      {c.order.razorpay_payment_id && <span>{c.order.razorpay_payment_id}</span>}
                      <span>{c.order.state}</span>
                    </div>
                  </div>
                )}
                {c.kind === "recovery" && (
                  <UpsellCard
                    addons={[c.alternative]}
                    onAccept={() => void acceptAlternative(c.orderId)}
                    onDecline={() =>
                      say({ role: "agent", text: "Understood. Nothing further was charged." })
                    }
                    busy={recovering === c.orderId}
                  />
                )}
                {c.kind === "saga" && (
                  <div className={s.sagaWrap}>
                    <SagaTimeline steps={saga[c.orderId] ?? []} animate />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className={s.composer}>
        {turns.length === 0 && (
          <div className={s.suggestions}>
            {SUGGESTIONS.map((sg) => (
              <button key={sg} className={s.suggestion} onClick={() => void runPurchase(sg)}>
                {sg}
              </button>
            ))}
          </div>
        )}
        <form
          className={s.composerInner}
          onSubmit={(e) => {
            e.preventDefault();
            const text = draft.trim();
            if (!text) return;
            setDraft("");
            void runPurchase(text);
          }}
        >
          <input
            className={s.input}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="What should the agent buy?"
            disabled={busy}
          />
          <Button variant="primary" type="submit" disabled={busy || !draft.trim()}>
            Send
          </Button>
        </form>
      </div>
    </div>
  );
}
