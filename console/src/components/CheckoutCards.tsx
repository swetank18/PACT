/**
 * The three inline cards in the conversational checkout. LANE-C section 3.
 *
 * Quote card, upsell card, gate result. Structured cards rather than walls of
 * text, because the buyer is reading a purchase, not a chat log.
 */
import { useEffect, useState } from "react";

import type { Addon, Decision, Quote } from "../lib/contracts";
import { reasonText } from "../lib/contracts";
import { inr, ms, until } from "../lib/money";
import { GateTrace } from "./GateTrace";
import { Button, Pill, VerdictPill } from "./ui";
import s from "./CheckoutCards.module.css";

/* --------------------------------------------------------------- quote ---- */

/** Ticks once a second so the TTL on screen is the real one, not a stale render. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function QuoteCard({ quote }: { quote: Quote }) {
  const now = useNow();
  const left = new Date(quote.expires_at).getTime() - now;
  const expiring = left < 60_000;

  return (
    <div className={s.card}>
      <div className={s.cardHead}>
        <span className={s.cardTitle}>Quote · {quote.quote_id}</span>
        <span className={`${s.ttl} ${expiring ? s.ttlWarn : ""}`}>
          {left > 0 ? `expires in ${until(quote.expires_at, now)}` : "expired"}
        </span>
      </div>

      <table className={s.lines}>
        <tbody>
          {quote.items.map((it) => (
            <tr key={it.sku}>
              <td className={s.qty}>{it.qty}×</td>
              <td>
                {it.name}
                <span className={s.sku}>
                  {it.sku} · {it.category}
                </span>
              </td>
              <td className={s.lineAmt}>{inr(it.line_total_paise)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className={s.totals}>
        <div className={s.totalRow}>
          <span>Subtotal</span>
          <span className="num">{inr(quote.subtotal_paise)}</span>
        </div>
        <div className={s.totalRow}>
          <span>Tax</span>
          <span className="num">{inr(quote.tax_paise)}</span>
        </div>
        <div className={s.totalRow}>
          <span>Shipping</span>
          <span className="num">{inr(quote.shipping_paise)}</span>
        </div>
        <div className={s.grand}>
          <span className={s.grandLabel}>Total</span>
          <span className={s.grandValue}>{inr(quote.total_paise)}</span>
        </div>
      </div>

      {/* Section 5 of the shared contract, made visible. The model never does
          arithmetic that reaches a payload. */}
      <div className={s.provenance}>
        Prices computed by the merchant, not by the assistant. Every figure came from the quote
        engine and the gate refuses any payment that does not match this total.
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- upsell ---- */

export function UpsellCard({
  addons,
  filteredOut,
  onAccept,
  onDecline,
  accepted,
  busy,
}: {
  addons: Addon[];
  filteredOut?: number;
  onAccept: (addon: Addon) => void;
  onDecline: () => void;
  accepted?: string | null;
  busy?: boolean;
}) {
  if (addons.length === 0) return null;

  return (
    <div className={s.card}>
      <div className={s.cardHead}>
        <span className={s.cardTitle}>Also available</span>
        <Pill tone="accent">fits remaining authority</Pill>
      </div>

      {addons.map((a) => (
        <div key={a.sku} className={s.addon}>
          <div>
            <div className={s.addonName}>{a.name}</div>
            <div className={s.addonMeta}>
              {a.reason ?? `Commonly bought with this order`} · {a.category}
            </div>
          </div>
          <div className={s.addonActions}>
            <span className={s.addonPrice}>{inr(a.price_paise)}</span>
            {accepted === a.sku ? (
              <Pill tone="allow">added</Pill>
            ) : (
              <Button variant="primary" onClick={() => onAccept(a)} disabled={busy}>
                Add
              </Button>
            )}
          </div>
        </div>
      ))}

      {/* The growth claim, stated where the audience is already looking. */}
      <div className={s.fits}>
        Every item here was checked against the buyer's remaining authority before it was offered,
        so the gate will approve it.
      </div>

      {filteredOut != null && filteredOut > 0 && (
        <div className={s.filtered}>
          {filteredOut} other item{filteredOut === 1 ? " was" : "s were"} withheld because they would
          not have been approved.
        </div>
      )}

      {!accepted && (
        <div className={s.filtered}>
          <Button variant="quiet" onClick={onDecline} disabled={busy}>
            No thanks
          </Button>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------- gate result ---- */

export function GateResult({ decision }: { decision: Decision }) {
  const [open, setOpen] = useState(decision.verdict !== "ALLOW");

  const tone =
    decision.verdict === "ALLOW"
      ? s.resultAllow
      : decision.verdict === "BLOCK"
        ? s.resultVeto
        : s.resultStepup;

  return (
    <div className={`${s.result} ${tone}`}>
      <div className={s.resultHead}>
        <VerdictPill verdict={decision.verdict} />
        <span className={s.resultReason}>
          {decision.reason_detail || reasonText(decision.reason_code)}
        </span>
      </div>

      <div className={s.resultMeta}>
        <span>{decision.reason_code}</span>
        <span>{ms(decision.elapsed_ms)}</span>
        <span>{decision.decision_id}</span>
        <button className={s.traceToggle} onClick={() => setOpen((v) => !v)}>
          {open ? "hide the checks" : "show the checks"}
        </button>
      </div>

      {open && <GateTrace decision={decision} />}
    </div>
  );
}
