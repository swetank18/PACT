/**
 * Settings. Spec tab 6.
 *
 * Two things here are stated rather than implied, because implying them would
 * be a lie the rest of the product has to live with:
 *
 *   The auto-approve threshold is applied by this screen, not by the gate. It
 *   decides how *this* device answers a step up; it does not change what the
 *   gate will allow without one.
 *
 *   The export's hash chain is computed here, over the rows as exported. It
 *   makes the exported file tamper evident. It is not a chain the gate keeps,
 *   so it proves the file has not been edited since you saved it — not that
 *   the gate's own log was never touched.
 */
import { useState } from "react";

import { sha256 } from "@noble/hashes/sha256";

import type { Decision } from "../../lib/contracts";
import { bytesToHex } from "../../lib/crypto";
import { rotateDeviceKey } from "../../lib/device";
import { canonicalize, type JsonValue } from "../../lib/jcs";
import { inr, inrPlain, stamp } from "../../lib/money";
import { useLive } from "../../lib/store";
import { Confirm, Switch } from "./parts";
import { useFirewall } from "./provider";
import f from "./firewall.module.css";

const NOTIFY_ROWS: Array<{ key: keyof ReturnType<typeof noteKeys>; label: string }> = [
  { key: "blocked", label: "Blocked payments" },
  { key: "step_up", label: "Payments that need your approval" },
  { key: "expiry", label: "Mandates about to expire" },
  { key: "weekly", label: "Weekly summary" },
  { key: "every_allow", label: "Every approved payment" },
];

// Only here so the row list above can be typed against the real shape.
function noteKeys() {
  return {
    blocked: true,
    step_up: true,
    expiry: true,
    weekly: true,
    every_allow: false,
  };
}

/* -------------------------------------------------------- export chain ---- */

type ExportRow = {
  seq: number;
  decision_id: string;
  at: string;
  verdict: string;
  reason_code: string;
  mandate_id: string;
  payee_vpa: string;
  amount_paise: number;
  elapsed_ms: number;
  prev_hash: string;
  hash: string;
};

/**
 * Each row's hash covers the row *and* the hash before it, so changing any row
 * changes every hash after it. Recompute the chain over the file and compare
 * the last hash to verify it.
 */
function chain(decisions: Decision[]): ExportRow[] {
  let prev = "0".repeat(64);
  return decisions.map((d, i) => {
    const body = {
      seq: i,
      decision_id: d.decision_id,
      at: d.at,
      verdict: d.verdict,
      reason_code: String(d.reason_code),
      mandate_id: d.mandate_id,
      payee_vpa: d.payee_vpa,
      amount_paise: d.amount_paise,
      elapsed_ms: d.elapsed_ms,
      prev_hash: prev,
    };
    const hash = bytesToHex(sha256(canonicalize(body as unknown as JsonValue)));
    const row: ExportRow = { ...body, hash };
    prev = hash;
    return row;
  });
}

function download(name: string, mime: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function toCsv(rows: ExportRow[]): string {
  const cols = Object.keys(rows[0] ?? { seq: 0 }) as Array<keyof ExportRow>;
  const esc = (v: unknown) => `"${String(v).replace(/"/g, '""')}"`;
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

/* -------------------------------------------------------------- screen ---- */

export function Settings() {
  const { prefs, setPrefs, device, mandates } = useFirewall();
  const { decisions } = useLive();

  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [rotating, setRotating] = useState(false);
  const [rotated, setRotated] = useState(false);

  const fingerprint = device ? bytesToHex(sha256(device.publicKey)).slice(0, 32) : "";

  const inRange = decisions
    .filter((d) => {
      const t = Date.parse(d.at);
      if (from && t < new Date(from).getTime()) return false;
      if (to && t > new Date(to).getTime() + 86_399_000) return false;
      return true;
    })
    .slice()
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));

  const rows = chain(inRange);
  const tip = rows.length ? rows[rows.length - 1].hash : null;

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Settings</div>
          <div className={f.pageLede}>Your account, your key, and what leaves this device.</div>
        </div>
      </div>

      {/* ---- profile --------------------------------------------------- */}
      <div className={f.card}>
        <div className={f.cardTitle}>Profile and linked accounts</div>
        <div className={f.row2} style={{ marginTop: 18 }}>
          <div className={f.field}>
            <label className={f.label} htmlFor="set-name">
              Name
            </label>
            <input
              id="set-name"
              className={f.input}
              value={prefs.display_name}
              onChange={(e) => setPrefs({ ...prefs, display_name: e.target.value })}
            />
          </div>
          <div className={f.field}>
            <label className={f.label} htmlFor="set-vpa">
              Paying from
            </label>
            <input
              id="set-vpa"
              className={`${f.input} ${f.mono}`}
              value={prefs.vpa}
              onChange={(e) => setPrefs({ ...prefs, vpa: e.target.value })}
            />
            <span className={f.hint}>
              Goes into every mandate you sign from here as the delegator.
            </span>
          </div>
        </div>
        <div className={f.cardSub}>
          {mandates.length} mandate{mandates.length === 1 ? "" : "s"} signed on this device.
        </div>
      </div>

      {/* ---- notifications --------------------------------------------- */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Notifications</div>
      </div>
      <div className={f.card}>
        {NOTIFY_ROWS.map((r) => (
          <div key={r.key} className={f.toggleRow}>
            <span className={f.toggleLabel}>{r.label}</span>
            <Switch
              label={r.label}
              on={prefs.notify[r.key]}
              onChange={(v) => setPrefs({ ...prefs, notify: { ...prefs.notify, [r.key]: v } })}
            />
          </div>
        ))}
        <div className={f.toggleRow}>
          <span className={f.toggleLabel}>Sound when a payment is blocked</span>
          <Switch
            label="Sound on block"
            on={prefs.sound_on_block}
            onChange={(v) => setPrefs({ ...prefs, sound_on_block: v })}
          />
        </div>
      </div>

      {/* ---- security --------------------------------------------------- */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Security</div>
      </div>
      <div className={f.card}>
        <dl className={f.kv}>
          <dt>Signing key fingerprint</dt>
          <dd className={f.mono}>{fingerprint || "—"}</dd>
          <dt>Algorithm</dt>
          <dd>Ed25519 over RFC 8785 JCS</dd>
          <dt>Where it lives</dt>
          <dd>this browser's local storage</dd>
        </dl>

        <div className={f.warn} style={{ marginTop: 16 }}>
          ⚠️ localStorage is not a secure enclave. On a phone this is the device keystore behind a
          biometric prompt; the security model is the same shape, the storage is not.
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
          <button className={`${f.btn} ${f.btnDanger}`} onClick={() => setRotating(true)}>
            Rotate signing key
          </button>
          {rotated && <span style={{ color: "var(--green-ink)" }}>✅ New key generated</span>}
        </div>

        <div className={f.sectionHead}>
          <div className={f.cardTitle}>Auto-approve threshold</div>
        </div>
        <input
          className={f.slider}
          type="range"
          min={0}
          max={50000}
          step={1000}
          value={prefs.auto_approve_paise}
          onChange={(e) => setPrefs({ ...prefs, auto_approve_paise: Number(e.target.value) })}
          aria-label="Auto-approve threshold"
        />
        <div className={f.barLabel}>
          {prefs.auto_approve_paise === 0
            ? "Off — every step-up waits for you."
            : `Step-ups at or under ₹${inrPlain(prefs.auto_approve_paise)} are approved from this screen without asking.`}
        </div>
        <div className={f.note} style={{ marginTop: 12 }}>
          <span>ℹ️</span>
          <span>
            This threshold lives on this device. It decides how this screen answers a step-up — the
            approval is still signed by your key and still verified by the gate. It does not change
            what the gate allows without asking.
          </span>
        </div>
      </div>

      {/* ---- audit ------------------------------------------------------ */}
      <div className={f.sectionHead}>
        <div className={f.h2}>Audit trail</div>
      </div>
      <div className={f.card}>
        <div className={f.row3}>
          <div className={f.field}>
            <label className={f.label} htmlFor="exp-from">
              From
            </label>
            <input
              id="exp-from"
              type="date"
              className={f.input}
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div className={f.field}>
            <label className={f.label} htmlFor="exp-to">
              To
            </label>
            <input
              id="exp-to"
              type="date"
              className={f.input}
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          <div className={f.field}>
            <span className={f.label}>Rows</span>
            <div style={{ paddingTop: 9 }}>
              {rows.length} decision{rows.length === 1 ? "" : "s"} ·{" "}
              {inr(rows.reduce((n, r) => n + r.amount_paise, 0))}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            className={`${f.btn} ${f.btnPrimary}`}
            disabled={rows.length === 0}
            onClick={() => download("pact-decisions.csv", "text/csv", toCsv(rows))}
          >
            📥 Export CSV
          </button>
          <button
            className={f.btn}
            disabled={rows.length === 0}
            onClick={() =>
              download(
                "pact-decisions.json",
                "application/json",
                JSON.stringify({ exported_at: new Date().toISOString(), tip_hash: tip, rows }, null, 2),
              )
            }
          >
            📥 Export JSON
          </button>
        </div>

        {tip && (
          <>
            <div className={f.note} style={{ marginTop: 16 }}>
              <span>🔒</span>
              <span>
                Each row's SHA-256 covers the row and the hash before it, so editing any row
                changes every hash after it. Recompute the chain over the file and compare the last
                hash to verify it.
              </span>
            </div>
            <dl className={f.kv} style={{ marginTop: 12 }}>
              <dt>Tip hash</dt>
              <dd className={f.mono} style={{ wordBreak: "break-all" }}>
                {tip}
              </dd>
            </dl>
            <div className={f.barLabel}>
              Computed here, over the rows as exported. The gate does not keep this chain, so it
              proves the file has not been edited since you saved it.
            </div>
          </>
        )}
      </div>

      {/* ---- about ------------------------------------------------------ */}
      <div className={f.sectionHead}>
        <div className={f.h2}>About</div>
      </div>
      <div className={f.card}>
        <dl className={f.kv}>
          <dt>Product</dt>
          <dd>PACT — agent payment firewall</dd>
          <dt>Surface</dt>
          <dd>principal console</dd>
          <dt>Decisions held on this instance</dt>
          <dd>{decisions.length}</dd>
          <dt>Oldest decision on screen</dt>
          <dd>{decisions.length ? stamp(decisions[decisions.length - 1].at) : "—"}</dd>
        </dl>
      </div>

      {rotating && (
        <Confirm
          danger
          title="Rotate your signing key?"
          confirmLabel="Rotate"
          body={
            <>
              A new Ed25519 key is generated in this browser and the old one is discarded. Mandates
              already registered stay valid at the gate, but this device will no longer be able to
              approve a step-up on them, because the gate verifies approvals against the key on the
              mandate. Revoke them first if that matters.
            </>
          }
          onCancel={() => setRotating(false)}
          onConfirm={() => {
            void rotateDeviceKey().then(() => {
              setRotated(true);
              setRotating(false);
            });
          }}
        />
      )}
    </div>
  );
}
