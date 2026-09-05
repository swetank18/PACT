/**
 * Live state for the console: decisions, orders, saga steps and stats.
 *
 * One provider owns both streams and both polling backstops so no component
 * has to think about transport. Components read; only the demo strip writes.
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

import { GATE, MERCHANT, gate, merchant } from "./api";
import type { Decision, MerchantStats, Order, SagaStep } from "./contracts";
import { openStream, type ConnState } from "./stream";

const MAX_DECISIONS = 200;
const MAX_ORDERS = 200;
const STATS_POLL_MS = 2000;

export const EMPTY_STATS: MerchantStats = {
  gmv_paise: 0,
  orders: 0,
  avg_order_value_paise: 0,
  upsell_offers_made: 0,
  upsell_offers_accepted: 0,
  upsell_offers_filtered_by_headroom: 0,
  upsell_attach_rate: 0,
  recovered_paise: 0,
  recovered_orders: 0,
  needs_attention: 0,
};

type Store = {
  decisions: Decision[];
  orders: Order[];
  saga: Record<string, SagaStep[]>;
  stats: MerchantStats;
  gateConn: ConnState;
  merchantConn: ConnState;
  /** Set when the gate asks for a human. Drives the step up modal. */
  pendingStepUp: Decision | null;
  clearStepUp: () => void;
  loadSaga: (orderId: string) => Promise<void>;
  refetchAll: () => Promise<void>;
  /** Bumped on a server reset so views can drop local selection state. */
  resetToken: number;
  /**
   * Bumped whenever the gate says a mandate changed — today that is only a
   * revocation. Headroom is fetched per mandate rather than streamed, so views
   * that show remaining authority watch this instead of polling.
   */
  mandateToken: number;
};

const Ctx = createContext<Store | null>(null);

/** Newest first, de-duplicated by id, capped. Late duplicates change nothing. */
function upsert<T>(list: T[], incoming: T, idOf: (v: T) => string, cap: number): T[] {
  const id = idOf(incoming);
  const at = list.findIndex((v) => idOf(v) === id);
  if (at === -1) return [incoming, ...list].slice(0, cap);
  const next = list.slice();
  next[at] = { ...next[at], ...incoming };
  return next;
}

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [saga, setSaga] = useState<Record<string, SagaStep[]>>({});
  const [stats, setStats] = useState<MerchantStats>(EMPTY_STATS);
  const [gateConn, setGateConn] = useState<ConnState>("connecting");
  const [merchantConn, setMerchantConn] = useState<ConnState>("connecting");
  const [pendingStepUp, setPendingStepUp] = useState<Decision | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [mandateToken, setMandateToken] = useState(0);

  // Which orders have a timeline open. Only those get saga events applied
  // eagerly; everything else is fetched on demand.
  const watched = useRef<Set<string>>(new Set());

  const refetchDecisions = useCallback(async () => {
    try {
      const { decisions: rows } = await gate.decisions(50);
      setDecisions(rows.slice(0, MAX_DECISIONS));
    } catch {
      /* The connection dot already says we are struggling. */
    }
  }, []);

  const refetchMerchant = useCallback(async () => {
    const [o, s] = await Promise.allSettled([merchant.orders(50), merchant.stats()]);
    if (o.status === "fulfilled") setOrders(o.value.orders.slice(0, MAX_ORDERS));
    if (s.status === "fulfilled") setStats(s.value);
  }, []);

  const refetchAll = useCallback(async () => {
    await Promise.all([refetchDecisions(), refetchMerchant()]);
  }, [refetchDecisions, refetchMerchant]);

  const loadSaga = useCallback(async (orderId: string) => {
    watched.current.add(orderId);
    try {
      const { steps } = await merchant.saga(orderId);
      setSaga((prev) => ({ ...prev, [orderId]: steps.slice().sort((a, b) => a.seq - b.seq) }));
    } catch {
      /* leave whatever we already had on screen */
    }
  }, []);

  const clearStepUp = useCallback(() => setPendingStepUp(null), []);

  const onReset = useCallback(() => {
    setDecisions([]);
    setOrders([]);
    setSaga({});
    setStats(EMPTY_STATS);
    setPendingStepUp(null);
    watched.current.clear();
    setResetToken((n) => n + 1);
    setMandateToken((n) => n + 1);
  }, []);

  /* ------------------------------------------------------- gate stream --- */
  useEffect(() => {
    return openStream({
      url: `${GATE}/v1/decisions/stream`,
      onState: setGateConn,
      onResync: refetchDecisions,
      onEvent: (type, data) => {
        if (type === "decision") {
          const d = data as Decision;
          setDecisions((prev) => upsert(prev, d, (x) => x.decision_id, MAX_DECISIONS));
          if (d.verdict === "STEP_UP") setPendingStepUp(d);
        } else if (type === "step_up") {
          setPendingStepUp(data as Decision);
        } else if (type === "mandate") {
          setMandateToken((n) => n + 1);
        } else if (type === "reset") {
          onReset();
        }
      },
    });
  }, [refetchDecisions, onReset]);

  /* --------------------------------------------------- merchant stream --- */
  useEffect(() => {
    return openStream({
      url: `${MERCHANT}/v1/stream`,
      onState: setMerchantConn,
      onResync: refetchMerchant,
      onEvent: (type, data) => {
        if (type === "order") {
          setOrders((prev) => upsert(prev, data as Order, (x) => x.order_id, MAX_ORDERS));
        } else if (type === "saga_step") {
          const step = data as SagaStep;
          // Reflect the step on the order row even when the timeline is shut,
          // so the feed's state column stays truthful.
          setOrders((prev) =>
            prev.map((o) => (o.order_id === step.order_id ? { ...o, state: step.state } : o)),
          );
          if (!watched.current.has(step.order_id)) return;
          setSaga((prev) => {
            const existing = prev[step.order_id] ?? [];
            if (existing.some((s) => s.seq === step.seq)) return prev; // idempotent
            return {
              ...prev,
              [step.order_id]: [...existing, step].sort((a, b) => a.seq - b.seq),
            };
          });
        } else if (type === "stats") {
          setStats(data as MerchantStats);
        } else if (type === "reset") {
          onReset();
        }
      },
    });
  }, [refetchMerchant, onReset]);

  /* ------------------------------------------------------ poll backstop --- */
  // Section 6: poll stats every two seconds regardless. If both streams are
  // healthy this is redundant, and redundant is the point.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await merchant.stats();
        if (!cancelled) setStats(s);
      } catch {
        /* ignore */
      }
    };
    const id = setInterval(tick, STATS_POLL_MS);
    void tick();
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    void refetchAll();
  }, [refetchAll]);

  const value = useMemo<Store>(
    () => ({
      decisions,
      orders,
      saga,
      stats,
      gateConn,
      merchantConn,
      pendingStepUp,
      clearStepUp,
      loadSaga,
      refetchAll,
      resetToken,
      mandateToken,
    }),
    [
      decisions,
      orders,
      saga,
      stats,
      gateConn,
      merchantConn,
      pendingStepUp,
      clearStepUp,
      loadSaga,
      refetchAll,
      resetToken,
      mandateToken,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLive(): Store {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLive must be used inside <LiveDataProvider>");
  return v;
}
