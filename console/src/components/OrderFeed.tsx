/**
 * The live order feed. LANE-C section 4, region two.
 *
 * One row per order with its saga state. Rows are clickable into the timeline,
 * which is the audit trail the brief explicitly asks to see.
 */
import { Fragment, useEffect, useState } from "react";

import type { Order, SagaStep } from "../lib/contracts";
import { clock, inr, shortId } from "../lib/money";
import { SagaTimeline } from "./SagaTimeline";
import { Empty, Pill } from "./ui";
import s from "./Feeds.module.css";

/** State to colour. Only three semantic colours exist, so most states are neutral. */
function stateClass(state: string): string {
  if (state === "FULFILLED" || state === "RECOVERED") return s.stateOk;
  if (state === "ROLLED_BACK" || state === "NEEDS_ATTENTION") return s.stateFail;
  if (
    state === "ROLLING_BACK" ||
    state === "REFUND_ISSUED" ||
    state === "BUDGET_RELEASED" ||
    state === "ALTERNATIVE_OFFERED"
  ) {
    return s.stateWork;
  }
  return s.stateIdle;
}

export function OrderFeed({
  orders,
  saga,
  onSelect,
  resetToken,
}: {
  orders: Order[];
  saga: Record<string, SagaStep[]>;
  onSelect: (orderId: string) => void;
  resetToken?: number;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => setOpenId(null), [resetToken]);

  const toggle = (orderId: string) => {
    const next = openId === orderId ? null : orderId;
    setOpenId(next);
    if (next) onSelect(next);
  };

  if (orders.length === 0) {
    return <Empty>No orders yet. Press 1 to run a purchase.</Empty>;
  }

  return (
    <div className={s.scroll}>
      <table className={s.table}>
        <thead>
          <tr>
            <th style={{ width: 34 }} />
            <th style={{ width: 88 }}>Time</th>
            <th style={{ width: 160 }}>Order</th>
            <th>Items</th>
            <th style={{ width: 190 }}>State</th>
            <th style={{ width: 130 }} className={s.amount}>
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => {
            const open = openId === o.order_id;
            return (
              <Fragment key={o.order_id}>
                <tr
                  className={`${s.rowBtn} ${open ? s.selected : ""} row-enter`}
                  onClick={() => toggle(o.order_id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle(o.order_id);
                    }
                  }}
                >
                  <td>
                    <span className={s.caret}>{open ? "▾" : "▸"}</span>
                  </td>
                  <td className={s.at}>{clock(o.at)}</td>
                  <td className={s.vpa}>
                    {shortId(o.order_id, 6)}
                    {o.recovered_from && <span className={s.recovered}> recovered</span>}
                  </td>
                  <td className={s.items} title={o.items_summary}>
                    {o.items_summary}
                  </td>
                  <td>
                    <span className={`${s.state} ${stateClass(String(o.state))}`}>{o.state}</span>
                    {o.state === "NEEDS_ATTENTION" && (
                      <>
                        {" "}
                        <Pill tone="veto">compensation failed</Pill>
                      </>
                    )}
                  </td>
                  <td className={s.amount}>{inr(o.amount_paise)}</td>
                </tr>
                {open && (
                  <tr>
                    <td className={s.expand} colSpan={6}>
                      <SagaTimeline steps={saga[o.order_id] ?? []} animate />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
