/**
 * Transactions. Spec tab 3 — every decision the gate has made, and the queue
 * of the ones waiting for a person.
 *
 * The verdict filters are the verdict badges: filtering to blocks tints the
 * page red, filtering to approvals tints it green. It is the cheapest possible
 * way to make "what am I looking at" answerable without reading a word.
 */
import { useEffect, useMemo, useState } from "react";

import type { Decision } from "../../lib/contracts";
import { reasonText } from "../../lib/contracts";
import { inr, ms, rupeesToPaise } from "../../lib/money";
import { useLive } from "../../lib/store";
import { useLabels } from "./labels";
import { Ago, Empty, SkeletonRows, VerdictBadge } from "./parts";
import { useFirewall } from "./provider";
import { StepUpCard, secondsLeft } from "./StepUpCard";
import f from "./firewall.module.css";

const PAGE = 25;

type VerdictFilter = "ALL" | "ALLOW" | "BLOCK" | "STEP_UP";

export function Transactions({
  onOpenDecision,
  initialView,
}: {
  onOpenDecision: (d: Decision) => void;
  initialView?: "all" | "pending";
}) {
  const { decisions, gateConn, refetchAll } = useLive();
  const { prefs } = useFirewall();
  const labels = useLabels();

  const [view, setView] = useState<"all" | "pending">(initialView ?? "all");
  const [verdict, setVerdict] = useState<VerdictFilter>("ALL");
  const [agentQuery, setAgentQuery] = useState("all");
  const [merchantQuery, setMerchantQuery] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [since, setSince] = useState("");
  const [page, setPage] = useState(0);
  const [resolved, setResolved] = useState<Set<string>>(new Set());

  useEffect(() => setPage(0), [verdict, agentQuery, merchantQuery, min, max, since]);

  const agentNames = useMemo(
    () => [...new Set(decisions.map((d) => labels.agentLabel(d.mandate_id)))].sort(),
    [decisions, labels],
  );

  const pending = useMemo(
    () => decisions.filter((d) => d.verdict === "STEP_UP" && !resolved.has(d.decision_id)),
    [decisions, resolved],
  );

  const filtered = useMemo(() => {
    const minP = min ? rupeesToPaise(min) : null;
    const maxP = max ? rupeesToPaise(max) : null;
    const sinceT = since ? new Date(since).getTime() : null;

    return decisions.filter((d) => {
      if (verdict !== "ALL" && d.verdict !== verdict) return false;
      if (agentQuery !== "all" && labels.agentLabel(d.mandate_id) !== agentQuery) return false;
      if (merchantQuery && !d.payee_vpa.toLowerCase().includes(merchantQuery.toLowerCase()))
        return false;
      if (minP !== null && d.amount_paise < minP) return false;
      if (maxP !== null && d.amount_paise > maxP) return false;
      if (sinceT !== null && Date.parse(d.at) < sinceT) return false;
      return true;
    });
  }, [decisions, verdict, agentQuery, merchantQuery, min, max, since, labels]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const rows = filtered.slice(page * PAGE, page * PAGE + PAGE);

  const tint =
    verdict === "ALLOW"
      ? f.tintAllow
      : verdict === "BLOCK"
        ? f.tintBlock
        : verdict === "STEP_UP"
          ? f.tintStep
          : "";

  const pill = (v: VerdictFilter, label: string, on: string, off: string) => (
    <button
      className={`${f.filterPill} ${verdict === v ? on : off}`}
      onClick={() => setVerdict(v)}
    >
      {label}
    </button>
  );

  return (
    <div className={`${f.page} ${tint}`}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Transactions</div>
          <div className={f.pageLede}>
            Every decision the gate has made, and why. Click any row for the check chain.
          </div>
        </div>
      </div>

      <div className={f.filterBar}>
        <button
          className={`${f.filterPill} ${view === "all" ? f.fpAllOn : ""}`}
          onClick={() => setView("all")}
        >
          All transactions
        </button>
        <button
          className={`${f.filterPill} ${view === "pending" ? f.fpAllOn : ""}`}
          onClick={() => setView("pending")}
        >
          Pending approvals {pending.length > 0 && `· ${pending.length}`}
        </button>
      </div>

      {view === "pending" ? (
        pending.length === 0 ? (
          <div className={f.tableWrap}>
            <Empty icon="✅" title="Nothing is waiting for you">
              When the gate cannot decide on its own, the payment appears here with the check that
              raised it.
            </Empty>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {pending
              .slice()
              .sort((a, b) => secondsLeft(a) - secondsLeft(b))
              .map((d) => (
                <StepUpCard
                  key={d.decision_id}
                  wide
                  decision={d}
                  agentLabel={labels.agentLabel(d.mandate_id)}
                  intent={labels.intentFor(d.mandate_id)}
                  autoApprovePaise={prefs.auto_approve_paise}
                  onResolved={(id) => {
                    setResolved((s) => new Set(s).add(id));
                    void refetchAll();
                  }}
                  onOpenDetail={() => onOpenDecision(d)}
                />
              ))}
          </div>
        )
      ) : (
        <>
          <div className={f.filterBar}>
            {pill("ALL", "All", f.fpAllOn, "")}
            {pill("ALLOW", "Allowed", f.fpAllowOn, f.fpAllowOff)}
            {pill("BLOCK", "Blocked", f.fpBlockOn, f.fpBlockOff)}
            {pill("STEP_UP", "Step-up", f.fpStepOn, f.fpStepOff)}

            <select
              className={`${f.select} ${f.small}`}
              style={{ width: "auto" }}
              value={agentQuery}
              onChange={(e) => setAgentQuery(e.target.value)}
              aria-label="Filter by agent"
            >
              <option value="all">All agents</option>
              {agentNames.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>

            <input
              className={`${f.input} ${f.small}`}
              style={{ width: 200 }}
              placeholder="Merchant VPA…"
              value={merchantQuery}
              onChange={(e) => setMerchantQuery(e.target.value)}
              aria-label="Search merchant"
            />
            <input
              className={`${f.input} ${f.small}`}
              style={{ width: 100 }}
              placeholder="min ₹"
              inputMode="decimal"
              value={min}
              onChange={(e) => setMin(e.target.value)}
              aria-label="Minimum amount"
            />
            <input
              className={`${f.input} ${f.small}`}
              style={{ width: 100 }}
              placeholder="max ₹"
              inputMode="decimal"
              value={max}
              onChange={(e) => setMax(e.target.value)}
              aria-label="Maximum amount"
            />
            <input
              className={`${f.input} ${f.small}`}
              style={{ width: 170 }}
              type="date"
              value={since}
              onChange={(e) => setSince(e.target.value)}
              aria-label="On or after"
            />
            {(merchantQuery || min || max || since || agentQuery !== "all") && (
              <button
                className={`${f.btn} ${f.small}`}
                onClick={() => {
                  setMerchantQuery("");
                  setMin("");
                  setMax("");
                  setSince("");
                  setAgentQuery("all");
                }}
              >
                Clear
              </button>
            )}
          </div>

          <div className={f.tableWrap}>
            {decisions.length === 0 && gateConn !== "live" ? (
              <SkeletonRows n={6} />
            ) : rows.length === 0 ? (
              <Empty icon="🔍" title="Nothing matches">
                {decisions.length === 0
                  ? "No decision has been made yet."
                  : "Widen the filters to see more."}
              </Empty>
            ) : (
              <>
                <div className={f.tableScroll}>
                  <table className={f.table}>
                    <thead>
                      <tr>
                        <th style={{ width: 130 }}>Verdict</th>
                        <th style={{ width: 150 }}>Agent</th>
                        <th>Merchant</th>
                        <th style={{ width: 110 }}>Amount</th>
                        <th style={{ width: 210 }}>Why</th>
                        <th style={{ width: 90 }}>Gate</th>
                        <th style={{ width: 130 }}>When</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((d) => (
                        <tr
                          key={d.decision_id}
                          className={f.rowClick}
                          onClick={() => onOpenDecision(d)}
                        >
                          <td>
                            <VerdictBadge verdict={d.verdict} />
                          </td>
                          <td
                            className={labels.isKnown(d.mandate_id) ? "" : f.unknownAgent}
                            title={
                              labels.isKnown(d.mandate_id)
                                ? undefined
                                : `Mandate ${d.mandate_id}, signed on another device — this one cannot name the agent behind it`
                            }
                          >
                            {labels.agentLabel(d.mandate_id)}
                          </td>
                          <td className={f.mono}>{d.payee_vpa}</td>
                          <td className={f.numCell}>{inr(d.amount_paise)}</td>
                          <td className={f.cardSub}>{reasonText(d.reason_code)}</td>
                          <td className={`${f.mono} ${f.numCell}`}>{ms(d.elapsed_ms)}</td>
                          <td>
                            <Ago at={d.at} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className={f.pager}>
                  <span>
                    {filtered.length} decision{filtered.length === 1 ? "" : "s"} · page {page + 1} of{" "}
                    {pages}
                  </span>
                  <button
                    className={`${f.btn} ${f.small}`}
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    ← Previous
                  </button>
                  <button
                    className={`${f.btn} ${f.small}`}
                    disabled={page >= pages - 1}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next →
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
