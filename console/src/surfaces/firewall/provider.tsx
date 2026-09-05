/**
 * The firewall's own provider.
 *
 * It sits inside the console's LiveDataProvider and adds the two things the
 * live store does not carry: what this device signed, and the headroom
 * envelope for each of those mandates. Headroom is fetched per mandate rather
 * than streamed, so it is refreshed on a decision, on a mandate event, and on
 * a slow timer as a backstop.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { gate } from "../../lib/api";
import type { Headroom, Mandate, Paise } from "../../lib/contracts";
import { signPayload } from "../../lib/crypto";
import { getDeviceKey, newId, type DeviceKey } from "../../lib/device";
import { nowRfc3339 } from "../../lib/money";
import { useLive } from "../../lib/store";
import { statusOf } from "./derive";
import {
  DEFAULT_AGENT_ID,
  seedAgent,
  store,
  type KillState,
  type Prefs,
  type Remainder,
  type StoredAgent,
  type StoredMandate,
} from "./state";

const HEADROOM_POLL_MS = 8000;

export type MandateDraft = {
  intent: string;
  agent_id: string;
  merchants: string[];
  categories: string[];
  per_txn_paise: Paise;
  total_paise: Paise;
  count: number;
  valid_from: string;
  valid_until: string;
  template_id?: string | null;
};

type Ctx = {
  device: DeviceKey | null;
  mandates: StoredMandate[];
  agents: StoredAgent[];
  prefs: Prefs;
  kill: KillState;
  headroom: Record<string, Headroom>;
  /** False until the first headroom sweep has finished, so tables can shimmer. */
  ready: boolean;
  templateUse: Record<string, number>;
  dismissed: string[];

  sign: (draft: MandateDraft) => Promise<StoredMandate>;
  revoke: (mandateId: string, reason: "user" | "agent_revoked") => Promise<void>;
  engageKill: () => Promise<{ revoked: number; failed: number }>;
  releaseKill: () => Promise<{ reissued: number; failed: number }>;
  addAgent: (name: string, agentId: string) => StoredAgent;
  setAgentStatus: (agentId: string, status: StoredAgent["status"]) => Promise<void>;
  setPrefs: (next: Prefs) => void;
  noteTemplateUse: (id: string) => void;
  dismiss: (id: string) => void;
  refreshHeadroom: () => Promise<void>;
};

const C = createContext<Ctx | null>(null);

export function FirewallProvider({ children }: { children: ReactNode }) {
  const { decisions, mandateToken, resetToken } = useLive();

  const [device, setDevice] = useState<DeviceKey | null>(null);
  const [mandates, setMandates] = useState<StoredMandate[]>(() => store.mandates.load());
  const [agents, setAgents] = useState<StoredAgent[]>(() => store.agents.load());
  const [prefs, setPrefsState] = useState<Prefs>(() => store.prefs.load());
  const [kill, setKill] = useState<KillState>(() => store.kill.load());
  const [templateUse, setTemplateUse] = useState<Record<string, number>>(() =>
    store.templateUse.load(),
  );
  const [dismissed, setDismissed] = useState<string[]>(() => store.dismissed.load());
  const [headroom, setHeadroom] = useState<Record<string, Headroom>>({});
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void getDeviceKey().then((k) => {
      setDevice(k);
      // First run: name the agent everything in this system delegates to,
      // rather than opening on an empty Agents tab.
      setAgents((prev) => {
        if (prev.length > 0) return prev;
        const next = [seedAgent(k.publicKeyB64u)];
        store.agents.save(next);
        return next;
      });
    });
  }, []);

  const persistMandates = useCallback((next: StoredMandate[]) => {
    store.mandates.save(next);
    setMandates(next);
  }, []);

  const persistAgents = useCallback((next: StoredAgent[]) => {
    store.agents.save(next);
    setAgents(next);
  }, []);

  /* ------------------------------------------------------- headroom ----- */

  // Which mandates are worth asking about. A revoked or expired one reports
  // zero forever, so it is fetched once and then left alone.
  const settled = useRef<Set<string>>(new Set());

  const refreshHeadroom = useCallback(async () => {
    const wanted = mandates.filter(
      (m) => !m.revoked_at || !settled.current.has(m.mandate.mandate_id),
    );
    if (wanted.length === 0) {
      setReady(true);
      return;
    }
    const results = await Promise.allSettled(
      wanted.map((m) => gate.headroom(m.mandate.mandate_id)),
    );
    setHeadroom((prev) => {
      const next = { ...prev };
      results.forEach((r, i) => {
        const id = wanted[i].mandate.mandate_id;
        if (r.status === "fulfilled") {
          next[id] = r.value;
          if (wanted[i].revoked_at) settled.current.add(id);
        }
      });
      return next;
    });
    setReady(true);
  }, [mandates]);

  useEffect(() => {
    void refreshHeadroom();
  }, [refreshHeadroom, mandateToken, decisions.length]);

  useEffect(() => {
    const id = setInterval(() => void refreshHeadroom(), HEADROOM_POLL_MS);
    return () => clearInterval(id);
  }, [refreshHeadroom]);

  // A server reset wipes every mandate the gate ever knew about, so the
  // device's copy is stale rather than merely out of date. Say so by dropping
  // the envelopes; the local records stay, and read as expired.
  useEffect(() => {
    if (resetToken === 0) return;
    setHeadroom({});
    settled.current.clear();
  }, [resetToken]);

  /* ---------------------------------------------------------- signing --- */

  const buildAndSign = useCallback(
    async (draft: MandateDraft, key: DeviceKey, replaces?: string): Promise<StoredMandate> => {
      const unsigned: Mandate = {
        v: 1,
        mandate_id: newId("mnd"),
        delegator: { vpa: prefs.vpa.trim(), pubkey: key.publicKeyB64u },
        delegate: { agent_id: draft.agent_id || DEFAULT_AGENT_ID, pubkey: key.publicKeyB64u },
        intent: draft.intent.trim(),
        constraints: {
          max_per_txn_paise: draft.per_txn_paise,
          max_total_paise: draft.total_paise,
          max_count: draft.count,
          merchant_allowlist: draft.merchants,
          category_allowlist: draft.categories,
          valid_from: draft.valid_from,
          valid_until: draft.valid_until,
        },
        issued_at: nowRfc3339(),
      };

      const signature = await signPayload(
        unsigned as unknown as Record<string, never>,
        key.privateKey,
      );
      const mandate: Mandate = { ...unsigned, signature };

      // The gate is told, but the mandate is signed either way. A gate that is
      // not up does not make an unsigned mandate.
      await gate.registerMandate(mandate).catch(() => undefined);

      return {
        mandate,
        signature,
        created_at: nowRfc3339(),
        template_id: draft.template_id ?? null,
        replaces: replaces ?? null,
      };
    },
    [prefs.vpa],
  );

  const sign = useCallback(
    async (draft: MandateDraft) => {
      const key = device ?? (await getDeviceKey());
      const stored = await buildAndSign(draft, key);
      persistMandates([stored, ...mandates]);
      return stored;
    },
    [device, buildAndSign, mandates, persistMandates],
  );

  /* --------------------------------------------------------- revoking --- */

  const markRevoked = useCallback(
    (ids: Set<string>, reason: StoredMandate["revoked_reason"], remainders?: Map<string, Remainder>) => {
      const at = nowRfc3339();
      persistMandates(
        mandates.map((m) =>
          ids.has(m.mandate.mandate_id)
            ? {
                ...m,
                revoked_at: at,
                revoked_reason: reason,
                remainder: remainders?.get(m.mandate.mandate_id) ?? m.remainder ?? null,
              }
            : m,
        ),
      );
    },
    [mandates, persistMandates],
  );

  const revoke = useCallback(
    async (mandateId: string, reason: "user" | "agent_revoked") => {
      await gate.revokeMandate(mandateId);
      markRevoked(new Set([mandateId]), reason);
      await refreshHeadroom();
    },
    [markRevoked, refreshHeadroom],
  );

  /* ------------------------------------------------------ kill switch --- */

  /**
   * Pause every agent, server side.
   *
   * There is no pause in the mandate lifecycle, so the honest implementation
   * of "stop everything" is to revoke. A flag held in this browser would be a
   * client-side control, and a compromised agent does not run those.
   *
   * The remaining authority on each mandate is read *before* the revoke,
   * because a revoked mandate reports zero headroom and the remainder would
   * otherwise be unrecoverable. Resume re-issues exactly that much.
   */
  const engageKill = useCallback(async () => {
    const live = mandates.filter((m) => statusOf(m, headroom[m.mandate.mandate_id]) === "Active");
    const remainders = new Map<string, Remainder>();

    const envelopes = await Promise.allSettled(
      live.map((m) => gate.headroom(m.mandate.mandate_id)),
    );
    envelopes.forEach((r, i) => {
      if (r.status === "fulfilled") {
        remainders.set(live[i].mandate.mandate_id, {
          headroom_paise: r.value.headroom_paise,
          payments_remaining: r.value.payments_remaining,
          max_per_txn_paise: r.value.max_per_txn_paise,
        });
      }
    });

    const revoked = await Promise.allSettled(
      live.map((m) => gate.revokeMandate(m.mandate.mandate_id)),
    );
    const ok = new Set<string>();
    revoked.forEach((r, i) => {
      if (r.status === "fulfilled") ok.add(live[i].mandate.mandate_id);
    });

    markRevoked(ok, "kill_switch", remainders);
    persistAgents(agents.map((a) => (a.status === "active" ? { ...a, status: "paused" } : a)));

    const next: KillState = { engaged: true, at: nowRfc3339(), paused: [...ok] };
    store.kill.save(next);
    setKill(next);
    await refreshHeadroom();
    return { revoked: ok.size, failed: live.length - ok.size };
  }, [mandates, headroom, agents, markRevoked, persistAgents, refreshHeadroom]);

  /**
   * Resume.
   *
   * A revocation is not reversible, so this re-issues each paused mandate with
   * the authority that was left at the moment it was paused — not the
   * authority it started with. Anything already spent stays spent.
   */
  const releaseKill = useCallback(async () => {
    const key = device ?? (await getDeviceKey());
    const paused = mandates.filter(
      (m) => m.revoked_reason === "kill_switch" && kill.paused.includes(m.mandate.mandate_id),
    );

    const created: StoredMandate[] = [];
    let failed = 0;

    for (const m of paused) {
      const r = m.remainder;
      const c = m.mandate.constraints;
      // Nothing left, or the window has closed. Re-issuing would be inventing
      // authority the human never granted.
      if (!r || r.headroom_paise <= 0 || r.payments_remaining <= 0) continue;
      if (Date.parse(c.valid_until) <= Date.now()) continue;

      try {
        created.push(
          await buildAndSign(
            {
              intent: m.mandate.intent,
              agent_id: m.mandate.delegate.agent_id,
              merchants: [...c.merchant_allowlist],
              categories: [...c.category_allowlist],
              per_txn_paise: Math.min(r.max_per_txn_paise || c.max_per_txn_paise, r.headroom_paise),
              total_paise: r.headroom_paise,
              count: r.payments_remaining,
              valid_from: nowRfc3339(),
              valid_until: c.valid_until,
              template_id: m.template_id ?? null,
            },
            key,
            m.mandate.mandate_id,
          ),
        );
      } catch {
        failed += 1;
      }
    }

    persistMandates([...created, ...mandates]);
    persistAgents(agents.map((a) => (a.status === "paused" ? { ...a, status: "active" } : a)));
    store.kill.save({ engaged: false, at: null, paused: [] });
    setKill({ engaged: false, at: null, paused: [] });
    await refreshHeadroom();
    return { reissued: created.length, failed };
  }, [device, mandates, kill, buildAndSign, persistMandates, persistAgents, agents, refreshHeadroom]);

  /* ---------------------------------------------------------- agents ---- */

  const addAgent = useCallback(
    (name: string, agentId: string) => {
      const agent: StoredAgent = {
        agent_id: agentId,
        name,
        pubkey: device?.publicKeyB64u ?? "",
        status: "active",
        connected_at: nowRfc3339(),
      };
      persistAgents([...agents.filter((a) => a.agent_id !== agentId), agent]);
      return agent;
    },
    [agents, device, persistAgents],
  );

  const setAgentStatus = useCallback(
    async (agentId: string, status: StoredAgent["status"]) => {
      // Pausing or revoking an agent has to reach the gate, or it is a label.
      if (status !== "active") {
        const mine = mandates.filter(
          (m) =>
            m.mandate.delegate.agent_id === agentId &&
            statusOf(m, headroom[m.mandate.mandate_id]) === "Active",
        );
        const done = await Promise.allSettled(
          mine.map((m) => gate.revokeMandate(m.mandate.mandate_id)),
        );
        const ok = new Set<string>();
        done.forEach((r, i) => {
          if (r.status === "fulfilled") ok.add(mine[i].mandate.mandate_id);
        });
        markRevoked(ok, status === "paused" ? "kill_switch" : "agent_revoked");
      }
      persistAgents(agents.map((a) => (a.agent_id === agentId ? { ...a, status } : a)));
      await refreshHeadroom();
    },
    [agents, mandates, headroom, markRevoked, persistAgents, refreshHeadroom],
  );

  /* ------------------------------------------------------------ misc ---- */

  const setPrefs = useCallback((next: Prefs) => {
    store.prefs.save(next);
    setPrefsState(next);
  }, []);

  const noteTemplateUse = useCallback((id: string) => {
    setTemplateUse((prev) => {
      const next = { ...prev, [id]: (prev[id] ?? 0) + 1 };
      store.templateUse.save(next);
      return next;
    });
  }, []);

  const dismiss = useCallback((id: string) => {
    setDismissed((prev) => {
      const next = prev.includes(id) ? prev : [...prev, id];
      store.dismissed.save(next);
      return next;
    });
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      device,
      mandates,
      agents,
      prefs,
      kill,
      headroom,
      ready,
      templateUse,
      dismissed,
      sign,
      revoke,
      engageKill,
      releaseKill,
      addAgent,
      setAgentStatus,
      setPrefs,
      noteTemplateUse,
      dismiss,
      refreshHeadroom,
    }),
    [
      device,
      mandates,
      agents,
      prefs,
      kill,
      headroom,
      ready,
      templateUse,
      dismissed,
      sign,
      revoke,
      engageKill,
      releaseKill,
      addAgent,
      setAgentStatus,
      setPrefs,
      noteTemplateUse,
      dismiss,
      refreshHeadroom,
    ],
  );

  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useFirewall(): Ctx {
  const v = useContext(C);
  if (!v) throw new Error("useFirewall must be used inside <FirewallProvider>");
  return v;
}
