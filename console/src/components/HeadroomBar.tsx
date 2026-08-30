/**
 * The headroom bar. LANE-C section 3.
 *
 * One horizontal bar: budget already spent, this purchase, the addon on offer,
 * and what is left. The moment the pitch is built around is the upsell segment
 * appearing inside the bar with room to spare, so the addon gets its own
 * segment rather than being folded into the purchase.
 *
 * Everything here is derived from the signed headroom envelope. The console
 * never computes a price; it divides paise by a hundred to draw a rectangle.
 */
import { inr } from "../lib/money";
import type { Headroom, Paise } from "../lib/contracts";
import s from "./HeadroomBar.module.css";

type Props = {
  headroom: Headroom | null;
  /** Total budget, only known when the human granted it on this device. */
  totalBudgetPaise?: Paise | null;
  /** The quote currently on the table. */
  purchasePaise?: Paise;
  /** The addon being offered, drawn as its own segment on top of the purchase. */
  addonPaise?: Paise;
  compact?: boolean;
};

export function HeadroomBar({
  headroom,
  totalBudgetPaise,
  purchasePaise = 0,
  addonPaise = 0,
  compact = false,
}: Props) {
  if (!headroom) {
    return (
      <div className={s.wrap}>
        <div className={s.head}>
          <span className={s.title}>Remaining authority</span>
        </div>
        <div className={s.track} />
        <div className={s.meta}>No mandate yet. Grant authority to see the envelope.</div>
      </div>
    );
  }

  const remaining = headroom.headroom_paise;
  // The merchant only ever sees remaining headroom — the total budget is
  // deliberately absent from the envelope. When the human granted the mandate
  // on this device we know the total and can draw the spent portion; when we
  // are rendering the merchant's view we cannot, and the bar is scaled to
  // remaining authority instead. Both are honest, and the difference is the
  // privacy argument made visible.
  const total = totalBudgetPaise ?? remaining;
  const used = Math.max(0, total - remaining);

  const committed = Math.min(purchasePaise, remaining);
  const addon = Math.max(0, Math.min(addonPaise, remaining - committed));
  const free = Math.max(0, remaining - committed - addon);
  const overflow = purchasePaise + addonPaise - remaining;

  const w = (paise: number) => `${total > 0 ? (paise / total) * 100 : 0}%`;
  const capPct =
    total > 0 && headroom.max_per_txn_paise < total
      ? Math.min(100, ((used + headroom.max_per_txn_paise) / total) * 100)
      : null;

  const afterThis = remaining - committed - addon;

  return (
    <div className={s.wrap}>
      <div className={s.head}>
        <span className={s.title}>Remaining authority</span>
        <span className={`${s.remaining} num`}>{inr(afterThis)}</span>
      </div>

      <div className={capPct !== null ? s.capRow : undefined}>
        <div
          className={s.track}
          role="img"
          aria-label={`${inr(used)} spent, ${inr(committed)} this purchase, ${inr(
            addon,
          )} addon, ${inr(free)} remaining`}
        >
          <div className={`${s.seg} ${s.used}`} style={{ width: w(used) }} />
          <div className={`${s.seg} ${s.pending}`} style={{ width: w(committed) }} />
          <div className={`${s.seg} ${s.addon}`} style={{ width: w(addon) }} />
          <div className={`${s.seg} ${s.free}`} style={{ width: w(free) }} />
          {capPct !== null && <div className={s.cap} style={{ left: `${capPct}%` }} />}
        </div>
        {capPct !== null && (
          <div className={s.capLabel} style={{ left: `${capPct}%` }}>
            per transaction cap {inr(headroom.max_per_txn_paise)}
          </div>
        )}
      </div>

      {!compact && (
        <>
          <div className={s.legend}>
            {totalBudgetPaise != null && (
              <span className={s.legendItem}>
                <span className={`${s.swatch} ${s.used}`} />
                spent <span className={`${s.value} num`}>{inr(used)}</span>
              </span>
            )}
            {committed > 0 && (
              <span className={s.legendItem}>
                <span className={s.swatch} style={{ background: "var(--accent)" }} />
                this purchase <span className={`${s.value} num`}>{inr(committed)}</span>
              </span>
            )}
            {addon > 0 && (
              <span className={s.legendItem}>
                <span className={s.swatch} style={{ background: "var(--accent-dim)" }} />
                addon <span className={`${s.value} num`}>{inr(addon)}</span>
              </span>
            )}
            <span className={s.legendItem}>
              <span className={s.swatch} style={{ background: "var(--surface-3)" }} />
              remaining after <span className={`${s.value} num`}>{inr(afterThis)}</span>
            </span>
          </div>

          <div className={s.meta}>
            <span>
              {headroom.payments_remaining} purchase
              {headroom.payments_remaining === 1 ? "" : "s"} left
            </span>
            <span className="mono">{headroom.categories_allowed.join(" · ")}</span>
            {overflow > 0 && (
              <span className={s.overflow}>over by {inr(overflow)} — the gate would refuse this</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
