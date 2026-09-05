/**
 * The drawer. Spec 3.2 — the screen the whole product is judged on.
 *
 * Three things have to be readable in two seconds: which checks ran, which one
 * caught the problem, and why in English. Two rules carried over from the
 * console's gate trace and enforced here rather than left to the caller:
 *
 *   Never reorder. The order is the design — cheapest and most certain first.
 *   Never truncate. The skipped entries are what prove the chain short circuits.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CHECK_LABEL,
  CHECK_ORDER,
  reasonText,
  type CheckResult,
  type Decision,
} from "../../lib/contracts";
import { inr, ms, stamp } from "../../lib/money";
import { ding, thud } from "./sound";
import f from "./firewall.module.css";

/* --------------------------------------------------------------- chain ---- */

const MARK: Record<string, string> = { PASS: "✅", FAIL: "❌", STEP_UP: "⚠️", SKIPPED: "⬜" };
const CLASS: Record<string, string> = {
  PASS: f.cPass,
  FAIL: f.cFail,
  STEP_UP: f.cStep,
  SKIPPED: f.cSkip,
};

/** Contract order, with anything the engine did not report shown as SKIPPED. */
function fullChain(reported: CheckResult[]): CheckResult[] {
  const byName = new Map(reported.map((c) => [c.name, c]));
  const chain: CheckResult[] = CHECK_ORDER.map(
    (name) => byName.get(name) ?? { name, status: "SKIPPED", ms: 0 },
  );
  // A check the engine reported that is not in the frozen list is a contract
  // drift bug. Show it rather than swallowing it.
  for (const c of reported) if (!CHECK_ORDER.includes(c.name as never)) chain.push(c);
  return chain;
}

const STEP_MS = 400;

export function CheckChain({
  decision,
  replayToken,
  sound,
}: {
  decision: Decision;
  /** Bumped by the replay button. Each bump restarts the walk. */
  replayToken: number;
  sound: boolean;
}) {
  const chain = useMemo(() => fullChain(decision.checks ?? []), [decision]);
  const ran = chain.filter((c) => c.status !== "SKIPPED").length;

  // -1 means "show everything", which is the resting state. During a replay it
  // counts up and only the checks below it are revealed.
  const [upTo, setUpTo] = useState(-1);

  useEffect(() => {
    if (replayToken === 0) return;
    setUpTo(0);
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    let n = 0;
    for (let i = 0; i < chain.length; i++) {
      const c = chain[i];
      if (c.status === "SKIPPED") continue; // Never reached, never announced.
      n += 1;
      const step = n;
      timers.push(
        setTimeout(
          () => {
            setUpTo(i + 1);
            if (sound) (c.status === "FAIL" ? thud : ding)();
          },
          200 + step * STEP_MS,
        ),
      );
    }
    timers.push(setTimeout(() => setUpTo(-1), 200 + (n + 1) * STEP_MS));
    return () => timers.forEach(clearTimeout);
    // chain is derived from decision, and sound must not restart the walk.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayToken, decision.decision_id]);

  return (
    <>
      <div className={f.chain}>
        {chain.map((c, i) => {
          const hidden = upTo >= 0 && i >= upTo;
          const justIn = upTo === i + 1;
          const anim = justIn ? (c.status === "FAIL" ? f.animFail : f.animPass) : "";
          return (
            <div
              key={c.name}
              className={`${f.check} ${CLASS[c.status] ?? f.cSkip} ${hidden ? f.checkIdle : ""} ${anim}`}
            >
              <span className={f.checkMark}>{hidden ? "⬜" : (MARK[c.status] ?? "⬜")}</span>
              <span className={f.checkName}>{CHECK_LABEL[c.name] ?? c.name}</span>
              <span className={f.checkMs}>
                {c.status === "SKIPPED" || hidden ? "" : ms(c.ms)}
              </span>
              <span className={f.checkDetail}>
                {c.status === "SKIPPED"
                  ? "Skipped — blocked before reaching this check"
                  : hidden
                    ? ""
                    : (c.detail ?? "passed")}
              </span>
            </div>
          );
        })}
      </div>

      <div className={f.barLabel} style={{ marginTop: 10 }}>
        {ran} check{ran === 1 ? "" : "s"} ran, {chain.length - ran} skipped ·{" "}
        <span className={f.mono}>{ms(decision.elapsed_ms)}</span> total
      </div>
    </>
  );
}

/* --------------------------------------------------------- intent meter --- */

/**
 * Spec 3.4, with the one honest adjustment this system forces.
 *
 * The auditor answers `matches_intent` as a boolean; there is no confidence
 * number anywhere in the engine. Rather than invent a percentage to fill a
 * bar, the meter shows where the engine's actual answer lands and says on
 * screen that the answer is yes or no. The comparison underneath — what the
 * mandate asked for against what the agent was reading — is the part that
 * makes the mismatch visible either way.
 */
export function IntentMeter({ decision, intent }: { decision: Decision; intent?: string }) {
  const check = (decision.checks ?? []).find((c) => c.name === "intent");
  const status = check?.status ?? "SKIPPED";
  const injected = decision.reason_code === "INTENT_INJECTION_SUSPECTED";

  const view =
    status === "PASS"
      ? { pct: 100, cls: f.barFill, label: "✅ Matches the stated goal", colour: "" }
      : injected
        ? { pct: 100, cls: `${f.barFill} ${f.barRed}`, label: "❌ Instruction-shaped text found", colour: "" }
        : status === "STEP_UP"
          ? { pct: 0, cls: `${f.barFill} ${f.barAmber}`, label: "⚠️ No match — sent to you", colour: "" }
          : { pct: 0, cls: f.barFill, label: "⬜ Not reached", colour: "" };

  return (
    <>
      <div className={f.kicker}>📊 Intent check</div>
      <div className={f.meterHead}>
        <span className={f.meterPct}>{view.label}</span>
      </div>
      <div className={f.bar}>
        <div className={view.cls} style={{ width: `${view.pct}%` }} />
      </div>
      <div className={f.barLabel}>
        The auditor answers yes or no, not a percentage — this is where its answer landed.
        {check?.detail ? ` ${check.detail}.` : ""}
      </div>

      <dl className={f.meterCmp}>
        <dt>Mandate intent</dt>
        <dd>{intent ?? "— not signed on this device"}</dd>
        <dt>Page the agent was reading</dt>
        <dd>{decision.page_excerpt ? `${decision.page_excerpt.slice(0, 120)}…` : "— not recorded"}</dd>
      </dl>
    </>
  );
}

/* -------------------------------------------------------------- excerpt --- */

function Excerpt({ decision }: { decision: Decision }) {
  const text = decision.page_excerpt;
  if (!text) return null;
  const span = (decision.checks ?? []).find((c) => c.injected_span)?.injected_span ?? null;

  return (
    <>
      <div className={f.kicker}>📄 Page text the agent was reading</div>
      <div className={f.excerpt}>
        {span ? (
          <>
            {text.slice(0, span.start)}
            <mark className={f.injected}>{text.slice(span.start, span.end)}</mark>
            {text.slice(span.end)}
          </>
        ) : (
          text
        )}
      </div>
    </>
  );
}

/* --------------------------------------------------------------- drawer --- */

export function TransactionDrawer({
  decision,
  intent,
  agentLabel,
  sound,
  onClose,
  onCreateMandate,
}: {
  decision: Decision;
  intent?: string;
  agentLabel: string;
  sound: boolean;
  onClose: () => void;
  /** Only offered on a block: opens the wizard pre-filled from this attempt. */
  onCreateMandate?: (d: Decision) => void;
}) {
  const [replay, setReplay] = useState(0);
  const [raw, setRaw] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const banner =
    decision.verdict === "ALLOW"
      ? { cls: f.vbAllow, word: "✅ ALLOWED" }
      : decision.verdict === "BLOCK"
        ? { cls: f.vbBlock, word: "❌ BLOCKED" }
        : { cls: f.vbStep, word: "⚠️ STEP-UP" };

  const copyJson = useCallback(() => {
    void navigator.clipboard?.writeText(JSON.stringify(decision, null, 2));
  }, [decision]);

  return (
    <>
      <div className={f.scrim} onClick={onClose} />
      <aside className={f.drawer} role="dialog" aria-modal="true" aria-label="Transaction detail">
        <div className={f.drawerHead}>
          <button className={`${f.btn} ${f.small}`} onClick={onClose}>
            ✕ Close
          </button>
          <span className={`${f.mono} ${f.cardSub}`} style={{ marginLeft: "auto" }}>
            {decision.decision_id}
          </span>
        </div>

        <div className={f.drawerBody}>
          <div className={`${f.verdictBanner} ${banner.cls}`}>
            <span className={f.verdictWord}>{banner.word}</span>
            <span className={f.verdictAmount}>{inr(decision.amount_paise)}</span>
          </div>
          <div className={f.drawerSub}>
            {agentLabel} → <span className={f.mono}>{decision.payee_vpa}</span> ·{" "}
            {stamp(decision.at)}
          </div>

          <div className={f.kicker}>🔗 Check chain</div>
          <CheckChain decision={decision} replayToken={replay} sound={sound} />

          <div className={f.kicker}>💬 Verdict reason</div>
          <div className={f.reasonQuote}>
            {decision.reason_detail || reasonText(decision.reason_code)}
            <div className={`${f.mono} ${f.cardSub}`} style={{ marginTop: 6 }}>
              {decision.reason_code}
            </div>
          </div>

          <IntentMeter decision={decision} intent={intent} />

          <Excerpt decision={decision} />

          {raw && (
            <>
              <div className={f.kicker}>📜 Raw decision</div>
              <pre className={f.raw}>{JSON.stringify(decision, null, 2)}</pre>
            </>
          )}
        </div>

        <div className={f.drawerFoot}>
          <button className={`${f.btn} ${f.btnPrimary}`} onClick={() => setReplay((n) => n + 1)}>
            🔄 Replay this decision
          </button>
          <button className={f.btn} onClick={() => setRaw((v) => !v)}>
            📜 {raw ? "Hide" : "View"} raw JSON
          </button>
          <button className={f.btn} onClick={copyJson}>
            Copy
          </button>
          {decision.verdict === "BLOCK" && onCreateMandate && (
            <button
              className={`${f.btn} ${f.btnGhost}`}
              style={{ borderColor: "var(--blue)" }}
              onClick={() => onCreateMandate(decision)}
            >
              📜 Create mandate for this
            </button>
          )}
        </div>
      </aside>
    </>
  );
}
