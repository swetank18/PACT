/**
 * The rollback timeline. LANE-C section 5, moment two.
 *
 * "Let it draw one row at a time with a short delay. Do not animate it all at
 * once. The audience needs to watch the money come back and then watch the sale
 * get saved."
 *
 * So this component does not render the array it is given. It reveals it, one
 * row per tick, and holds a longer beat on the rows that carry the argument —
 * the fulfilment failure, the refund, the budget coming back. When steps arrive
 * live over SSE the reveal is already ahead of them and it behaves like a
 * normal feed; when the timeline is opened on a finished order it replays.
 */
import { useEffect, useRef, useState } from "react";

import type { SagaStep } from "../lib/contracts";
import { clock } from "../lib/money";
import s from "./SagaTimeline.module.css";

/** Rows the audience needs a moment to read. */
const HOLD_LONGER = new Set([
  "FULFILMENT",
  "FULFILLMENT",
  "ROLLING_BACK",
  "REFUND_ISSUED",
  "BUDGET_RELEASED",
  "RECOVERED",
]);

const STEP_MS = 420;
const HOLD_MS = 750;

const ROLLBACK_STATES = new Set([
  "ROLLING_BACK",
  "REFUND_ISSUED",
  "BUDGET_RELEASED",
  "ROLLED_BACK",
  "ALTERNATIVE_OFFERED",
]);

function outcomeClass(step: SagaStep): string {
  if (step.outcome === "FAIL") return s.fail;
  if (step.outcome === "PENDING") return s.pending;
  if (step.state === "RECOVERED") return s.recovered;
  if (ROLLBACK_STATES.has(String(step.state))) return `${s.ok} ${s.rollback}`;
  return s.ok;
}

const MARK: Record<string, string> = { OK: "✓", FAIL: "✗", PENDING: "…" };

export function SagaTimeline({
  steps,
  /** Off for the audit trail's static reading, on for the demo beat. */
  animate = true,
}: {
  steps: SagaStep[];
  animate?: boolean;
}) {
  const [shown, setShown] = useState(animate ? 0 : steps.length);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [replayToken, setReplayToken] = useState(0);

  useEffect(() => {
    if (!animate) {
      setShown(steps.length);
      return;
    }
    if (shown >= steps.length) return;

    const next = steps[shown];
    const delay = HOLD_LONGER.has(String(next?.state)) || next?.outcome === "FAIL" ? HOLD_MS : STEP_MS;
    timer.current = setTimeout(() => setShown((n) => n + 1), delay);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [shown, steps, animate, replayToken]);

  // A different order was selected. Start its reveal from the top.
  const orderId = steps[0]?.order_id;
  useEffect(() => {
    if (animate) setShown(0);
  }, [orderId, animate, replayToken]);

  if (steps.length === 0) {
    return (
      <div className={s.wrap}>
        <div className={s.empty}>No saga steps recorded for this order yet.</div>
      </div>
    );
  }

  const visible = steps.slice(0, shown);

  return (
    <div className={s.wrap}>
      <div className={s.head}>
        <span className={s.title}>Audit trail — saga steps</span>
        {animate && (
          <button
            className={s.replay}
            onClick={() => {
              setShown(0);
              setReplayToken((n) => n + 1);
            }}
          >
            replay
          </button>
        )}
      </div>

      <div className={s.list}>
        {visible.map((step) => (
          <div
            key={`${step.order_id}:${step.seq}`}
            className={`${s.row} ${outcomeClass(step)} row-enter`}
          >
            <span className={s.at}>{clock(step.at)}</span>
            <span className={s.dot} />
            <span className={s.state}>{step.state}</span>
            <span className={s.detail}>
              {step.reason_code && (
                // Shown for the same reason the gate trace shows codes rather
                // than sentences: the audience is being asked to believe there
                // is a contract underneath, and prose alone does not show one.
                <span className={s.code}>{step.reason_code}</span>
              )}
              {step.detail}
              {step.ref && <span className={s.ref}> {step.ref}</span>}
            </span>
            <span className={s.outcome}>{MARK[step.outcome] ?? ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
