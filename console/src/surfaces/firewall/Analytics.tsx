/**
 * Analytics. Spec tab 4 — the numbers, and where each of them came from.
 *
 * The ablation matrix is not hand written. It is `eval/results/raw.json`, which
 * `python sim/run.py --all` produces by rerunning every attack with one check
 * switched off, and a cell is green because the attack *leaked* without that
 * check — not because someone decided it ought to. The same file is what the
 * repository's results.md is built from, so the screen and the write-up cannot
 * disagree.
 *
 * The latency and the spend charts are the opposite: those are this instance,
 * right now, from the decisions the gate has actually made.
 */
import { useMemo, useState } from "react";

import evidence from "../../../../eval/results/raw.json";
import { inr, ms, pct } from "../../lib/money";
import { useLive } from "../../lib/store";
import { dailyCounts, latencyPercentiles } from "./derive";
import { useLabels } from "./labels";
import { Bars, Donut, Empty, Sparkline, TimeSeries } from "./parts";
import f from "./firewall.module.css";

type AblationRow = Record<string, string>;
type Attack = {
  id: string;
  name: string;
  verdict: string;
  reason_code: string;
  blocked: boolean;
  not_applicable: boolean;
};

const ablation = evidence.ablation as unknown as Record<string, AblationRow>;
const attacks = evidence.attacks as unknown as Attack[];

/** The five checks the harness can switch off, in chain order. */
const ABLATED = [
  { key: "-replay", label: "Replay" },
  { key: "-ceiling", label: "Ceiling" },
  { key: "-scope", label: "Scope" },
  { key: "-quote", label: "Quote binding" },
  { key: "-intent", label: "Intent" },
];

/** One readable name per attack id — the family, not each variant. */
const ATTACK_NAME = new Map<string, string>();
for (const a of attacks) {
  if (!ATTACK_NAME.has(a.id)) ATTACK_NAME.set(a.id, a.name.split(" / ")[0]);
}

const CATEGORY_COLOURS = ["#6366f1", "#3b82f6", "#0ea5e9", "#14b8a6", "#8b5cf6", "#ec4899"];

export function Analytics() {
  const { decisions } = useLive();
  const labels = useLabels();
  const [hover, setHover] = useState<string | null>(null);

  /* ---- measured once, by the harness ---------------------------------- */

  const applicable = attacks.filter((a) => !a.not_applicable);
  const caught = applicable.filter((a) => a.blocked);
  const na = attacks.length - applicable.length;

  /* ---- measured now, by this instance ---------------------------------- */

  const latency = latencyPercentiles(decisions);
  const recent = decisions.slice(0, 24).map((d) => d.elapsed_ms).reverse();

  const byPayee = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of decisions) {
      if (d.verdict !== "ALLOW") continue;
      m.set(d.payee_vpa, (m.get(d.payee_vpa) ?? 0) + d.amount_paise);
    }
    return [...m.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label, value], i) => ({ label, value, colour: CATEGORY_COLOURS[i % CATEGORY_COLOURS.length] }));
  }, [decisions]);

  const byAgent = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of decisions) {
      if (d.verdict !== "ALLOW") continue;
      const name = labels.agentLabel(d.mandate_id);
      m.set(name, (m.get(name) ?? 0) + d.amount_paise);
    }
    return [...m.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label, value], i) => ({ label, value, colour: CATEGORY_COLOURS[i % CATEGORY_COLOURS.length] }));
  }, [decisions, labels]);

  const daily = useMemo(() => dailyCounts(decisions, 30), [decisions]);
  const totalSpend = byPayee.reduce((n, r) => n + r.value, 0);

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Analytics</div>
          <div className={f.pageLede}>
            What the firewall caught, what it cost, and how fast it decided.
          </div>
        </div>
      </div>

      {/* ---- 4.1 ablation matrix ------------------------------------- */}
      <div className={f.card}>
        <div className={f.cardTitle}>Which check catches which attack</div>
        <div className={f.cardSub} style={{ marginTop: 4 }}>
          Every attack was rerun with one check switched off. A cell is green because the attack
          got through without that check — so that check is what stops it.
        </div>

        <div className={f.tableScroll} style={{ marginTop: 18 }}>
          <table className={f.matrix}>
            <thead>
              <tr>
                <th className={f.rowHead}>Attack</th>
                {ABLATED.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.keys(ablation).map((id) => (
                <tr key={id}>
                  <th className={f.rowHead}>
                    {ATTACK_NAME.get(id) ?? id}
                    <div className={`${f.mono} ${f.cardSub}`}>{id}</div>
                  </th>
                  {ABLATED.map((c) => {
                    const outcome = ablation[id][c.key];
                    const cellId = `${id}:${c.key}`;
                    const caughtHere = outcome === "LEAK";
                    const notApplicable = outcome === "N/A";
                    return (
                      <td
                        key={c.key}
                        className={`${f.cell} ${caughtHere ? f.cellCaught : notApplicable ? f.cellNa : ""}`}
                        onMouseEnter={() => setHover(cellId)}
                        onMouseLeave={() => setHover(null)}
                        title={
                          notApplicable
                            ? "Not measured — this attack needs the model auditor, which has no key here."
                            : caughtHere
                              ? `Without the ${c.label.toLowerCase()} check this attack settles. It is the check that stops it.`
                              : `Switched off, the attack is still blocked — another check catches it too.`
                        }
                      >
                        {notApplicable ? "n/a" : caughtHere ? "✅" : "–"}
                        {hover === cellId && caughtHere && (
                          <span className={f.tip} style={{ bottom: "calc(100% + 4px)" }}>
                            Removing the {c.label.toLowerCase()} check lets this attack through. It
                            is the layer that stops it.
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className={f.barLabel} style={{ marginTop: 14 }}>
          Generated {new Date(evidence.generated).toLocaleString("en-IN")} by{" "}
          <span className={f.mono}>python sim/run.py --all</span>. The signature and freshness
          checks are not in this table because the harness cannot switch them off — without them
          there is no request to test.
        </div>
      </div>

      {/* ---- 4.2 key metrics ----------------------------------------- */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Key metrics</div>
      </div>

      <div className={f.row3}>
        <div className={`${f.card} ${f.hoverLift}`}>
          <div className={f.statLabel}>Attacks blocked</div>
          <div className={f.statValue} style={{ color: "var(--green-ink)" }}>
            {caught.length}/{applicable.length}
          </div>
          <div className={f.cardSub}>
            {na > 0 && `${na} variant${na === 1 ? "" : "s"} not measured — they need the model `}
            {na > 0 && "auditor, which has no key in this build."}
          </div>
        </div>

        <div className={`${f.card} ${f.hoverLift}`}>
          <div className={f.statLabel}>False block rate, benign traffic</div>
          <div className={f.statValue} style={{ color: "var(--green-ink)" }}>
            {pct(evidence.benign_false_positive_rate, 1)}
          </div>
          <div className={f.cardSub}>
            Across {evidence.sessions_per_arm} sessions per arm, {evidence.seeds} seeds.
          </div>
        </div>

        <div className={`${f.card} ${f.hoverLift}`}>
          <div className={f.statLabel}>Gate latency, this instance</div>
          <div className={f.statValue} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span>{latency.n ? ms(latency.p50) : "—"}</span>
            <Sparkline values={recent} />
          </div>
          <div className={f.cardSub}>
            {latency.n
              ? `p50 ${ms(latency.p50)} · p95 ${ms(latency.p95)} over ${latency.n} decisions`
              : "No decision has been made yet on this instance."}
          </div>
        </div>
      </div>

      {/* ---- 4.3 spend breakdown ------------------------------------- */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Where the money went</div>
        <span className={f.cardSub}>Approved payments on this instance</span>
      </div>

      <div className={f.chartRow}>
        <div className={f.card}>
          <div className={f.cardTitle}>By merchant</div>
          {byPayee.length === 0 ? (
            <Empty icon="🥧" title="Nothing approved yet" />
          ) : (
            <Donut slices={byPayee} centre={inr(totalSpend)} />
          )}
        </div>

        <div className={f.card}>
          <div className={f.cardTitle}>By agent</div>
          {byAgent.length === 0 ? (
            <Empty icon="📊" title="Nothing approved yet" />
          ) : (
            <Bars rows={byAgent} />
          )}
        </div>
      </div>

      {/* ---- 4.4 activity over time ---------------------------------- */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Activity, last 30 days</div>
      </div>

      <div className={f.card}>
        <TimeSeries rows={daily} />
        <div className={f.legend}>
          <span className={f.legendItem}>
            <span className={f.swatch} style={{ background: "#10b981" }} /> allowed
          </span>
          <span className={f.legendItem}>
            <span className={f.swatch} style={{ background: "#ef4444" }} /> blocked
          </span>
        </div>
      </div>
    </div>
  );
}
