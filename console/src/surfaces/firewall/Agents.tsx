/**
 * Agents. Spec tab 5.
 *
 * Pausing and revoking are not labels held in this browser. Both revoke the
 * agent's live mandates at the gate, because a control that only exists on the
 * principal's screen is not a control — a compromised agent does not run it.
 * The difference between the two is what happens next: a pause records what
 * was left so it can be re-issued, a revoke does not.
 */
import { useMemo, useState } from "react";

import { gate } from "../../lib/api";
import type { Decision } from "../../lib/contracts";
import { inr } from "../../lib/money";
import { useLive } from "../../lib/store";
import { agentColour, healthFor, initialsOf, statusOf } from "./derive";
import { Ago, Confirm, Empty, HealthDot, StatusBadge, VerdictBadge } from "./parts";
import { useFirewall } from "./provider";
import type { StoredAgent } from "./state";
import f from "./firewall.module.css";

export function Agents({ onOpenDecision }: { onOpenDecision: (d: Decision) => void }) {
  const { agents, mandates, headroom, addAgent, setAgentStatus, device } = useFirewall();
  const { decisions } = useLive();

  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState<{ agent: StoredAgent; to: "paused" | "revoked" } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const mandatesOf = useMemo(() => {
    const m = new Map<string, typeof mandates>();
    for (const sm of mandates) {
      const id = sm.mandate.delegate.agent_id;
      m.set(id, [...(m.get(id) ?? []), sm]);
    }
    return m;
  }, [mandates]);

  const lastSeen = (agentId: string): string | null => {
    const ids = new Set(
      (mandatesOf.get(agentId) ?? []).map((sm) => sm.mandate.mandate_id),
    );
    const d = decisions.find((x) => ids.has(x.mandate_id));
    return d?.at ?? null;
  };

  const detail = open ? agents.find((a) => a.agent_id === open) : null;

  if (detail) {
    return (
      <AgentDetail
        agent={detail}
        onBack={() => setOpen(null)}
        onOpenDecision={onOpenDecision}
        onStatus={(to) => setConfirm({ agent: detail, to })}
      />
    );
  }

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Agents</div>
          <div className={f.pageLede}>
            Which software may ask to spend, and what it is holding right now.
          </div>
        </div>
      </div>

      <div className={f.agentGrid}>
        {agents.map((a) => {
          const held = (mandatesOf.get(a.agent_id) ?? []).filter(
            (sm) => statusOf(sm, headroom[sm.mandate.mandate_id]) === "Active",
          );
          const seen = lastSeen(a.agent_id);
          return (
            <div key={a.agent_id} className={`${f.agentCard} ${f.hoverLift}`}>
              <div className={f.agentHead}>
                <span className={f.identicon} style={{ background: agentColour(a.agent_id) }}>
                  {initialsOf(a.name)}
                </span>
                <span>
                  <span className={f.agentName}>{a.name}</span>
                  <div className={f.agentId}>{a.agent_id}</div>
                </span>
              </div>

              <dl className={f.agentFacts}>
                <dt>Status</dt>
                <dd>
                  <StatusBadge
                    status={a.status === "active" ? "Active" : a.status === "paused" ? "Paused" : "Revoked"}
                  />
                </dd>
                <dt>Mandates</dt>
                <dd>{held.length} active</dd>
                <dt>Last decision</dt>
                <dd>{seen ? <Ago at={seen} /> : "never"}</dd>
              </dl>

              <button className={f.btn} onClick={() => setOpen(a.agent_id)}>
                View details
              </button>
            </div>
          );
        })}

        <button className={f.addAgent} onClick={() => setAdding(true)}>
          <span style={{ fontSize: 26 }}>＋</span>
          <span>Add agent</span>
        </button>
      </div>

      {adding && (
        <AddAgent
          devicePubkey={device?.publicKeyB64u ?? ""}
          onCancel={() => setAdding(false)}
          onAdd={(name, id) => {
            addAgent(name, id);
            setAdding(false);
          }}
        />
      )}

      {confirm && (
        <Confirm
          danger={confirm.to === "revoked"}
          busy={busy}
          title={confirm.to === "paused" ? "Pause this agent?" : "Revoke this agent?"}
          confirmLabel={confirm.to === "paused" ? "Pause" : "Revoke"}
          body={
            confirm.to === "paused" ? (
              <>
                Every live mandate held by <strong>{confirm.agent.name}</strong> is revoked at the
                gate, and what was left on each is recorded so it can be re-issued when you resume.
                The agent cannot spend in the meantime.
              </>
            ) : (
              <>
                Every live mandate held by <strong>{confirm.agent.name}</strong> is revoked at the
                gate and nothing is kept. To let it spend again you would sign a new mandate.
              </>
            )
          }
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setBusy(true);
            void setAgentStatus(confirm.agent.agent_id, confirm.to).finally(() => {
              setBusy(false);
              setConfirm(null);
              setOpen(null);
            });
          }}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------- add agent ----- */

function AddAgent({
  devicePubkey,
  onAdd,
  onCancel,
}: {
  devicePubkey: string;
  onAdd: (name: string, id: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [id, setId] = useState(`agent_${Math.random().toString(36).slice(2, 8)}`);
  const [test, setTest] = useState<"idle" | "testing" | "ok" | "failed">("idle");
  const [detail, setDetail] = useState("");

  async function testConnection() {
    setTest("testing");
    try {
      const h = await gate.health();
      setDetail(`gate answered · auditor ${h.auditor} · ${h.subscribers} subscriber(s)`);
      setTest("ok");
    } catch (e) {
      setDetail(e instanceof Error ? e.message : String(e));
      setTest("failed");
    }
  }

  return (
    <div className={f.modal} role="dialog" aria-modal="true" aria-label="Add agent">
      <div className={f.modalCard}>
        <div className={f.modalTitle}>Add an agent</div>

        <div className={f.field} style={{ marginTop: 16 }}>
          <label className={f.label} htmlFor="ag-name">
            Name
          </label>
          <input
            id="ag-name"
            className={f.input}
            value={name}
            autoFocus
            placeholder="ShopBot v2"
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className={f.field}>
          <label className={f.label} htmlFor="ag-id">
            Agent id
          </label>
          <input
            id="ag-id"
            className={`${f.input} ${f.mono}`}
            value={id}
            onChange={(e) => setId(e.target.value.replace(/\s+/g, "_"))}
          />
          <span className={f.hint}>Goes into the mandate as the delegate.</span>
        </div>

        <div className={f.field}>
          <span className={f.label}>Delegate public key</span>
          <div style={{ display: "flex", gap: 8 }}>
            <input className={`${f.input} ${f.mono}`} readOnly value={devicePubkey} />
            <button
              className={f.btn}
              onClick={() => void navigator.clipboard?.writeText(devicePubkey)}
            >
              Copy
            </button>
          </div>
          <span className={f.hint}>
            This build delegates to the same device key, so the agent proves itself with a
            signature rather than a bearer secret. There is no API key to leak.
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <button className={f.btn} onClick={() => void testConnection()} disabled={test === "testing"}>
            {test === "testing" ? "Testing…" : "Test connection"}
          </button>
          {test === "ok" && <span style={{ color: "var(--green-ink)" }}>✅ {detail}</span>}
          {test === "failed" && <span style={{ color: "var(--red-ink)" }}>❌ {detail}</span>}
        </div>

        <div className={f.modalActions}>
          <button className={f.btn} onClick={onCancel}>
            Cancel
          </button>
          <button
            className={`${f.btn} ${f.btnPrimary}`}
            disabled={!name.trim() || !id.trim()}
            onClick={() => onAdd(name.trim(), id.trim())}
          >
            Connect
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- detail ----- */

function AgentDetail({
  agent,
  onBack,
  onOpenDecision,
  onStatus,
}: {
  agent: StoredAgent;
  onBack: () => void;
  onOpenDecision: (d: Decision) => void;
  onStatus: (to: "paused" | "revoked") => void;
}) {
  const { mandates, headroom } = useFirewall();
  const { decisions } = useLive();

  const held = mandates.filter((sm) => sm.mandate.delegate.agent_id === agent.agent_id);
  const ids = new Set(held.map((sm) => sm.mandate.mandate_id));
  const mine = decisions.filter((d) => ids.has(d.mandate_id));

  return (
    <div className={f.page}>
      <button className={`${f.btn} ${f.btnGhost} ${f.small}`} onClick={onBack}>
        ← Back to agents
      </button>

      <div className={f.pageHead} style={{ marginTop: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span
            className={f.identicon}
            style={{ background: agentColour(agent.agent_id), width: 46, height: 46, fontSize: 15 }}
          >
            {initialsOf(agent.name)}
          </span>
          <div>
            <div className={f.pageTitle}>{agent.name}</div>
            <div className={f.pageLede}>
              <span className={f.mono}>{agent.agent_id}</span> · connected{" "}
              <Ago at={agent.connected_at} />
            </div>
          </div>
        </div>
        <div className={f.pageActions}>
          <StatusBadge
            status={agent.status === "active" ? "Active" : agent.status === "paused" ? "Paused" : "Revoked"}
          />
          {agent.status === "active" && (
            <>
              <button className={f.btn} onClick={() => onStatus("paused")}>
                Pause agent
              </button>
              <button className={`${f.btn} ${f.btnDanger}`} onClick={() => onStatus("revoked")}>
                Revoke agent
              </button>
            </>
          )}
        </div>
      </div>

      <div className={f.sectionHead}>
        <div className={f.h2}>Mandates held</div>
      </div>

      <div className={f.tableWrap}>
        {held.length === 0 ? (
          <Empty icon="📜" title="No mandates" >
            This agent has never been granted anything on this device.
          </Empty>
        ) : (
          <div className={f.tableScroll}>
            <table className={f.table}>
              <thead>
                <tr>
                  <th style={{ width: 44 }} />
                  <th>Intent</th>
                  <th style={{ width: 150 }}>Budget</th>
                  <th style={{ width: 120 }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {held.map((sm) => {
                  const hr = headroom[sm.mandate.mandate_id];
                  const blocks = decisions.filter(
                    (d) => d.mandate_id === sm.mandate.mandate_id && d.verdict === "BLOCK",
                  ).length;
                  return (
                    <tr key={sm.mandate.mandate_id}>
                      <td>
                        <HealthDot health={healthFor(sm, hr, blocks)} />
                      </td>
                      <td>{sm.mandate.intent}</td>
                      <td className={f.numCell}>{inr(sm.mandate.constraints.max_total_paise)}</td>
                      <td>
                        <StatusBadge status={statusOf(sm, hr)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className={f.sectionHead}>
        <div className={f.h2}>Decisions</div>
      </div>

      <div className={f.tableWrap}>
        {mine.length === 0 ? (
          <Empty icon="🕘" title="Nothing yet" />
        ) : (
          <div className={f.tableScroll}>
            <table className={f.table}>
              <thead>
                <tr>
                  <th style={{ width: 130 }}>Verdict</th>
                  <th>Merchant</th>
                  <th style={{ width: 110 }}>Amount</th>
                  <th style={{ width: 130 }}>When</th>
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
    </div>
  );
}
