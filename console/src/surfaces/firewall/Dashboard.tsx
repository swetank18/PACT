/**
 * The command centre. Spec tab 1.
 *
 * One question answered above the fold: is everything okay? Six things on
 * screen and no more — four numbers, whatever needs a person, what was
 * prevented, and the live feed underneath.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { Decision } from "../../lib/contracts";
import { inr, inrPlain } from "../../lib/money";
import { useLive } from "../../lib/store";
import { statusOf, threatSummary } from "./derive";
import { useLabels } from "./labels";
import { Ago, Empty, SkeletonRows, StatCard, Switch, VerdictBadge } from "./parts";
import { useFirewall } from "./provider";
import { StepUpCard, secondsLeft } from "./StepUpCard";
import { ding } from "./sound";
import type { Tab } from "./nav";
import f from "./firewall.module.css";

const FEED_LIMIT = 20;

function startOfToday(): number {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

type Alert = {
  id: string;
  tone: "red" | "amber" | "blue";
  icon: string;
  text: string;
  at: string;
  go: Tab;
};

export function Dashboard({
  onGo,
  onOpenDecision,
}: {
  onGo: (tab: Tab) => void;
  onOpenDecision: (d: Decision) => void;
}) {
  const { decisions, gateConn } = useLive();
  const { mandates, headroom, prefs, setPrefs, ready, agents, dismissed, dismiss } = useFirewall();
  const labels = useLabels();

  const [resolved, setResolved] = useState<Set<string>>(new Set());

  const today = startOfToday();
  const loading = !ready && decisions.length === 0 && gateConn !== "live";

  const spendToday = decisions
    .filter((d) => d.verdict === "ALLOW" && Date.parse(d.at) >= today)
    .reduce((n, d) => n + d.amount_paise, 0);

  const blocksToday = decisions.filter(
    (d) => d.verdict === "BLOCK" && Date.parse(d.at) >= today,
  ).length;

  const activeMandates = mandates.filter(
    (m) => statusOf(m, headroom[m.mandate.mandate_id]) === "Active",
  );

  const pending = useMemo(
    () => decisions.filter((d) => d.verdict === "STEP_UP" && !resolved.has(d.decision_id)),
    [decisions, resolved],
  );

  /* ------------------------------------------------------------ sound --- */

  // Ding on a block, if the human asked for it. Only ever on a block that
  // arrived after this screen opened — replaying history out loud on mount
  // would be noise.
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (seen.current === null) {
      seen.current = new Set(decisions.map((d) => d.decision_id));
      return;
    }
    for (const d of decisions) {
      if (seen.current.has(d.decision_id)) continue;
      seen.current.add(d.decision_id);
      if (d.verdict === "BLOCK" && prefs.sound_on_block) ding();
    }
  }, [decisions, prefs.sound_on_block]);

  /* ----------------------------------------------------------- alerts --- */

  const alerts = useMemo<Alert[]>(() => {
    const out: Alert[] = [];
    const hourAgo = Date.now() - 3600_000;

    const recentBlocks = decisions.filter(
      (d) => d.verdict === "BLOCK" && Date.parse(d.at) >= hourAgo,
    );
    if (recentBlocks.length > 0) {
      out.push({
        id: `blocks:${recentBlocks.length}:${recentBlocks[0].decision_id}`,
        tone: "red",
        icon: "🔴",
        text: `${recentBlocks.length} blocked payment attempt${recentBlocks.length === 1 ? "" : "s"} in the last hour`,
        at: recentBlocks[0].at,
        go: "transactions",
      });
    }

    for (const m of activeMandates) {
      const until = Date.parse(m.mandate.constraints.valid_until);
      if (until - Date.now() < 2 * 3600_000) {
        out.push({
          id: `expiry:${m.mandate.mandate_id}`,
          tone: "amber",
          icon: "🟡",
          text: `Mandate “${m.mandate.intent}” expires soon`,
          at: m.mandate.constraints.valid_until,
          go: "mandates",
        });
      }
    }

    for (const a of agents) {
      if (Date.now() - Date.parse(a.connected_at) < 24 * 3600_000) {
        out.push({
          id: `agent:${a.agent_id}`,
          tone: "blue",
          icon: "🔵",
          text: `Agent “${a.name}” is connected`,
          at: a.connected_at,
          go: "agents",
        });
      }
    }

    return out.filter((a) => !dismissed.includes(a.id));
  }, [decisions, activeMandates, agents, dismissed]);

  /* ----------------------------------------------------------- threat --- */

  const threat = threatSummary(decisions, 7);

  /* ----------------------------------------------------------- render --- */

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Dashboard</div>
          <div className={f.pageLede}>
            Everything your agents did, and everything the gate refused on your behalf.
          </div>
        </div>
      </div>

      <div className={f.statBar}>
        <StatCard
          loading={loading}
          tone="blue"
          icon="💰"
          value={`₹${inrPlain(spendToday)}`}
          label="Spent today"
        />
        <StatCard
          loading={loading}
          tone="green"
          icon="📜"
          value={activeMandates.length}
          label="Active mandates"
        />
        <StatCard
          loading={loading}
          tone="amber"
          icon="⏳"
          value={pending.length}
          label="Pending step-ups"
          pulse={pending.length > 0}
        />
        <StatCard
          loading={loading}
          tone="red"
          icon="🛡️"
          value={blocksToday}
          label="Blocked today"
        />
      </div>

      {alerts.length > 0 && (
        <div className={f.alerts}>
          {alerts.slice(0, 3).map((a) => (
            <div
              key={a.id}
              className={`${f.alert} ${a.tone === "red" ? f.alertRed : a.tone === "amber" ? f.alertAmber : f.alertBlue}`}
            >
              <span>{a.icon}</span>
              <button
                className={f.alertText}
                style={{ border: 0, background: "none", font: "inherit", textAlign: "left", cursor: "pointer" }}
                onClick={() => onGo(a.go)}
              >
                {a.text}
              </button>
              <Ago at={a.at} className={f.alertTime} />
              <button className={f.alertX} aria-label="Dismiss" onClick={() => dismiss(a.id)}>
                ✕
              </button>
            </div>
          ))}
          {alerts.length > 3 && (
            <button className={`${f.btn} ${f.btnGhost} ${f.small}`} onClick={() => onGo("transactions")}>
              View all {alerts.length} alerts →
            </button>
          )}
        </div>
      )}

      {/* ---- threat summary ------------------------------------------- */}
      <div className={f.threat}>
        <div>
          <div className={f.threatHead}>🛡️ Threat summary — last 7 days</div>
          {threat.blocks === 0 ? (
            <div className={f.threatCalm}>
              No threats detected. Your agents are operating normally. ✅
            </div>
          ) : (
            <>
              <div className={f.threatBig}>₹{inrPlain(threat.prevented_paise)}</div>
              <div className={f.threatBigLabel}>
                of unauthorised spend the gate refused to settle
              </div>
            </>
          )}
        </div>

        <div className={f.threatLines}>
          <span className={f.threatN}>{threat.injections}</span>
          <span>prompt injection attempts caught</span>
          <span className={f.threatN}>{threat.replays}</span>
          <span>replayed requests refused</span>
          <span className={f.threatN}>{threat.step_ups}</span>
          <span>payments sent to you to decide</span>
          {threat.top_payee && (
            <>
              <span className={f.threatN}>{threat.top_payee.n}×</span>
              <span>
                most refused payee <span className={f.mono}>{threat.top_payee.vpa}</span>
              </span>
            </>
          )}
          {threat.window && (
            <>
              <span className={f.threatN}>{threat.window.n}</span>
              <span>
                busiest window {String(threat.window.from).padStart(2, "0")}:00 –{" "}
                {String(threat.window.to).padStart(2, "0")}:00
              </span>
            </>
          )}
        </div>
      </div>

      {/* ---- step ups -------------------------------------------------- */}
      {pending.length > 0 && (
        <>
          <div className={f.sectionHead}>
            <div className={f.h2}>Waiting for you</div>
          </div>
          <div className={f.stepUpRow}>
            {pending
              .slice()
              .sort((a, b) => secondsLeft(a) - secondsLeft(b))
              .slice(0, 3)
              .map((d) => (
                <StepUpCard
                  key={d.decision_id}
                  decision={d}
                  agentLabel={labels.agentLabel(d.mandate_id)}
                  intent={labels.intentFor(d.mandate_id)}
                  autoApprovePaise={prefs.auto_approve_paise}
                  onResolved={(id) => setResolved((s) => new Set(s).add(id))}
                  onOpenDetail={() => onOpenDecision(d)}
                />
              ))}
            {pending.length > 3 && (
              <button className={`${f.btn} ${f.btnGhost}`} onClick={() => onGo("transactions")}>
                +{pending.length - 3} more →
              </button>
            )}
          </div>
        </>
      )}

      {/* ---- live feed -------------------------------------------------- */}
      <div className={f.feedCard}>
        <div className={f.feedHead}>
          <span className={f.cardTitle}>Live activity</span>
          <span className={f.cardSub}>
            {gateConn === "live" ? "streaming" : gateConn === "retrying" ? "reconnecting" : "connecting"}
          </span>
          <span className={f.cardSub} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            🔔
            <Switch
              on={prefs.sound_on_block}
              label="Sound on a blocked payment"
              onChange={(v) => setPrefs({ ...prefs, sound_on_block: v })}
            />
          </span>
        </div>

        <div className={f.feedList}>
          {loading ? (
            <SkeletonRows n={5} />
          ) : decisions.length === 0 ? (
            <Empty icon="🛡️" title="No activity yet">
              Once your agent starts transacting, every decision the gate makes appears here in
              real time.
            </Empty>
          ) : (
            decisions.slice(0, FEED_LIMIT).map((d, i) => (
              <button
                key={d.decision_id}
                className={`${f.feedRow} ${i === 0 ? f.enter : ""}`}
                onClick={() => onOpenDecision(d)}
              >
                <VerdictBadge verdict={d.verdict} />
                <span
                  className={`${f.feedAgent} ${labels.isKnown(d.mandate_id) ? "" : f.unknownAgent}`}
                  title={
                    labels.isKnown(d.mandate_id)
                      ? undefined
                      : `Mandate ${d.mandate_id}, signed on another device — this one cannot name the agent behind it`
                  }
                >
                  {labels.agentLabel(d.mandate_id)}
                </span>
                <span className={f.feedMerchant}>→ {d.payee_vpa}</span>
                <span className={f.feedAmount}>{inr(d.amount_paise)}</span>
                <Ago at={d.at} className={f.feedTime} />
                <span className={f.feedChevron}>›</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
