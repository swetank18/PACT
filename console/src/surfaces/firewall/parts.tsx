/**
 * The firewall's shared pieces. Every screen is built out of these, so a
 * verdict is the same colour, a number is the same weight and an empty list
 * says something useful wherever you meet it.
 *
 * The charts are hand drawn SVG rather than a charting library. Four small
 * charts do not justify a runtime dependency on a laptop that may be offline
 * when someone runs `npm install` an hour before the pitch.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

import { ago, inr, mmss, stamp } from "../../lib/money";
import type { Band, Health } from "./derive";
import f from "./firewall.module.css";

/* --------------------------------------------------------------- badges --- */

export function VerdictBadge({ verdict }: { verdict: string }) {
  if (verdict === "ALLOW") return <span className={`${f.badge} ${f.bAllow}`}>✅ ALLOWED</span>;
  if (verdict === "BLOCK") return <span className={`${f.badge} ${f.bBlock}`}>❌ BLOCKED</span>;
  if (verdict === "STEP_UP") return <span className={`${f.badge} ${f.bStep}`}>⚠️ STEP-UP</span>;
  return <span className={`${f.badge} ${f.bSkip}`}>{verdict}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "Active"
      ? f.bAllow
      : status === "Revoked"
        ? f.bBlock
        : status === "Paused"
          ? f.bStep
          : f.bQuiet;
  return <span className={`${f.badge} ${cls}`}>{status}</span>;
}

/* ------------------------------------------------------------ stat card --- */

export function StatCard({
  value,
  label,
  tone,
  icon,
  pulse,
  loading,
}: {
  value: ReactNode;
  label: string;
  tone: "blue" | "green" | "amber" | "red";
  icon: string;
  pulse?: boolean;
  loading?: boolean;
}) {
  const toneClass = { blue: f.statBlue, green: f.statGreen, amber: f.statAmber, red: f.statRed }[
    tone
  ];
  return (
    <div className={`${f.stat} ${toneClass} ${f.hoverLift} ${pulse ? f.statPulse : ""}`}>
      <span className={f.statIcon}>{icon}</span>
      {loading ? (
        <div className={f.skel} style={{ width: 90, height: 32 }} />
      ) : (
        <div className={f.statValue}>{value}</div>
      )}
      <div className={f.statLabel}>{label}</div>
    </div>
  );
}

/* ---------------------------------------------------------- health dot ---- */

const BAND_CLASS: Record<Band, string> = { green: "hGreen", amber: "hAmber", red: "hRed" };
const BAND_MARK: Record<Band, string> = { green: "✅", amber: "⚠️", red: "❌" };

export function HealthDot({ health }: { health: Health }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className={f.tipWrap}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={0}
      role="img"
      aria-label={`${health.word}, score ${health.score} of 100`}
    >
      <span className={`${f.health} ${f[BAND_CLASS[health.band]]}`} />
      {open && (
        <span className={f.tip}>
          <strong>
            {BAND_MARK[health.band]} {health.word} — score {health.score}/100
          </strong>
          <span className={f.tipGrid} style={{ marginTop: 8 }}>
            {health.signals.map((s) => (
              <span key={s.label} style={{ display: "contents" }}>
                <span>{s.label}</span>
                <span style={{ opacity: 0.8 }}>{s.detail}</span>
                <span>{BAND_MARK[s.band]}</span>
              </span>
            ))}
          </span>
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------ progress ---- */

export function Bar({
  used,
  total,
  label,
}: {
  used: number;
  total: number;
  label?: ReactNode;
}) {
  const frac = total > 0 ? Math.min(1, Math.max(0, used / total)) : 0;
  const cls = frac > 0.8 ? f.barRed : frac > 0.5 ? f.barAmber : "";
  return (
    <div>
      <div className={f.bar}>
        <div className={`${f.barFill} ${cls}`} style={{ width: `${frac * 100}%` }} />
      </div>
      {label !== undefined && <div className={f.barLabel}>{label}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- time ------- */

/** Relative by default, exact on hover. Every timestamp in the product. */
export function Ago({ at, className }: { at: string; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className={className} title={stamp(at)}>
      {ago(at)}
    </span>
  );
}

/** Counts down and colours as it depletes. Blue, then amber, then red. */
export function Countdown({
  seconds,
  onZero,
}: {
  seconds: number;
  onZero?: () => void;
}) {
  const [left, setLeft] = useState(seconds);
  const fired = useRef(false);

  useEffect(() => {
    const id = setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (left <= 0 && !fired.current) {
      fired.current = true;
      onZero?.();
    }
  }, [left, onZero]);

  const cls = left < 30 ? f.timerCrit : left < 60 ? f.timerWarn : "";
  return <span className={`${f.timer} ${cls}`}>⏱ {mmss(left)}</span>;
}

/* ------------------------------------------------------------ skeleton ---- */

export function SkeletonRows({ n = 5 }: { n?: number }) {
  return (
    <div aria-hidden>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className={`${f.skel} ${f.skelRow}`} style={{ width: `${88 - i * 7}%` }} />
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- empty ---- */

export function Empty({
  icon,
  title,
  children,
}: {
  icon: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className={f.empty}>
      <div className={f.emptyIcon}>{icon}</div>
      <div className={f.emptyTitle}>{title}</div>
      {children && <div>{children}</div>}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className={f.errorBox}>
      <span>⚠️</span>
      <span style={{ flex: 1 }}>{message}</span>
      {onRetry && (
        <button className={`${f.btn} ${f.small}`} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- modal ---- */

export function Confirm({
  title,
  body,
  confirmLabel,
  danger,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className={f.modal} role="dialog" aria-modal="true" aria-label={title}>
      <div className={f.modalCard}>
        <div className={f.modalTitle}>{title}</div>
        <div className={f.modalBody}>{body}</div>
        <div className={f.modalActions}>
          <button className={f.btn} onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={`${f.btn} ${danger ? f.btnDanger : f.btnPrimary}`}
            onClick={onConfirm}
            disabled={busy}
            autoFocus
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- switch ---- */

export function Switch({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`${f.switch} ${on ? f.switchOn : ""}`}
      onClick={() => onChange(!on)}
    >
      <span className={f.knob} />
    </button>
  );
}

/* -------------------------------------------------------------- charts ---- */

/** A tiny inline line, for putting movement next to a single number. */
export function Sparkline({ values, colour = "#3b82f6" }: { values: number[]; colour?: string }) {
  if (values.length < 2) return null;
  const w = 84;
  const h = 24;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * (h - 4) - 2}`)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <polyline points={points} fill="none" stroke={colour} strokeWidth="1.6" />
    </svg>
  );
}

export type Slice = { label: string; value: number; colour: string };

export function Donut({ slices, centre }: { slices: Slice[]; centre?: ReactNode }) {
  const total = slices.reduce((n, s) => n + s.value, 0);
  const r = 70;
  const stroke = 26;
  const circ = 2 * Math.PI * r;
  let offset = 0;

  return (
    <div>
      <svg viewBox="0 0 200 200" className={f.chartSvg} style={{ maxHeight: 210 }}>
        <g transform="rotate(-90 100 100)">
          {total === 0 ? (
            <circle cx="100" cy="100" r={r} fill="none" stroke="#e2e8f0" strokeWidth={stroke} />
          ) : (
            slices.map((s) => {
              const len = (s.value / total) * circ;
              const el = (
                <circle
                  key={s.label}
                  cx="100"
                  cy="100"
                  r={r}
                  fill="none"
                  stroke={s.colour}
                  strokeWidth={stroke}
                  strokeDasharray={`${len} ${circ - len}`}
                  strokeDashoffset={-offset}
                >
                  <title>
                    {s.label}: {inr(s.value)}
                  </title>
                </circle>
              );
              offset += len;
              return el;
            })
          )}
        </g>
        {centre && (
          <text x="100" y="105" textAnchor="middle" fontSize="17" fontWeight="700" fill="#0f172a">
            {centre}
          </text>
        )}
      </svg>
      <div className={f.legend}>
        {slices.map((s) => (
          <span key={s.label} className={f.legendItem}>
            <span className={f.swatch} style={{ background: s.colour }} />
            {s.label} · {inr(s.value)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function Bars({ rows }: { rows: Array<{ label: string; value: number; colour: string }> }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const barH = 30;
  const gap = 14;
  const h = rows.length * (barH + gap);

  return (
    <svg viewBox={`0 0 400 ${Math.max(h, 40)}`} className={f.chartSvg} style={{ maxHeight: 210 }}>
      {rows.map((r, i) => {
        const y = i * (barH + gap);
        const w = (r.value / max) * 250;
        return (
          <g key={r.label}>
            <text x="0" y={y + 19} fontSize="12" fill="#64748b">
              {r.label}
            </text>
            <rect x="118" y={y + 4} width={Math.max(2, w)} height={barH - 8} rx="4" fill={r.colour}>
              <title>
                {r.label}: {inr(r.value)}
              </title>
            </rect>
            <text x={124 + Math.max(2, w)} y={y + 19} fontSize="12" fill="#0f172a" fontWeight="600">
              {inr(r.value)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function TimeSeries({
  rows,
}: {
  rows: Array<{ day: string; allowed: number; blocked: number }>;
}) {
  const w = 640;
  const h = 200;
  const pad = { l: 30, r: 10, t: 12, b: 24 };
  const max = Math.max(1, ...rows.flatMap((r) => [r.allowed, r.blocked]));
  const x = (i: number) => pad.l + (i / Math.max(1, rows.length - 1)) * (w - pad.l - pad.r);
  const y = (v: number) => h - pad.b - (v / max) * (h - pad.t - pad.b);

  const path = (key: "allowed" | "blocked") =>
    rows.map((r, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(r[key])}`).join(" ");

  // Deduplicated: a single-digit maximum collapses the midpoint onto an end.
  const ticks = [...new Set([0, Math.round(max / 2), max])];

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={f.chartSvg} style={{ maxHeight: 240 }}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="#e2e8f0" />
          <text x={pad.l - 6} y={y(t) + 4} fontSize="10" fill="#94a3b8" textAnchor="end">
            {t}
          </text>
        </g>
      ))}

      <path d={path("allowed")} fill="none" stroke="#10b981" strokeWidth="2" />
      <path d={path("blocked")} fill="none" stroke="#ef4444" strokeWidth="2" />

      {rows.map((r, i) => (
        <g key={r.day}>
          <circle cx={x(i)} cy={y(r.allowed)} r="7" fill="transparent">
            <title>{`${r.day}: ${r.allowed} allowed`}</title>
          </circle>
          <circle cx={x(i)} cy={y(r.blocked)} r="7" fill="transparent">
            <title>{`${r.day}: ${r.blocked} blocked`}</title>
          </circle>
        </g>
      ))}

      {rows.length > 0 && (
        <>
          <text x={pad.l} y={h - 6} fontSize="10" fill="#94a3b8">
            {rows[0].day.slice(5)}
          </text>
          <text x={w - pad.r} y={h - 6} fontSize="10" fill="#94a3b8" textAnchor="end">
            {rows[rows.length - 1].day.slice(5)}
          </text>
        </>
      )}
    </svg>
  );
}
