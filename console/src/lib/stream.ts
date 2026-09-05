/**
 * SSE transport. LANE-C section 6.
 *
 * "A frozen dashboard that looks fine but has silently stopped updating is a
 * worse stage failure than a visible reconnect." So this module is built around
 * three ideas:
 *
 *   1. Reconnect with backoff, capped at two seconds. Never give up.
 *   2. On every (re)connect, refetch state rather than trusting the stream to
 *      have buffered. The `onResync` callback is not optional.
 *   3. A liveness watchdog. EventSource can sit in OPEN with a dead pipe behind
 *      it, so a stream that has said nothing for a while is treated as broken
 *      even though the browser still thinks it is connected.
 */

export type ConnState = "connecting" | "live" | "retrying";

export type StreamOptions = {
  url: string;
  /** Called for each named event. */
  onEvent: (type: string, data: unknown) => void;
  /** Called on every successful (re)connect. Refetch, do not assume buffering. */
  onResync: () => void | Promise<void>;
  onState?: (state: ConnState) => void;
  /** Treat silence longer than this as a dead pipe. Server sends a heartbeat. */
  idleTimeoutMs?: number;
};

const BACKOFF_CAP_MS = 2000;

export function openStream(opts: StreamOptions): () => void {
  const idleTimeout = opts.idleTimeoutMs ?? 15000;

  let es: EventSource | null = null;
  let attempt = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const setState = (s: ConnState) => opts.onState?.(s);

  const clearTimers = () => {
    if (retryTimer) clearTimeout(retryTimer);
    if (idleTimer) clearTimeout(idleTimer);
    retryTimer = null;
    idleTimer = null;
  };

  const armIdleWatchdog = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      // Silent for too long. The browser may still call this OPEN; it is not.
      if (!closed) reconnect();
    }, idleTimeout);
  };

  const reconnect = () => {
    if (closed) return;
    es?.close();
    es = null;
    setState("retrying");
    // Exponential up to the cap, with jitter so a whole room of reconnecting
    // tabs does not hit the gate in lockstep.
    const base = Math.min(BACKOFF_CAP_MS, 150 * 2 ** attempt);
    const delay = base * (0.7 + Math.random() * 0.3);
    attempt += 1;
    clearTimers();
    retryTimer = setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    setState(attempt === 0 ? "connecting" : "retrying");
    try {
      es = new EventSource(opts.url);
    } catch {
      reconnect();
      return;
    }

    es.onopen = () => {
      attempt = 0;
      setState("live");
      armIdleWatchdog();
      void opts.onResync();
    };

    es.onerror = () => {
      // EventSource retries on its own, but with no backoff cap we control and
      // no resync. Take it over.
      reconnect();
    };

    // Unnamed events plus the heartbeat keep the watchdog fed.
    es.onmessage = (ev) => {
      armIdleWatchdog();
      dispatch("message", ev.data);
    };

    for (const type of STREAM_EVENTS) {
      es.addEventListener(type, (ev) => {
        armIdleWatchdog();
        dispatch(type, (ev as MessageEvent).data);
      });
    }
  };

  const dispatch = (type: string, raw: string) => {
    if (type === "heartbeat") return; // Liveness only, nothing to render.
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw);
    } catch {
      /* A non-JSON frame is not worth killing the stream over. */
    }
    opts.onEvent(type, parsed);
  };

  connect();

  return () => {
    closed = true;
    clearTimers();
    es?.close();
    es = null;
  };
}

/**
 * Event names the console listens for. Adding one here is the only change
 * needed to render a new server event.
 */
export const STREAM_EVENTS = [
  "heartbeat",
  "decision",
  "order",
  "saga_step",
  "stats",
  "quote",
  "addon_offer",
  "step_up",
  "mandate",
  "reset",
] as const;
