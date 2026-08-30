/**
 * The decision feed. LANE-C section 4, region three.
 *
 * One row per gate decision: verdict, payee, amount, plain language reason,
 * elapsed milliseconds. Rows expand into the ordered check list, which is the
 * first of the two moments that carry the pitch.
 */
import { Fragment, useEffect, useState } from "react";

import type { Decision } from "../lib/contracts";
import { reasonText } from "../lib/contracts";
import { clock, inr, ms } from "../lib/money";
import { GateTrace } from "./GateTrace";
import { Empty, VerdictPill } from "./ui";
import s from "./Feeds.module.css";

export function DecisionFeed({
  decisions,
  /** Cleared when the server resets so a stale expansion cannot survive. */
  resetToken,
}: {
  decisions: Decision[];
  resetToken?: number;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => setOpenId(null), [resetToken]);

  if (decisions.length === 0) {
    return <Empty>No decisions yet. Press 1 to run a purchase.</Empty>;
  }

  return (
    <div className={s.scroll}>
      <table className={s.table}>
        <thead>
          <tr>
            <th style={{ width: 34 }} />
            <th style={{ width: 88 }}>Time</th>
            <th style={{ width: 116 }}>Verdict</th>
            <th>Payee</th>
            <th style={{ width: 130 }} className={s.amount}>
              Amount
            </th>
            <th>Reason</th>
            <th style={{ width: 92 }} className={s.ms}>
              Elapsed
            </th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => {
            const open = openId === d.decision_id;
            return (
              <Fragment key={d.decision_id}>
                <tr
                  className={`${s.rowBtn} ${open ? s.selected : ""} row-enter`}
                  onClick={() => setOpenId(open ? null : d.decision_id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setOpenId(open ? null : d.decision_id);
                    }
                  }}
                >
                  <td>
                    <span className={s.caret}>{open ? "▾" : "▸"}</span>
                  </td>
                  <td className={s.at}>{clock(d.at)}</td>
                  <td>
                    <VerdictPill verdict={d.verdict} />
                  </td>
                  <td className={s.vpa}>{d.payee_vpa}</td>
                  <td className={s.amount}>{inr(d.amount_paise)}</td>
                  <td className={s.reason}>
                    {d.reason_detail || reasonText(d.reason_code)}
                    <span className={s.reasonCode}>{d.reason_code}</span>
                  </td>
                  <td className={s.ms}>{ms(d.elapsed_ms)}</td>
                </tr>
                {open && (
                  <tr>
                    <td className={s.expand} colSpan={7}>
                      <GateTrace decision={d} />
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
