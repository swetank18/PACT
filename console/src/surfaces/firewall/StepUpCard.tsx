/**
 * A payment waiting for a person. Spec 1.5 and 3.6.
 *
 * Approving re-signs this one payment on this device. It does not raise the
 * mandate's limits and it does not hand the agent anything it could have
 * produced itself — the gate verifies the approval against the delegator key
 * on the mandate and checks the signed object names this decision.
 *
 * The countdown is derived from the decision's own timestamp rather than from
 * when this card mounted, so reloading the page does not buy the agent another
 * two minutes.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { gate } from "../../lib/api";
import { reasonText, type Decision } from "../../lib/contracts";
import { signPayload } from "../../lib/crypto";
import { getDeviceKey } from "../../lib/device";
import { inr, nowRfc3339 } from "../../lib/money";
import { Countdown } from "./parts";
import f from "./firewall.module.css";

/** How long a step up stands before silence is treated as a refusal. */
export const STEP_UP_TTL_S = 120;

export function secondsLeft(d: Decision, now = Date.now()): number {
  const t = Date.parse(d.at);
  if (Number.isNaN(t)) return STEP_UP_TTL_S;
  return Math.max(0, STEP_UP_TTL_S - Math.floor((now - t) / 1000));
}

export async function answerStepUp(decision: Decision, approve: boolean): Promise<void> {
  let approval: Record<string, string | number> | undefined;
  let signature: string | undefined;
  if (approve) {
    const key = await getDeviceKey();
    approval = {
      decision_id: decision.decision_id,
      mandate_id: decision.mandate_id,
      amount_paise: decision.amount_paise,
      payee_vpa: decision.payee_vpa,
      approved_at: nowRfc3339(),
    };
    signature = await signPayload(approval, key.privateKey);
  }
  await gate.resolveStepUp(decision.decision_id, approve, approval, signature);
}

export function StepUpCard({
  decision,
  agentLabel,
  intent,
  wide,
  autoApprovePaise,
  onResolved,
  onOpenDetail,
}: {
  decision: Decision;
  agentLabel: string;
  intent?: string;
  wide?: boolean;
  /** This screen's own threshold. The gate does not know about it. */
  autoApprovePaise: number;
  onResolved: (id: string, approved: boolean) => void;
  onOpenDetail?: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [auto, setAuto] = useState<"threshold" | "timeout" | null>(null);

  const left = useMemo(() => secondsLeft(decision), [decision]);
  const wasLive = useMemo(() => left > 0, [left]);

  const resolve = useCallback(
    async (approve: boolean, reason: "threshold" | "timeout" | null = null) => {
      setBusy(approve ? "approve" : "deny");
      setError(null);
      try {
        await answerStepUp(decision, approve);
        setAuto(reason);
        setGone(true);
        onResolved(decision.decision_id, approve);
      } catch (e) {
        // Fail closed: if the gate could not be told, do not pretend it was.
        setError(e instanceof Error ? e.message : String(e));
        setBusy(null);
      }
    },
    [decision, onResolved],
  );

  // Below the threshold the human set, this screen answers on their behalf —
  // still with their device signature, and still labelled as automatic.
  useEffect(() => {
    if (autoApprovePaise > 0 && decision.amount_paise <= autoApprovePaise && !busy && !gone) {
      void resolve(true, "threshold");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decision.decision_id]);

  const onZero = useCallback(() => {
    // Only for a step up this screen actually watched run out. One that had
    // already expired before the page opened is left for the human to answer,
    // rather than sending a refusal nobody was present for.
    if (wasLive && !busy && !gone) void resolve(false, "timeout");
  }, [wasLive, busy, gone, resolve]);

  if (gone) {
    return (
      <div className={`${f.stepUp} ${wide ? f.stepUpWide : ""} ${f.stepUpExpired}`}>
        <div className={f.stepUpHead}>
          {auto === "threshold"
            ? `Auto-approved · under your ${inr(autoApprovePaise)} threshold`
            : auto === "timeout"
              ? "Refused · no answer in time"
              : busy === "approve"
                ? "Approved"
                : "Refused"}
        </div>
      </div>
    );
  }

  return (
    <div className={`${f.stepUp} ${wide ? f.stepUpWide : ""}`}>
      <div className={f.stepUpHead}>
        <span>⚠️ Step-up required</span>
        {left > 0 ? <Countdown seconds={left} onZero={onZero} /> : <span className={f.timer}>expired</span>}
      </div>

      <div className={f.stepUpBody}>
        <div>
          <strong>{agentLabel}</strong> wants to pay
        </div>
        <div className={f.stepUpAmount}>{inr(decision.amount_paise)}</div>
        <div className={f.stepUpMeta}>
          to <span className={f.mono}>{decision.payee_vpa}</span>
        </div>
        {intent && <div className={f.stepUpMeta}>Intent: “{intent}”</div>}
        <div className={f.stepUpMeta}>
          Trigger: {decision.reason_detail || reasonText(decision.reason_code)}
        </div>
        {onOpenDetail && (
          <button className={`${f.btn} ${f.btnGhost} ${f.small}`} style={{ marginTop: 8, padding: "4px 0" }} onClick={onOpenDetail}>
            View the full check chain →
          </button>
        )}
      </div>

      {error && (
        <div className={f.errorBox} style={{ marginBottom: 12 }}>
          <span>⚠️</span>
          <span>Could not reach the gate: {error}</span>
        </div>
      )}

      <div className={f.stepUpActions}>
        <button
          className={`${f.btn} ${f.btnDanger}`}
          onClick={() => void resolve(false)}
          disabled={busy !== null}
        >
          {busy === "deny" ? "Refusing…" : "❌ Deny"}
        </button>
        <button
          className={`${f.btn} ${f.btnGreen}`}
          onClick={() => void resolve(true)}
          disabled={busy !== null}
        >
          {busy === "approve" ? "Signing…" : "✅ Approve"}
        </button>
      </div>
    </div>
  );
}
