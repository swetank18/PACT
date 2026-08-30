/**
 * The gate trace. LANE-C section 5, moment one.
 *
 * Expand a decision into the ordered check list. Two rules that are not
 * negotiable and are enforced here rather than left to the caller:
 *
 *   Never reorder. The order is the design — cheapest and most certain first.
 *   Never truncate. Skipped entries prove the chain short circuits.
 *
 * If the engine sends checks in a different order, or omits the ones it never
 * reached, this component still renders the full chain in contract order with
 * the unreached ones marked SKIPPED. The audience sees the same ten lines every
 * time, which is what makes the failing line findable in two seconds.
 */
import { CHECK_LABEL, CHECK_ORDER, type CheckResult, type Decision } from "../lib/contracts";
import { ms } from "../lib/money";
import s from "./GateTrace.module.css";

const MARK: Record<string, string> = {
  PASS: "✓",
  FAIL: "✗",
  STEP_UP: "!",
  SKIPPED: "–",
};

const CLASS: Record<string, string> = {
  PASS: s.pass,
  FAIL: s.fail,
  STEP_UP: s.stepup,
  SKIPPED: s.skipped,
};

/** Contract order, with anything the engine did not report shown as SKIPPED. */
function fullChain(reported: CheckResult[]): CheckResult[] {
  const byName = new Map(reported.map((c) => [c.name, c]));
  const chain: CheckResult[] = CHECK_ORDER.map(
    (name) => byName.get(name) ?? { name, status: "SKIPPED", ms: 0 },
  );
  // A check the engine reported that is not in the frozen list is a contract
  // drift bug. Show it at the end rather than swallowing it.
  for (const c of reported) {
    if (!CHECK_ORDER.includes(c.name as never)) chain.push(c);
  }
  return chain;
}

function Excerpt({ decision }: { decision: Decision }) {
  const text = decision.page_excerpt;
  if (!text) return null;
  const span = decision.checks.find((c) => c.injected_span)?.injected_span ?? null;

  return (
    <div className={s.excerpt}>
      <div className={s.excerptLabel}>Page text the agent was reading</div>
      {span ? (
        <>
          {text.slice(0, span.start)}
          <mark className={s.injected}>{text.slice(span.start, span.end)}</mark>
          {text.slice(span.end)}
        </>
      ) : (
        text
      )}
    </div>
  );
}

export function GateTrace({ decision }: { decision: Decision }) {
  const chain = fullChain(decision.checks ?? []);
  const total = chain.reduce((sum, c) => sum + (c.ms || 0), 0);
  const ran = chain.filter((c) => c.status !== "SKIPPED").length;

  return (
    <div className={s.wrap}>
      <div className={s.list}>
        {chain.map((c) => (
          <div key={c.name} className={`${s.row} ${CLASS[c.status] ?? s.skipped}`}>
            <span className={s.mark}>{MARK[c.status] ?? "–"}</span>
            <span className={s.name}>{CHECK_LABEL[c.name as never] ?? c.name}</span>
            <span className={s.status}>{c.status}</span>
            <span className={s.ms}>{c.status === "SKIPPED" ? "" : ms(c.ms)}</span>
            {c.detail && <span className={s.detail}>{c.detail}</span>}
          </div>
        ))}
      </div>

      <div className={s.footer}>
        <span>
          {ran} check{ran === 1 ? "" : "s"} ran, {chain.length - ran} skipped
        </span>
        <span className="mono num">{ms(decision.elapsed_ms || total)} total</span>
        <span className="mono">{decision.decision_id}</span>
      </div>

      <Excerpt decision={decision} />
    </div>
  );
}
