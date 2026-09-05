/**
 * Mandates. Spec tab 2 — the table, the detail page and the templates.
 *
 * The health dot in the first column is the whole of spec 2.4: it is computed
 * from the envelope the gate already returns, so it is always current and it
 * never needs a page of its own.
 */
import { useMemo, useState } from "react";

import type { Decision } from "../../lib/contracts";
import { inr, stamp, until } from "../../lib/money";
import { useLive } from "../../lib/store";
import { effectiveHeadroom, healthFor, statusOf, type MandateStatus } from "./derive";
import { Ago, Bar, Confirm, Empty, HealthDot, SkeletonRows, StatusBadge, VerdictBadge } from "./parts";
import { useFirewall } from "./provider";
import { TEMPLATES, type StoredMandate, type Template } from "./state";
import f from "./firewall.module.css";

type SortKey = "health" | "intent" | "budget" | "expires";

/* ---------------------------------------------------------------- table --- */

export function Mandates({
  onCreate,
  onUseTemplate,
  onOpenDecision,
}: {
  onCreate: () => void;
  onUseTemplate: (t: Template) => void;
  onOpenDecision: (d: Decision) => void;
}) {
  const { mandates, headroom, ready, revoke, templateUse, agents } = useFirewall();
  const { decisions } = useLive();

  const [open, setOpen] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<"all" | MandateStatus>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "expires", dir: 1 });
  const [view, setView] = useState<"active" | "templates">("active");

  const blocksPer = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of decisions) {
      if (d.verdict !== "BLOCK") continue;
      m.set(d.mandate_id, (m.get(d.mandate_id) ?? 0) + 1);
    }
    return m;
  }, [decisions]);

  const rows = useMemo(() => {
    const decorated = mandates.map((m) => {
      const raw = headroom[m.mandate.mandate_id];
      const status = statusOf(m, raw);
      // A revoked mandate's envelope reads zero by design; the numbers that
      // describe it are the ones captured before it was revoked.
      const hr = effectiveHeadroom(m, raw);
      const health = healthFor(m, hr, blocksPer.get(m.mandate.mandate_id) ?? 0);
      const spent = hr ? Math.max(0, m.mandate.constraints.max_total_paise - hr.headroom_paise) : 0;
      return { m, hr, status, health, spent };
    });

    const filtered = decorated.filter((r) => {
      if (agentFilter !== "all" && r.m.mandate.delegate.agent_id !== agentFilter) return false;
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (query && !r.m.mandate.intent.toLowerCase().includes(query.toLowerCase())) return false;
      return true;
    });

    const cmp: Record<SortKey, (a: (typeof decorated)[number], b: (typeof decorated)[number]) => number> = {
      health: (a, b) => a.health.score - b.health.score,
      intent: (a, b) => a.m.mandate.intent.localeCompare(b.m.mandate.intent),
      budget: (a, b) => a.spent - b.spent,
      expires: (a, b) =>
        Date.parse(a.m.mandate.constraints.valid_until) -
        Date.parse(b.m.mandate.constraints.valid_until),
    };
    return filtered.sort((a, b) => cmp[sort.key](a, b) * sort.dir);
  }, [mandates, headroom, blocksPer, agentFilter, statusFilter, query, sort]);

  const detail = open ? mandates.find((m) => m.mandate.mandate_id === open) : null;

  if (detail) {
    return (
      <MandateDetail
        sm={detail}
        onBack={() => setOpen(null)}
        onRevoke={() => void revoke(detail.mandate.mandate_id, "user").then(() => setOpen(null))}
        onOpenDecision={onOpenDecision}
      />
    );
  }

  const th = (key: SortKey, label: string, width?: number) => (
    <th
      className={f.thSort}
      style={width ? { width } : undefined}
      onClick={() => setSort((s) => ({ key, dir: s.key === key && s.dir === 1 ? -1 : 1 }))}
    >
      {label} {sort.key === key ? (sort.dir === 1 ? "↑" : "↓") : ""}
    </th>
  );

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Mandates</div>
          <div className={f.pageLede}>
            Every permission slip you have signed, and how much of it is left.
          </div>
        </div>
        <div className={f.pageActions}>
          <button className={`${f.btn} ${f.btnPrimary}`} onClick={onCreate}>
            + Create mandate
          </button>
        </div>
      </div>

      <div className={f.filterBar}>
        <button
          className={`${f.filterPill} ${view === "active" ? f.fpAllOn : ""}`}
          onClick={() => setView("active")}
        >
          Active
        </button>
        <button
          className={`${f.filterPill} ${view === "templates" ? f.fpAllOn : ""}`}
          onClick={() => setView("templates")}
        >
          Templates
        </button>
      </div>

      {view === "templates" ? (
        <div className={f.agentGrid}>
          {TEMPLATES.map((t) => (
            <button key={t.id} className={`${f.agentCard} ${f.hoverLift}`} onClick={() => onUseTemplate(t)}>
              <div className={f.agentHead}>
                <span style={{ fontSize: 22 }}>{t.icon}</span>
                <span className={f.agentName}>{t.name}</span>
              </div>
              <div className={f.cardSub} style={{ textAlign: "left" }}>
                “{t.intent}”
              </div>
              <dl className={f.agentFacts}>
                <dt>Budget</dt>
                <dd>
                  {inr(t.total_paise)} · {inr(t.per_txn_paise)} each
                </dd>
                <dt>Payments</dt>
                <dd>{t.count}</dd>
                <dt>Window</dt>
                <dd>{t.hours >= 24 ? `${Math.round(t.hours / 24)} days` : `${t.hours} hours`}</dd>
                {templateUse[t.id] ? (
                  <>
                    <dt>Used</dt>
                    <dd>{templateUse[t.id]} times</dd>
                  </>
                ) : null}
              </dl>
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className={f.filterBar}>
            <select
              className={`${f.select} ${f.small}`}
              style={{ width: "auto" }}
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              aria-label="Filter by agent"
            >
              <option value="all">All agents</option>
              {agents.map((a) => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.name}
                </option>
              ))}
            </select>
            <select
              className={`${f.select} ${f.small}`}
              style={{ width: "auto" }}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              aria-label="Filter by status"
            >
              {["all", "Active", "Paused", "Expired", "Revoked", "Spent"].map((s) => (
                <option key={s} value={s}>
                  {s === "all" ? "All statuses" : s}
                </option>
              ))}
            </select>
            <input
              className={`${f.input} ${f.small}`}
              style={{ width: 240 }}
              placeholder="Search intent…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search intent"
            />
          </div>

          <div className={f.tableWrap}>
            {!ready && mandates.length > 0 ? (
              <SkeletonRows n={4} />
            ) : rows.length === 0 ? (
              <Empty icon="📜" title="No mandates yet">
                A mandate is what your agent carries instead of your card. Create one and it can
                spend exactly that much, with exactly those merchants, for exactly that long.
                <div style={{ marginTop: 14 }}>
                  <button className={`${f.btn} ${f.btnPrimary}`} onClick={onCreate}>
                    + Create mandate
                  </button>
                </div>
              </Empty>
            ) : (
              <div className={f.tableScroll}>
                <table className={f.table}>
                  <thead>
                    <tr>
                      {th("health", "", 44)}
                      {th("intent", "Intent")}
                      <th style={{ width: 140 }}>Agent</th>
                      {th("budget", "Budget", 200)}
                      {th("expires", "Expires", 120)}
                      <th style={{ width: 110 }}>Status</th>
                      <th style={{ width: 90 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ m, hr, status, health, spent }) => (
                      <tr
                        key={m.mandate.mandate_id}
                        className={f.rowClick}
                        onClick={() => setOpen(m.mandate.mandate_id)}
                      >
                        <td>
                          <HealthDot health={health} />
                        </td>
                        <td>
                          <div style={{ fontWeight: 600 }}>{m.mandate.intent}</div>
                          <div className={`${f.mono} ${f.cardSub}`}>{m.mandate.mandate_id}</div>
                        </td>
                        <td>{m.mandate.delegate.agent_id}</td>
                        <td>
                          {hr ? (
                            <Bar
                              used={spent}
                              total={m.mandate.constraints.max_total_paise}
                              label={
                                m.revoked_at
                                  ? `${inr(hr.headroom_paise)} was left when it was paused`
                                  : `${inr(spent)} of ${inr(m.mandate.constraints.max_total_paise)}`
                              }
                            />
                          ) : (
                            <span className={f.cardSub}>
                              {m.revoked_at ? "authority withdrawn" : "the gate did not answer"}
                            </span>
                          )}
                        </td>
                        <td>{until(m.mandate.constraints.valid_until)}</td>
                        <td>
                          <StatusBadge status={status} />
                        </td>
                        <td>
                          <button className={`${f.btn} ${f.small}`}>View</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- detail --- */

function MandateDetail({
  sm,
  onBack,
  onRevoke,
  onOpenDecision,
}: {
  sm: StoredMandate;
  onBack: () => void;
  onRevoke: () => void;
  onOpenDecision: (d: Decision) => void;
}) {
  const { headroom } = useFirewall();
  const { decisions } = useLive();
  const [confirming, setConfirming] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  const id = sm.mandate.mandate_id;
  const raw = headroom[id];
  const hr = effectiveHeadroom(sm, raw);
  const c = sm.mandate.constraints;
  const mine = decisions.filter((d) => d.mandate_id === id);
  const blocks = mine.filter((d) => d.verdict === "BLOCK").length;
  const health = healthFor(sm, hr, blocks);
  const status = statusOf(sm, raw);
  const spent = hr ? Math.max(0, c.max_total_paise - hr.headroom_paise) : 0;
  const used = hr ? Math.max(0, c.max_count - hr.payments_remaining) : 0;

  return (
    <div className={f.page}>
      <button className={`${f.btn} ${f.btnGhost} ${f.small}`} onClick={onBack}>
        ← Back to mandates
      </button>

      <div className={f.pageHead} style={{ marginTop: 12 }}>
        <div>
          <div className={f.pageTitle}>📜 “{sm.mandate.intent}”</div>
          <div className={f.pageLede}>
            <span className={f.mono}>{id}</span> · {sm.mandate.delegate.agent_id} · signed{" "}
            <Ago at={sm.created_at} />
            {sm.replaces && (
              <>
                {" "}
                · re-issued from <span className={f.mono}>{sm.replaces}</span> with the authority
                that was left
              </>
            )}
          </div>
        </div>
        <div className={f.pageActions}>
          <HealthDot health={health} />
          <StatusBadge status={status} />
          {status === "Active" && (
            <button
              className={`${f.btn} ${f.btnDanger}`}
              onClick={() => setConfirming(true)}
              title="The agent can no longer use this mandate. Any payment already in flight is blocked."
            >
              Revoke
            </button>
          )}
        </div>
      </div>

      <div className={f.detailGrid}>
        <div className={f.card}>
          <div className={f.cardTitle}>Constraints</div>
          <dl className={f.kv} style={{ marginTop: 14 }}>
            <dt>Merchants</dt>
            <dd className={f.mono}>{c.merchant_allowlist.join(", ") || "— none"}</dd>
            <dt>Categories</dt>
            <dd>{c.category_allowlist.join(", ") || "— none"}</dd>
            <dt>Per payment</dt>
            <dd>{inr(c.max_per_txn_paise)}</dd>
            <dt>Total</dt>
            <dd>{inr(c.max_total_paise)}</dd>
            <dt>Payments</dt>
            <dd>at most {c.max_count}</dd>
            <dt>Valid from</dt>
            <dd>{stamp(c.valid_from)}</dd>
            <dt>Valid until</dt>
            <dd>{stamp(c.valid_until)}</dd>
          </dl>
          <button
            className={`${f.btn} ${f.small}`}
            style={{ marginTop: 14 }}
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? "Hide" : "Show"} the signed mandate
          </button>
          {showRaw && <pre className={f.raw} style={{ marginTop: 10 }}>{JSON.stringify(sm.mandate, null, 2)}</pre>}
        </div>

        <div className={f.card}>
          <div className={f.cardTitle}>Budget usage</div>
          {hr ? (
            <>
              <div style={{ marginTop: 18 }}>
                <Bar
                  used={spent}
                  total={c.max_total_paise}
                  label={`${inr(spent)} of ${inr(c.max_total_paise)}`}
                />
              </div>
              {sm.revoked_at && (
                <div className={f.note} style={{ marginTop: 14 }}>
                  <span>⏸</span>
                  <span>
                    These are the numbers as they stood when this mandate was revoked. The gate
                    now reports zero on it, because it is no longer spendable.
                  </span>
                </div>
              )}
              <dl className={f.kv} style={{ marginTop: 18 }}>
                <dt>Payments</dt>
                <dd>
                  {used} of {c.max_count}
                </dd>
                <dt>Left to spend</dt>
                <dd>{inr(hr.headroom_paise)}</dd>
                <dt>Time left</dt>
                <dd>{until(c.valid_until)}</dd>
                <dt>Blocked attempts</dt>
                <dd>{blocks}</dd>
                <dt>Health</dt>
                <dd>
                  {health.word} · {health.score}/100
                </dd>
              </dl>
              <div className={f.barLabel} style={{ marginTop: 12 }}>
                Every number here is the gate's own answer, read from the headroom envelope it
                signs.
              </div>
            </>
          ) : (
            <Empty
              icon={sm.revoked_at ? "🚫" : "🔌"}
              title={sm.revoked_at ? "Authority withdrawn" : "The gate did not answer"}
            >
              {sm.revoked_at
                ? "This mandate was revoked. What was left on it at that moment was not recorded, so there is no honest number to show."
                : "Budget and payment counts are held server side. Without them this screen will not guess."}
            </Empty>
          )}
        </div>
      </div>

      <div className={f.sectionHead}>
        <div className={f.h2}>Decisions under this mandate</div>
      </div>

      <div className={f.tableWrap}>
        {mine.length === 0 ? (
          <Empty icon="🕘" title="Nothing yet">
            No payment has been attempted against this mandate.
          </Empty>
        ) : (
          <div className={f.tableScroll}>
            <table className={f.table}>
              <thead>
                <tr>
                  <th style={{ width: 130 }}>Verdict</th>
                  <th>Merchant</th>
                  <th style={{ width: 120 }}>Amount</th>
                  <th style={{ width: 140 }}>When</th>
                </tr>
              </thead>
              <tbody>
                {mine.map((d) => (
                  <tr key={d.decision_id} className={f.rowClick} onClick={() => onOpenDecision(d)}>
                    <td>
                      <VerdictBadge verdict={d.verdict} />
                    </td>
                    <td className={f.mono}>{d.payee_vpa}</td>
                    <td className={f.numCell}>{inr(d.amount_paise)}</td>
                    <td>
                      <Ago at={d.at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {confirming && (
        <Confirm
          title="Revoke this mandate?"
          danger
          confirmLabel="Revoke"
          body={
            <>
              The agent will no longer be able to use this mandate, and any payment already in
              flight against it is blocked. {inr(hr?.headroom_paise ?? 0)} of unspent authority is
              withdrawn. Revoking cannot be undone — you would sign a new mandate instead.
            </>
          }
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            onRevoke();
          }}
        />
      )}
    </div>
  );
}
