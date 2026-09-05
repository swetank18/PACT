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

const HEADROOM_POLL_MS = 15000;

/** Long enough to fold a burst of decisions into one request, short enough that
 *  a budget bar still moves while you are looking at it. */
const HEADROOM_COALESCE_MS = 400;

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
  /** Hand a mandate to the gate again, for one it never accepted. */
  reregister: (mandateId: string) => Promise<StoredMandate["registered"]>;
  setPrefs: (next: Prefs) => void;
  noteTemplateUse: (id: string) => void;
  dismiss: (id: string) => void;
  refreshHeadroom: () => Promise<void>;
};

const C = createContext<Ctx | null>(null);

/**
 * Hand a mandate to the gate and report what came back.
 *
 * Three outcomes, not two. The gate answers 200 with `accepted: false` for a
 * signature it could not verify — it stores the mandate so the audit trail
 * shows the attempt and so a later authorize can answer MANDATE_SIG_INVALID
 * rather than MANDATE_NOT_FOUND. Treating that as success would put a mandate
 * on screen as live that refuses every payment made against it.
 */
async function register(mandate: Mandate): Promise<[StoredMandate["registered"], string | null]> {
  try {
    const res = await gate.registerMandate(mandate);
    if (res?.accepted) return ["ok", null];
    return ["rejected", res?.reason_code ?? "the gate did not accept it"];
  } catch (e) {
    return ["unreachable", e instanceof Error ? e.message : String(e)];
  }
}

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

  /** Fetch a named set of envelopes. One request each, all in flight together. */
  const fetchHeadroom = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) {
        setReady(true);
        return;
      }
      const results = await Promise.allSettled(ids.map((id) => gate.headroom(id)));
      setHeadroom((prev) => {
        const next = { ...prev };
        results.forEach((r, i) => {
          const id = ids[i];
          if (r.status !== "fulfilled") return;
          next[id] = r.value;
          // A revoked mandate reports zero forever. Fetch it once, then stop.
          if (mandates.find((m) => m.mandate.mandate_id === id)?.revoked_at) {
            settled.current.add(id);
          }
        });
        return next;
      });
      setReady(true);
    },
    [mandates],
  );

  /** Every mandate still worth asking about. */
  const live = useCallback(
    () =>
      mandates
        .filter((m) => !m.revoked_at || !settled.current.has(m.mandate.mandate_id))
        .map((m) => m.mandate.mandate_id),
    [mandates],
  );

  const refreshHeadroom = useCallback(() => fetchHeadroom(live()), [fetchHeadroom, live]);

  useEffect(() => {
    void refreshHeadroom();
  }, [refreshHeadroom, mandateToken]);

  /* ------------------------------------------------- refresh on a decision -- */

  /**
   * A decision changes what is left on exactly one mandate, so only that one is
   * re-read — and only if this device holds it.
   *
   * This used to sweep every mandate whenever the decision list changed, which
   * is fine at demo pace and is not fine under load: at the ~7 decisions/second
   * the soak sustains, an open tab holding five mandates was issuing about
   * thirty-five headroom requests a second, almost all of them for mandates
   * nothing had happened to. Coalesced through a short window so a burst of
   * decisions against one mandate is still one request.
   */
  const dirty = useRef<Set<string>>(new Set());
  const seenDecisions = useRef<Set<string>>(new Set());
  const flush = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const mine = new Set(live());
    let queued = false;
    for (const d of decisions) {
      if (seenDecisions.current.has(d.decision_id)) continue;
      seenDecisions.current.add(d.decision_id);
      if (!mine.has(d.mandate_id)) continue;
      dirty.current.add(d.mandate_id);
      queued = true;
    }
    if (!queued || flush.current) return;

    flush.current = setTimeout(() => {
      flush.current = null;
      const ids = [...dirty.current];
      dirty.current.clear();
      void fetchHeadroom(ids);
    }, HEADROOM_COALESCE_MS);
  }, [decisions, live, fetchHeadroom]);

  useEffect(
    () => () => {
      if (flush.current) clearTimeout(flush.current);
    },
    [],
  );

  // The backstop, for spending this device did not see a decision for.
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
    seenDecisions.current.clear();
    dirty.current.clear();
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

      // The gate is told, but the mandate is signed either way — a gate that is
      // not up does not make an unsigned mandate. What it said is kept, though,
      // because "signed" and "the gate will honour this" are different claims
      // and only one of them is worth anything to the agent.
      const [registered, detail] = await register(mandate);

      return {
        mandate,
        signature,
        registered,
        register_detail: detail,
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

  const reregister = useCallback(
    async (mandateId: string) => {
      const sm = mandates.find((m) => m.mandate.mandate_id === mandateId);
      if (!sm) return null;
      const [registered, detail] = await register(sm.mandate);
      persistMandates(
        mandates.map((m) =>
          m.mandate.mandate_id === mandateId ? { ...m, registered, register_detail: detail } : m,
        ),
      );
      await refreshHeadroom();
      return registered;
    },
    [mandates, persistMandates, refreshHeadroom],
  );

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
      reregister,
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
      reregister,
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
