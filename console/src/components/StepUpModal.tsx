/**
 * The step up modal. LANE-C section 0, hours 18 to 22.
 *
 * A step up is the whole reason the gate is a conversion instrument rather than
 * a wall. Arm C blocks a legitimate sale outright; we ask the human and recover
 * it. So this dialog is written to be answered in two seconds: what is being
 * bought, from whom, for how much, and why it needs a person.
 *
 * Approving re-signs the request on this device. The agent still never holds
 * the key — it holds a second signature it could not have produced itself.
 */
import { useEffect, useState } from "react";

import { gate } from "../lib/api";
import type { Decision } from "../lib/contracts";
import { reasonText } from "../lib/contracts";
import { signPayload } from "../lib/crypto";
import { getDeviceKey } from "../lib/device";
import { inr, nowRfc3339 } from "../lib/money";
import { Button, Pill } from "./ui";
import s from "./StepUpModal.module.css";

export function StepUpModal({
  decision,
  onResolved,
}: {
  decision: Decision;
  onResolved: (approved: boolean) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "refuse" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Escape refuses. A step up left hanging is a blocked sale, and the safe
  // default when the human walks away is not to spend their money.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") void resolve(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decision.decision_id]);

  async function resolve(approve: boolean) {
    if (busy) return;
    setBusy(approve ? "approve" : "refuse");
    setError(null);
    try {
      let signature: string | undefined;
      if (approve) {
        // The human's own signature over the specific decision, so the engine
        // can prove the approval came from the device and not from the agent.
        const key = await getDeviceKey();
        signature = await signPayload(
          {
            decision_id: decision.decision_id,
            mandate_id: decision.mandate_id,
            amount_paise: decision.amount_paise,
            payee_vpa: decision.payee_vpa,
            approved_at: nowRfc3339(),
          },
          key.privateKey,
        );
      }
      await gate.resolveStepUp(decision.decision_id, approve, signature);
      onResolved(approve);
    } catch (e) {
      // Fail closed. If we cannot tell the gate what the human said, we do not
      // pretend the purchase went through.
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  return (
    <div className={s.backdrop} role="dialog" aria-modal="true" aria-label="Approval needed">
      <div className={s.dialog}>
        <div className={s.head}>
          <Pill tone="stepup">Needs you</Pill>
          <span className={s.title}>Your agent is asking permission</span>
        </div>

        <div className={s.body}>
          <div className={s.reason}>
            {decision.reason_detail || reasonText(decision.reason_code)}
          </div>

          <div className={s.facts}>
            <span className={s.factLabel}>Amount</span>
            <span className={`${s.amount}`}>{inr(decision.amount_paise)}</span>

            <span className={s.factLabel}>Paying</span>
            <span className={s.factValue}>{decision.payee_vpa}</span>

            <span className={s.factLabel}>Reason code</span>
            <span className={s.factValue}>{decision.reason_code}</span>

            <span className={s.factLabel}>Decision</span>
            <span className={s.factValue}>{decision.decision_id}</span>
          </div>

          <div className={s.note}>
            Approving signs this one payment on this device. It does not raise the mandate's limits
            and it does not give the agent your key.
          </div>

          {error && <div className="is-veto">Could not reach the gate: {error}</div>}
        </div>

        <div className={s.actions}>
          <Button variant="quiet" onClick={() => void resolve(false)} disabled={busy !== null}>
            {busy === "refuse" ? "Refusing…" : "Not this time"}
          </Button>
          <Button
            className={s.approve}
            onClick={() => void resolve(true)}
            disabled={busy !== null}
            autoFocus
          >
            {busy === "approve" ? "Signing…" : `Approve ${inr(decision.amount_paise)}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
