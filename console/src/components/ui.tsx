import type { ButtonHTMLAttributes, ReactNode } from "react";

import type { Verdict } from "../lib/contracts";
import type { ConnState } from "../lib/stream";
import s from "./ui.module.css";

/* --------------------------------------------------------------- pills ---- */

type Tone = "allow" | "veto" | "stepup" | "neutral" | "accent";

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`${s.pill} ${s[tone]}`}>{children}</span>;
}

/**
 * The three words the audience has to be able to read from the back of the
 * room. Deliberately not the raw verdict enum — "NEEDS YOU" says who has to
 * act, which is the whole point of a step up.
 */
export function VerdictPill({ verdict }: { verdict: Verdict | string }) {
  if (verdict === "ALLOW") return <Pill tone="allow">Allowed</Pill>;
  if (verdict === "BLOCK") return <Pill tone="veto">Vetoed</Pill>;
  if (verdict === "STEP_UP") return <Pill tone="stepup">Needs you</Pill>;
  return <Pill tone="neutral">{String(verdict)}</Pill>;
}

/* ------------------------------------------------------ connection dot ---- */

/**
 * Section 6. A visible reconnect beats a frozen dashboard, so this never hides
 * a degraded state behind an optimistic green.
 */
export function ConnDot({ state, label }: { state: ConnState; label: string }) {
  const text = state === "live" ? "live" : state === "retrying" ? "reconnecting" : "connecting";
  return (
    <span className={s.conn} title={`${label}: ${text}`}>
      <span className={`${s.dot} ${s[state]}`} />
      {label}
    </span>
  );
}

/* -------------------------------------------------------------- empty ----- */

export function Empty({ children }: { children: ReactNode }) {
  return <div className={s.empty}>{children}</div>;
}

/* ---------------------------------------------------------- signature ----- */

/**
 * The base64 signature in monospace with one line underneath. LANE-C section 2:
 * judges read that line in two seconds and understand the whole security model.
 */
export function SignatureBlock({ signature, note }: { signature: string; note?: string }) {
  return (
    <div>
      <div className={s.sig}>{signature}</div>
      <div className={s.sigNote}>
        {note ?? "Signed on this device. The agent never receives the private key."}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- button ----- */

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "default" | "quiet";
};

export function Button({ variant = "default", className, ...rest }: BtnProps) {
  const v = variant === "primary" ? s.btnPrimary : variant === "quiet" ? s.btnQuiet : "";
  return <button {...rest} className={`${s.btn} ${v} ${className ?? ""}`} />;
}
