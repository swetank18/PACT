/**
 * Surface one: grant authority. LANE-C section 2.
 *
 * The buyer's human writes a delegation and signs it on this device. This is
 * the one piece of real cryptography Lane C owns, and it is the reason the
 * agent never holds spend authority.
 *
 * "Make it feel like granting authority, not filling a form." So the copy is
 * about what the agent may do, the money is large and monospace, and there is
 * exactly one button.
 */
import { useEffect, useMemo, useState } from "react";

import { gate } from "../../lib/api";
import type { Mandate } from "../../lib/contracts";
import { signPayload } from "../../lib/crypto";
import { getDeviceKey, newId, type DeviceKey } from "../../lib/device";
import { inr, nowRfc3339, rupeesToPaise } from "../../lib/money";
import { Button, Pill, SignatureBlock } from "../../components/ui";
import s from "./Grant.module.css";

const CATEGORIES = ["stationery", "office_furniture", "cables", "printers", "storage"];
const MERCHANTS = ["deskkit@razorpay", "officebasket@okhdfc"];

/** Hours from now, as an RFC 3339 UTC timestamp with a Z. */
function hoursFromNow(h: number): string {
  return new Date(Date.now() + h * 3600_000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export type GrantResult = {
  mandate: Mandate;
  signature: string;
  totalBudgetPaise: number;
};

export function Grant({ onGranted }: { onGranted: (r: GrantResult) => void }) {
  const [goal, setGoal] = useState("restock office supplies for the month");
  const [totalRupees, setTotalRupees] = useState("15000");
  const [perTxnRupees, setPerTxnRupees] = useState("5000");
  const [maxCount, setMaxCount] = useState("5");
  const [hours, setHours] = useState("24");
  const [categories, setCategories] = useState<string[]>([
    "stationery",
    "office_furniture",
    "cables",
  ]);
  const [merchants, setMerchants] = useState<string[]>(["deskkit@razorpay"]);
  const [vpa, setVpa] = useState("swetank@okaxis");

  const [device, setDevice] = useState<DeviceKey | null>(null);
  const [signing, setSigning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ mandate: Mandate; signature: string } | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [registered, setRegistered] = useState<"pending" | "ok" | "failed" | null>(null);

  useEffect(() => {
    void getDeviceKey().then(setDevice);
  }, []);

  const totalPaise = rupeesToPaise(totalRupees);
  const perTxnPaise = rupeesToPaise(perTxnRupees);

  const problems = useMemo(() => {
    const out: string[] = [];
    if (!goal.trim()) out.push("Say what the agent is for.");
    if (totalPaise <= 0) out.push("Set a budget.");
    if (perTxnPaise <= 0) out.push("Set a per purchase limit.");
    if (perTxnPaise > totalPaise) out.push("The per purchase limit is above the whole budget.");
    if (Number(maxCount) < 1) out.push("Allow at least one purchase.");
    if (categories.length === 0) out.push("Pick at least one category.");
    if (merchants.length === 0) out.push("Pick at least one merchant.");
    return out;
  }, [goal, totalPaise, perTxnPaise, maxCount, categories, merchants]);

  const toggle = (list: string[], set: (v: string[]) => void, value: string) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  async function grantAndSign() {
    if (!device || problems.length > 0) return;
    setSigning(true);
    setError(null);
    setRegistered(null);
    try {
      // Built exactly in the shape contracts section 6 froze. Field order does
      // not matter — JCS sorts before signing — but the shape does.
      const unsigned: Mandate = {
        v: 1,
        mandate_id: newId("mnd"),
        delegator: { vpa: vpa.trim(), pubkey: device.publicKeyB64u },
        delegate: { agent_id: "buyer_agent_v1", pubkey: device.publicKeyB64u },
        intent: goal.trim(),
        constraints: {
          max_per_txn_paise: perTxnPaise,
          max_total_paise: totalPaise,
          max_count: Number(maxCount),
          merchant_allowlist: merchants,
          category_allowlist: categories,
          valid_from: nowRfc3339(),
          valid_until: hoursFromNow(Number(hours) || 24),
        },
        issued_at: nowRfc3339(),
      };

      const signature = await signPayload(
        unsigned as unknown as Record<string, never>,
        device.privateKey,
      );
      const mandate: Mandate = { ...unsigned, signature };

      setResult({ mandate, signature });
      onGranted({ mandate, signature, totalBudgetPaise: totalPaise });

      // Hand it to the gate. If the gate is not up yet the mandate is still
      // valid and still signed — the console does not pretend otherwise.
      setRegistered("pending");
      try {
        await gate.registerMandate(mandate);
        setRegistered("ok");
      } catch {
        setRegistered("failed");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSigning(false);
    }
  }

  return (
    <div className={s.page}>
      <header className={s.header}>
        <div className={s.kicker}>Grant authority</div>
        <h1 className={s.title}>Give your agent a spending mandate</h1>
        <p className={s.lede}>
          The same shape as a UPI Circle delegation, where a primary user grants a spend-capped
          authority to someone else. Here the someone else is software. You sign the limits on this
          device; the agent carries the signed mandate and never the key.
        </p>
      </header>

      <div className={s.form}>
        <div className={s.field}>
          <label className={s.label} htmlFor="goal">
            What is the agent for
          </label>
          <textarea
            id="goal"
            className={s.textarea}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="restock office supplies for the month"
          />
          <span className={s.hint}>
            The gate compares each purchase against this, so write it the way you would tell a
            colleague.
          </span>
        </div>

        <div className={s.fieldRow}>
          <div className={s.field}>
            <label className={s.label} htmlFor="total">
              Total budget
            </label>
            <input
              id="total"
              className={`${s.input} ${s.money}`}
              value={totalRupees}
              inputMode="decimal"
              onChange={(e) => setTotalRupees(e.target.value)}
            />
            <span className={s.hint}>{inr(totalPaise)} across the whole mandate</span>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="pertxn">
              Most per purchase
            </label>
            <input
              id="pertxn"
              className={`${s.input} ${s.money}`}
              value={perTxnRupees}
              inputMode="decimal"
              onChange={(e) => setPerTxnRupees(e.target.value)}
            />
            <span className={s.hint}>{inr(perTxnPaise)} in any single payment</span>
          </div>
        </div>

        <div className={s.fieldRow}>
          <div className={s.field}>
            <label className={s.label} htmlFor="count">
              Purchases allowed
            </label>
            <input
              id="count"
              className={`${s.input} ${s.money}`}
              value={maxCount}
              inputMode="numeric"
              onChange={(e) => setMaxCount(e.target.value.replace(/\D/g, ""))}
            />
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="hours">
              Valid for
            </label>
            <select
              id="hours"
              className={s.select}
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            >
              <option value="1">1 hour</option>
              <option value="24">24 hours</option>
              <option value="168">7 days</option>
              <option value="720">30 days</option>
            </select>
            <span className={s.hint}>Expires {hoursFromNow(Number(hours) || 24)}</span>
          </div>
        </div>

        <div className={s.field}>
          <span className={s.label}>Categories it may buy</span>
          <div className={s.chips}>
            {CATEGORIES.map((c) => (
              <button
                key={c}
                type="button"
                className={`${s.chip} ${categories.includes(c) ? s.chipOn : ""}`}
                onClick={() => toggle(categories, setCategories, c)}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className={s.field}>
          <span className={s.label}>Merchants it may pay</span>
          <div className={s.chips}>
            {MERCHANTS.map((m) => (
              <button
                key={m}
                type="button"
                className={`${s.chip} ${merchants.includes(m) ? s.chipOn : ""} mono`}
                onClick={() => toggle(merchants, setMerchants, m)}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        <div className={s.field}>
          <label className={s.label} htmlFor="vpa">
            Paying from
          </label>
          <input
            id="vpa"
            className={`${s.input} mono`}
            value={vpa}
            onChange={(e) => setVpa(e.target.value)}
          />
        </div>

        <div className={s.submit}>
          <Button
            variant="primary"
            className={s.grantBtn}
            onClick={() => void grantAndSign()}
            disabled={signing || problems.length > 0 || !device}
          >
            {signing ? "Signing on device…" : "Grant and sign"}
          </Button>
          {problems.length > 0 && <span className={s.hint}>{problems[0]}</span>}
          {error && <span className={s.error}>{error}</span>}
        </div>
      </div>

      {result && (
        <section className={s.result}>
          <div className={s.resultHead}>
            <Pill tone="allow">Signed</Pill>
            <span className={s.resultTitle}>Mandate issued</span>
            <span className={s.mandateId}>{result.mandate.mandate_id}</span>
            {registered === "ok" && <Pill tone="accent">registered with the gate</Pill>}
            {registered === "pending" && <Pill tone="neutral">registering…</Pill>}
            {registered === "failed" && <Pill tone="stepup">gate unreachable</Pill>}
          </div>

          {/* The two seconds that explain the security model. */}
          <SignatureBlock signature={result.signature} />

          <div className={s.summary}>
            <span className={s.summaryLabel}>Budget</span>
            <span className={s.summaryValue}>
              {inr(result.mandate.constraints.max_total_paise)} total ·{" "}
              {inr(result.mandate.constraints.max_per_txn_paise)} per purchase
            </span>

            <span className={s.summaryLabel}>Purchases</span>
            <span className={s.summaryValue}>{result.mandate.constraints.max_count}</span>

            <span className={s.summaryLabel}>Categories</span>
            <span className={s.summaryValue}>
              {result.mandate.constraints.category_allowlist.join(", ")}
            </span>

            <span className={s.summaryLabel}>Merchants</span>
            <span className={s.summaryValue}>
              {result.mandate.constraints.merchant_allowlist.join(", ")}
            </span>

            <span className={s.summaryLabel}>Valid until</span>
            <span className={s.summaryValue}>{result.mandate.constraints.valid_until}</span>

            <span className={s.summaryLabel}>Device public key</span>
            <span className={s.deviceKey}>{device?.publicKeyB64u}</span>
          </div>

          <div className={s.actions}>
            <Button variant="quiet" onClick={() => setShowRaw((v) => !v)}>
              {showRaw ? "Hide the signed mandate" : "Show the signed mandate"}
            </Button>
            <Button
              variant="quiet"
              onClick={() => void navigator.clipboard?.writeText(JSON.stringify(result.mandate))}
            >
              Copy JSON
            </Button>
          </div>

          {showRaw && <pre className={s.raw}>{JSON.stringify(result.mandate, null, 2)}</pre>}
        </section>
      )}
    </div>
  );
}
