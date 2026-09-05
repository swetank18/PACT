/**
 * The principal's firewall. The shell: sidebar, the six tabs, and the one
 * control that is on screen no matter where you are.
 *
 * The kill switch lives here rather than inside settings for a reason worth
 * keeping: it is the answer to "what do I do if this goes wrong", and an
 * answer you have to go looking for is not one. It is a button with a
 * confirmation, not a toggle, because a toggle can be brushed.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import type { Decision } from "../../lib/contracts";
import { inr } from "../../lib/money";
import { useLive } from "../../lib/store";
import { Agents } from "./Agents";
import { Analytics } from "./Analytics";
import { Dashboard } from "./Dashboard";
import { statusOf } from "./derive";
import { useLabels } from "./labels";
import { Mandates } from "./Mandates";
import { MandateWizard, type WizardSeed } from "./MandateWizard";
import { isTab, TABS, type Tab } from "./nav";
import { Confirm } from "./parts";
import { FirewallProvider, useFirewall } from "./provider";
import { Settings } from "./Settings";
import { Transactions } from "./Transactions";
import { TransactionDrawer } from "./TransactionDrawer";
import f from "./firewall.module.css";

/* ---------------------------------------------------------------- hash ---- */

function readTab(): Tab {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/");
  const candidate = parts[1] ?? "";
  return isTab(candidate) ? candidate : "dashboard";
}

function useTab(): [Tab, (t: Tab) => void] {
  const [tab, setTab] = useState<Tab>(readTab);
  useEffect(() => {
    const onHash = () => setTab(readTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [
    tab,
    (t: Tab) => {
      window.location.hash = `#/firewall/${t}`;
      setTab(t);
    },
  ];
}

/* --------------------------------------------------------------- shell ---- */

function Shell({ onExit }: { onExit: () => void }) {
  const [tab, go] = useTab();
  const [drawer, setDrawer] = useState<Decision | null>(null);
  const [wizard, setWizard] = useState<WizardSeed | null>(null);
  const [txView, setTxView] = useState<"all" | "pending">("all");
  const [confirmKill, setConfirmKill] = useState<"engage" | "release" | null>(null);
  const [killBusy, setKillBusy] = useState(false);
  const [killNote, setKillNote] = useState<string | null>(null);

  const { decisions } = useLive();
  const { kill, engageKill, releaseKill, mandates, headroom, prefs } = useFirewall();
  const labels = useLabels();

  const pendingCount = decisions.filter((d) => d.verdict === "STEP_UP").length;

  const liveMandates = useMemo(
    () => mandates.filter((m) => statusOf(m, headroom[m.mandate.mandate_id]) === "Active"),
    [mandates, headroom],
  );

  const unspent = liveMandates.reduce(
    (n, m) => n + (headroom[m.mandate.mandate_id]?.headroom_paise ?? 0),
    0,
  );

  const openDecision = useCallback((d: Decision) => setDrawer(d), []);

  const runKill = useCallback(async () => {
    setKillBusy(true);
    try {
      if (confirmKill === "engage") {
        const { revoked, failed } = await engageKill();
        setKillNote(
          failed > 0
            ? `Paused ${revoked}, ${failed} did not answer — those are still live.`
            : `Paused ${revoked} mandate${revoked === 1 ? "" : "s"}.`,
        );
      } else {
        const { reissued, failed } = await releaseKill();
        setKillNote(
          failed > 0
            ? `Re-issued ${reissued}, ${failed} failed.`
            : `Re-issued ${reissued} mandate${reissued === 1 ? "" : "s"} with what was left.`,
        );
      }
    } finally {
      setKillBusy(false);
      setConfirmKill(null);
    }
  }, [confirmKill, engageKill, releaseKill]);

  const body = wizard ? (
    <MandateWizard
      seed={wizard}
      onCancel={() => setWizard(null)}
      onDone={() => {
        setWizard(null);
        go("mandates");
      }}
    />
  ) : tab === "dashboard" ? (
    <Dashboard
      onGo={(t) => {
        if (t === "transactions") setTxView("pending");
        go(t);
      }}
      onOpenDecision={openDecision}
    />
  ) : tab === "mandates" ? (
    <Mandates
      onCreate={() => setWizard({})}
      onUseTemplate={(t) => setWizard({ template: t })}
      onOpenDecision={openDecision}
    />
  ) : tab === "transactions" ? (
    <Transactions onOpenDecision={openDecision} initialView={txView} />
  ) : tab === "analytics" ? (
    <Analytics />
  ) : tab === "agents" ? (
    <Agents onOpenDecision={openDecision} />
  ) : (
    <Settings />
  );

  return (
    <div className={f.root}>
      <nav className={f.side}>
        <div className={f.logo}>
          <span className={f.logoMark}>🛡️</span>
          <span className={f.logoText}>
            FIREWALL
            <span className={f.logoSub}>PACT</span>
          </span>
        </div>

        {TABS.map((t) => (
          <button
            key={t.id}
            className={`${f.navItem} ${tab === t.id && !wizard ? f.navOn : ""}`}
            onClick={() => {
              setWizard(null);
              if (t.id === "transactions") setTxView("all");
              go(t.id);
            }}
            aria-current={tab === t.id ? "page" : undefined}
          >
            <span className={f.navIcon}>{t.icon}</span>
            <span className={f.navLabel}>{t.label}</span>
            {t.id === "transactions" && pendingCount > 0 && (
              <span className={f.navCount}>{pendingCount}</span>
            )}
          </button>
        ))}

        <span className={f.sideSpacer} />

        <div className={f.sideRule} />

        <button
          className={`${f.kill} ${kill.engaged ? f.killOn : ""}`}
          onClick={() => setConfirmKill(kill.engaged ? "release" : "engage")}
          title={
            kill.engaged
              ? "Re-issue every paused mandate with the authority that was left"
              : "Revoke every live mandate at the gate"
          }
        >
          {/* The glyph is outside the label so the control is still legible
              when the sidebar collapses to icons. */}
          <span className={f.killIcon}>{kill.engaged ? "▶" : "⏹"}</span>
          <span className={f.killText}>
            {kill.engaged ? " RESUME ALL" : " PAUSE ALL AGENTS"}
          </span>
        </button>
        <div className={f.killNote}>
          {killNote ??
            (kill.engaged
              ? "Everything is paused. The gate is refusing on every mandate."
              : `${liveMandates.length} live mandate${liveMandates.length === 1 ? "" : "s"}, ${inr(unspent)} unspent`)}
        </div>

        <button className={f.who} onClick={() => go("settings")}>
          <span className={f.avatar}>
            {prefs.display_name.slice(0, 2).toUpperCase()}
          </span>
          <span className={f.whoText}>
            <span className={f.whoName}>{prefs.display_name}</span>
            <span className={f.whoSub}>{prefs.vpa}</span>
          </span>
        </button>

        <button className={f.backLink} onClick={onExit}>
          ← merchant console
        </button>
      </nav>

      <main className={f.main}>{body}</main>

      <div className={f.tooSmall}>
        <div className={f.tooSmallTitle}>🛡️ Firewall</div>
        <div>Please use a laptop or desktop. This console is not built for small screens.</div>
      </div>

      {drawer && (
        <TransactionDrawer
          decision={drawer}
          intent={labels.intentFor(drawer.mandate_id)}
          agentLabel={labels.agentLabel(drawer.mandate_id)}
          sound={prefs.sound_on_block}
          onClose={() => setDrawer(null)}
          onCreateMandate={(d) => {
            setDrawer(null);
            setWizard({ fromBlock: d });
          }}
        />
      )}

      {confirmKill && (
        <Confirm
          danger={confirmKill === "engage"}
          busy={killBusy}
          title={confirmKill === "engage" ? "Pause all agents?" : "Resume all agents?"}
          confirmLabel={confirmKill === "engage" ? "Pause everything" : "Resume"}
          body={
            confirmKill === "engage" ? (
              <>
                Every live mandate is revoked at the gate — {liveMandates.length} of them, holding{" "}
                {inr(unspent)} of unspent authority. Any payment in flight is refused. There is no
                pause in the mandate lifecycle, so this is a real revocation; what was left on each
                mandate is recorded first so resuming can re-issue exactly that much.
              </>
            ) : (
              <>
                Each paused mandate is re-signed on this device with the authority that was left
                when it was paused — not what it started with. Anything already spent stays spent,
                and a mandate whose window has closed is not re-issued.
              </>
            )
          }
          onCancel={() => setConfirmKill(null)}
          onConfirm={() => void runKill()}
        />
      )}
    </div>
  );
}

export function Firewall({ onExit }: { onExit: () => void }) {
  return (
    <FirewallProvider>
      <Shell onExit={onExit} />
    </FirewallProvider>
  );
}
