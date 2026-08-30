/**
 * Surface three: the merchant console. LANE-C section 4.
 *
 * What a Razorpay judge looks at. Four regions, in the order they matter:
 * revenue strip, live order feed, decision feed, audit trail.
 *
 * The audit trail is a rail rather than a modal because during the failure demo
 * it has to be on screen at the same time as the order feed — the audience
 * needs to see the order row change state and the compensating steps arrive
 * together.
 */
import { useEffect, useMemo, useState } from "react";

import { DecisionFeed } from "../../components/DecisionFeed";
import { OrderFeed } from "../../components/OrderFeed";
import { RevenueStrip } from "../../components/RevenueStrip";
import { SagaTimeline } from "../../components/SagaTimeline";
import { ConnDot, Pill } from "../../components/ui";
import { clock, inr, shortId } from "../../lib/money";
import { useLive } from "../../lib/store";
import s from "./Console.module.css";

export function Console() {
  const {
    decisions,
    orders,
    saga,
    stats,
    gateConn,
    merchantConn,
    loadSaga,
    resetToken,
  } = useLive();

  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => setSelected(null), [resetToken]);

  // Follow the newest order until a human picks one, so the rail is never
  // empty and the failure demo needs no clicking.
  useEffect(() => {
    if (selected || orders.length === 0) return;
    const first = orders[0].order_id;
    setSelected(first);
    void loadSaga(first);
  }, [orders, selected, loadSaga]);

  const selectedOrder = useMemo(
    () => orders.find((o) => o.order_id === selected) ?? null,
    [orders, selected],
  );

  const needsAttention = useMemo(
    () => orders.filter((o) => o.state === "NEEDS_ATTENTION"),
    [orders],
  );

  const select = (orderId: string) => {
    setSelected(orderId);
    void loadSaga(orderId);
  };

  return (
    <div className={s.page}>
      <div style={{ display: "grid", gap: "var(--s4)" }}>
        <RevenueStrip stats={stats} />
        {needsAttention.length > 0 && (
          <div className={s.attention}>
            <Pill tone="veto">Needs attention</Pill>
            <span className={s.attentionText}>
              {needsAttention.length} order{needsAttention.length === 1 ? "" : "s"} parked after a
              compensation failed. Money is not lost silently — it is here, waiting for a human.
            </span>
          </div>
        )}
      </div>

      <div className={s.grid}>
        <div className={s.left}>
          <section className={s.panel}>
            <header className={s.panelHead}>
              <span className={s.panelTitle}>Live orders</span>
              <span className={s.panelMeta}>
                <span>{orders.length} shown</span>
                <ConnDot state={merchantConn} label="merchant" />
              </span>
            </header>
            <div className={s.panelBody}>
              <OrderFeed
                orders={orders}
                saga={saga}
                onSelect={select}
                resetToken={resetToken}
              />
            </div>
          </section>

          <section className={s.panel}>
            <header className={s.panelHead}>
              <span className={s.panelTitle}>Gate decisions</span>
              <span className={s.panelMeta}>
                <span>{decisions.length} shown</span>
                <ConnDot state={gateConn} label="gate" />
              </span>
            </header>
            <div className={s.panelBody}>
              <DecisionFeed decisions={decisions} resetToken={resetToken} />
            </div>
          </section>
        </div>

        <section className={s.panel}>
          <header className={s.panelHead}>
            <span className={s.panelTitle}>Audit trail</span>
            <span className={s.panelMeta}>
              {selectedOrder ? `${(saga[selectedOrder.order_id] ?? []).length} steps` : "no order"}
            </span>
          </header>

          <div className={s.panelBody}>
            {selectedOrder ? (
              <>
                <div className={s.auditHead}>
                  <div className={s.auditOrder}>{selectedOrder.order_id}</div>
                  <div className={s.auditMeta}>
                    <span className={s.auditAmount}>{inr(selectedOrder.amount_paise)}</span>
                    <span>{selectedOrder.items_summary}</span>
                    <span className="mono">{clock(selectedOrder.at)}</span>
                  </div>
                  <div className={s.auditMeta}>
                    <span className="mono">
                      mandate {shortId(selectedOrder.mandate_id)}
                    </span>
                    {selectedOrder.rail_payment_id && (
                      <span className="mono">{selectedOrder.rail_payment_id}</span>
                    )}
                    {selectedOrder.recovered_from && (
                      <Pill tone="allow">recovered from {shortId(selectedOrder.recovered_from)}</Pill>
                    )}
                  </div>
                </div>
                <SagaTimeline steps={saga[selectedOrder.order_id] ?? []} animate />
              </>
            ) : (
              <div className={s.hint}>Pick an order to see every step it went through.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
