/**
 * The demo strip. LANE-C section 8.
 *
 * "A small strip, unremarkable to the audience." Keys 1 through 6 drive
 * Utkarsh's beats, one key bound to force_stockout, and a reset that must clear
 * the gate, the merchant and the wallet in under a second.
 *
 * Never type a command on stage. Everything here is one key press, and the
 * reset time is displayed because you are going to press it forty times and you
 * want to know the moment it starts getting slow.
 */
import { useCallback, useEffect, useState } from "react";

import { merchant, resetAll, sim } from "./lib/api";
import { ConnDot } from "./components/ui";
import { useLive } from "./lib/store";
import s from "./App.module.css";

const BEATS: Record<number, string> = {
  1: "happy path, end to end",
  2: "headroom upsell accepted",
  3: "naive upsell, gate rejects",
  4: "four attacks, four blocks",
  5: "stockout, rollback, recovered",
  6: "duplicate webhook, no op",
};

export function DemoBar() {
  const { gateConn, merchantConn, refetchAll } = useLive();
  const [running, setRunning] = useState<number | null>(null);
  const [label, setLabel] = useState("ready");
  const [resetMs, setResetMs] = useState<number | null>(null);

  const runBeat = useCallback(async (n: number) => {
    setRunning(n);
    setLabel(`beat ${n} · ${BEATS[n]}`);
    try {
      await sim.beat(n);
    } catch {
      setLabel(`beat ${n} · simulation runner not reachable`);
    } finally {
      setRunning(null);
    }
  }, []);

  const forceStockout = useCallback(async () => {
    setLabel("forcing a stockout on the next fulfilment");
    try {
      await merchant.forceStockout();
    } catch {
      setLabel("merchant not reachable");
    }
  }, []);

  const doReset = useCallback(async () => {
    const started = performance.now();
    setLabel("resetting");
    const { ok, failed } = await resetAll();
    const took = Math.round(performance.now() - started);
    setResetMs(took);
    await refetchAll();
    setLabel(ok ? "reset clean" : `reset partial — ${failed.join(", ")} did not answer`);
  }, [refetchAll]);

  // Keyboard drives all six beats. Ignore the keys while someone is typing in
  // the checkout composer, or the first message on stage triggers a beat.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing =
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement ||
        (el as HTMLElement | null)?.isContentEditable;
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key >= "1" && e.key <= "6") {
        e.preventDefault();
        void runBeat(Number(e.key));
      } else if (e.key === "0") {
        e.preventDefault();
        void doReset();
      } else if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        void forceStockout();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [runBeat, doReset, forceStockout]);

  return (
    <div className={s.demo}>
      <div className={s.beats}>
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <button
            key={n}
            className={`${s.beat} ${running === n ? s.beatActive : ""}`}
            onClick={() => void runBeat(n)}
            disabled={running !== null}
            title={BEATS[n]}
          >
            {n}
          </button>
        ))}
      </div>

      <span className={s.beatLabel}>{label}</span>

      <div className={s.demoRight}>
        <ConnDot state={gateConn} label="gate" />
        <ConnDot state={merchantConn} label="merchant" />
        <button className={`${s.demoBtn} ${s.stockout}`} onClick={() => void forceStockout()}>
          force stockout · s
        </button>
        <button className={s.demoBtn} onClick={() => void doReset()}>
          reset · 0
        </button>
        {resetMs !== null && (
          <span className={`${s.resetMs} ${resetMs > 1000 ? s.resetSlow : ""}`}>{resetMs} ms</span>
        )}
      </div>
    </div>
  );
}
