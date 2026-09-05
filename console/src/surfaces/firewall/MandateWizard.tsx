/**
 * Create a mandate. Spec 2.1 — five steps, one signature.
 *
 * The wizard exists to make a *narrow* mandate the easy thing to produce, so
 * the defaults are the tight ones: today only, one payment, this merchant.
 * Where a wide choice is legitimate it is allowed and warned about rather than
 * blocked — a nudge, not a rule.
 *
 * Step five signs on this device. Nothing before that touches the network
 * except reading the merchant's manifest for the category list, so a wizard
 * left half filled has granted nothing.
 */
import { useEffect, useMemo, useState } from "react";

import { merchant as merchantApi } from "../../lib/api";
import type { Decision } from "../../lib/contracts";
import { inr, nowRfc3339, rupeesToPaise, stamp } from "../../lib/money";
import { useFirewall, type MandateDraft } from "./provider";
import { DEFAULT_AGENT_ID, TEMPLATES, type Template } from "./state";
import f from "./firewall.module.css";

const STEPS = ["Intent", "Merchants", "Spend caps", "Time window", "Review & sign"];

const FALLBACK_CATEGORIES = ["stationery", "office_furniture", "cables", "printers", "storage"];
const FALLBACK_MERCHANTS = ["deskkit@razorpay", "officebasket@okhdfc"];

/**
 * Caps suggested from the words in the intent.
 *
 * Deliberately keyword driven and deliberately conservative. It is a starting
 * point the human edits, not a recommendation dressed up as analysis, and the
 * screen says which keyword fired.
 */
const SUGGESTIONS: Array<{ match: RegExp; why: string; per: number; total: number; count: number }> =
  [
    { match: /lamp|chair|desk|furniture/i, why: "furniture", per: 800000, total: 800000, count: 1 },
    { match: /restock|month|supplies/i, why: "a monthly restock", per: 200000, total: 800000, count: 10 },
    { match: /cable|adapter|charger/i, why: "cables", per: 150000, total: 300000, count: 2 },
    { match: /printer|toner|ink|paper/i, why: "printer consumables", per: 100000, total: 300000, count: 3 },
    { match: /subscription|renew/i, why: "a subscription", per: 100000, total: 100000, count: 1 },
  ];

export type WizardSeed = {
  template?: Template;
  /** A blocked attempt, so the human can authorise the thing that was refused. */
  fromBlock?: Decision;
};

function hoursFromNow(h: number): string {
  return new Date(Date.now() + h * 3600_000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function toLocalInput(rfc3339: string): string {
  const d = new Date(rfc3339);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(v: string): string {
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? nowRfc3339() : d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function MandateWizard({
  seed,
  onDone,
  onCancel,
}: {
  seed: WizardSeed;
  onDone: (mandateId: string) => void;
  onCancel: () => void;
}) {
  const { sign, agents, noteTemplateUse } = useFirewall();

  const [step, setStep] = useState(0);
  const [catalogCats, setCatalogCats] = useState<string[]>(FALLBACK_CATEGORIES);
  const [knownMerchants, setKnownMerchants] = useState<string[]>(FALLBACK_MERCHANTS);

  const t = seed.template;
  const blocked = seed.fromBlock;

  const [intent, setIntent] = useState(t?.intent ?? (blocked ? `pay ${blocked.payee_vpa}` : ""));
  const [agentId, setAgentId] = useState(agents[0]?.agent_id ?? DEFAULT_AGENT_ID);
  const [categories, setCategories] = useState<string[]>(t?.categories ?? []);
  const [merchants, setMerchants] = useState<string[]>(
    t?.merchants ?? (blocked ? [blocked.payee_vpa] : []),
  );
  const [merchantEntry, setMerchantEntry] = useState("");
  const [perTxn, setPerTxn] = useState(
    String((t?.per_txn_paise ?? blocked?.amount_paise ?? 300000) / 100),
  );
  const [total, setTotal] = useState(
    String((t?.total_paise ?? blocked?.amount_paise ?? 300000) / 100),
  );
  const [count, setCount] = useState(String(t?.count ?? 1));
  const [from, setFrom] = useState(nowRfc3339());
  const [until, setUntil] = useState(hoursFromNow(t?.hours ?? 24));
  const [asTemplate, setAsTemplate] = useState(false);

  const [signing, setSigning] = useState<"idle" | "signing" | "done">("idle");
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<string | null>(null);

  // The merchant publishes what it sells. Ask, rather than shipping a list
  // that can drift from the catalog into mandates that can never be used.
  useEffect(() => {
    void merchantApi
      .manifest()
      .then((m) => {
        if (m.categories?.length) setCatalogCats(m.categories);
        if (m.merchant_vpa) setKnownMerchants((prev) => [...new Set([m.merchant_vpa, ...prev])]);
      })
      .catch(() => undefined);
  }, []);

  const perTxnPaise = rupeesToPaise(perTxn);
  const totalPaise = rupeesToPaise(total);
  const countN = Number(count) || 0;

  const suggestion = useMemo(
    () => SUGGESTIONS.find((s) => s.match.test(intent)) ?? null,
    [intent],
  );

  const problems = useMemo(() => {
    const out: string[] = [];
    if (step >= 0 && !intent.trim()) out.push("Say what the agent should do.");
    if (step >= 1 && merchants.length === 0 && categories.length === 0)
      out.push("Pick a category or name a merchant.");
    if (step >= 2) {
      if (perTxnPaise <= 0) out.push("Set a per-payment limit.");
      if (totalPaise <= 0) out.push("Set a total budget.");
      if (perTxnPaise > totalPaise) out.push("The per-payment limit is above the whole budget.");
      if (countN < 1) out.push("Allow at least one payment.");
    }
    if (step >= 3 && Date.parse(until) <= Date.parse(from))
      out.push("The window ends before it starts.");
    return out;
  }, [step, intent, merchants, categories, perTxnPaise, totalPaise, countN, from, until]);

  const blockingNow = useMemo(() => {
    if (step === 0) return !intent.trim();
    if (step === 1) return merchants.length === 0 && categories.length === 0;
    if (step === 2)
      return perTxnPaise <= 0 || totalPaise <= 0 || perTxnPaise > totalPaise || countN < 1;
    if (step === 3) return Date.parse(until) <= Date.parse(from);
    return problems.length > 0;
  }, [step, intent, merchants, categories, perTxnPaise, totalPaise, countN, from, until, problems]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const addMerchant = () => {
    const v = merchantEntry.trim();
    if (!v) return;
    if (!merchants.includes(v)) setMerchants([...merchants, v]);
    setMerchantEntry("");
  };

  async function signNow() {
    setSigning("signing");
    setError(null);
    try {
      const draft: MandateDraft = {
        intent: intent.trim(),
        agent_id: agentId,
        merchants,
        categories,
        per_txn_paise: perTxnPaise,
        total_paise: totalPaise,
        count: countN,
        valid_from: from,
        valid_until: until,
        template_id: t?.id ?? null,
      };
      const stored = await sign(draft);
      if (t) noteTemplateUse(t.id);
      if (asTemplate) noteTemplateUse(`custom:${stored.mandate.mandate_id}`);
      setIssued(stored.mandate.mandate_id);
      setSigning("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSigning("idle");
    }
  }

  /* ------------------------------------------------------------ render --- */

  return (
    <div className={f.page}>
      <div className={f.pageHead}>
        <div>
          <div className={f.pageTitle}>Create a mandate</div>
          <div className={f.pageLede}>
            You sign the limits on this device. The agent carries the signed mandate and never the
            key.
          </div>
        </div>
        <div className={f.pageActions}>
          <button className={f.btn} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>

      <div className={f.wizard}>
        <div className={f.stepper}>
          {STEPS.map((label, i) => (
            <div key={label} style={{ display: "contents" }}>
              <div
                className={`${f.stepDot} ${i === step ? f.stepOn : ""} ${i < step ? f.stepDone : ""}`}
              >
                <span className={f.stepNum}>{i < step ? "✓" : i + 1}</span>
                <span className={f.navLabel}>{label}</span>
              </div>
              {i < STEPS.length - 1 && <span className={f.stepLine} />}
            </div>
          ))}
        </div>

        <div className={f.card}>
          {/* ---------------------------------------------------- step 1 -- */}
          {step === 0 && (
            <>
              <div className={f.cardTitle}>Step 1 of 5 · What should the agent do?</div>
              <div className={f.field} style={{ marginTop: 16 }}>
                <label className={f.label} htmlFor="fw-intent">
                  Intent
                </label>
                <textarea
                  id="fw-intent"
                  className={f.textarea}
                  value={intent}
                  autoFocus
                  onChange={(e) => setIntent(e.target.value)}
                  placeholder="restock office supplies for the month"
                />
                <span className={f.hint}>
                  💡 The gate compares every purchase against this, so write it the way you would
                  tell a colleague.
                </span>
              </div>

              <div className={f.hint}>Examples</div>
              <div className={f.chips} style={{ marginTop: 8 }}>
                {[
                  "restock office supplies for the month",
                  "buy a desk lamp under ₹3,000",
                  "replace printer consumables when they run low",
                ].map((ex) => (
                  <button key={ex} className={f.chip} onClick={() => setIntent(ex)}>
                    {ex}
                  </button>
                ))}
              </div>

              <div className={f.field} style={{ marginTop: 20 }}>
                <label className={f.label} htmlFor="fw-agent">
                  Which agent
                </label>
                <select
                  id="fw-agent"
                  className={f.select}
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                >
                  {agents.map((a) => (
                    <option key={a.agent_id} value={a.agent_id}>
                      {a.name} · {a.agent_id}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}

          {/* ---------------------------------------------------- step 2 -- */}
          {step === 1 && (
            <>
              <div className={f.cardTitle}>Step 2 of 5 · Where may it spend?</div>
              <div className={f.cardSub} style={{ marginTop: 4 }}>
                Categories and named merchants combine — the agent may pay anything that matches
                either.
              </div>

              <div className={f.field} style={{ marginTop: 20 }}>
                <span className={f.label}>Categories</span>
                <div className={f.chips}>
                  {catalogCats.map((c) => (
                    <button
                      key={c}
                      className={`${f.chip} ${categories.includes(c) ? f.chipOn : ""}`}
                      onClick={() => toggle(categories, setCategories, c)}
                    >
                      {c.replace(/_/g, " ")}
                    </button>
                  ))}
                </div>
                <span className={f.hint}>From this merchant's published manifest.</span>
              </div>

              <div className={f.field}>
                <label className={f.label} htmlFor="fw-vpa">
                  Specific merchants
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    id="fw-vpa"
                    className={`${f.input} ${f.mono}`}
                    value={merchantEntry}
                    list="fw-known-merchants"
                    placeholder="deskkit@razorpay"
                    onChange={(e) => setMerchantEntry(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addMerchant();
                      }
                    }}
                  />
                  <datalist id="fw-known-merchants">
                    {knownMerchants.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <button className={f.btn} onClick={addMerchant}>
                    Add
                  </button>
                </div>
                {merchants.length > 0 && (
                  <div className={f.tagRow}>
                    {merchants.map((m) => (
                      <span key={m} className={f.tag}>
                        {m}
                        <button
                          className={f.tagX}
                          aria-label={`Remove ${m}`}
                          onClick={() => setMerchants(merchants.filter((x) => x !== m))}
                        >
                          ✕
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {merchants.length === 0 && categories.length > 0 && (
                <div className={f.warn}>
                  ⚠️ No named merchant. The agent may pay anyone selling in{" "}
                  {categories.join(", ")}, within the budget you set next.
                </div>
              )}
            </>
          )}

          {/* ---------------------------------------------------- step 3 -- */}
          {step === 2 && (
            <>
              <div className={f.cardTitle}>Step 3 of 5 · How much?</div>

              {suggestion && (
                <div className={f.note} style={{ marginTop: 16 }}>
                  <span>💡</span>
                  <span>
                    That reads like {suggestion.why}. Suggested: {inr(suggestion.per)} per payment,{" "}
                    {inr(suggestion.total)} total, {suggestion.count} payment
                    {suggestion.count === 1 ? "" : "s"}.{" "}
                    <button
                      className={`${f.btn} ${f.small}`}
                      style={{ marginLeft: 6 }}
                      onClick={() => {
                        setPerTxn(String(suggestion.per / 100));
                        setTotal(String(suggestion.total / 100));
                        setCount(String(suggestion.count));
                      }}
                    >
                      Use these
                    </button>
                  </span>
                </div>
              )}

              <div className={f.row3} style={{ marginTop: 20 }}>
                <div className={f.field}>
                  <label className={f.label} htmlFor="fw-per">
                    Per-payment limit (₹)
                  </label>
                  <input
                    id="fw-per"
                    className={f.input}
                    inputMode="decimal"
                    value={perTxn}
                    onChange={(e) => setPerTxn(e.target.value)}
                  />
                  <span className={f.hint}>Most the agent can spend in one payment</span>
                </div>
                <div className={f.field}>
                  <label className={f.label} htmlFor="fw-total">
                    Total budget (₹)
                  </label>
                  <input
                    id="fw-total"
                    className={f.input}
                    inputMode="decimal"
                    value={total}
                    onChange={(e) => setTotal(e.target.value)}
                  />
                  <span className={f.hint}>Across every payment on this mandate</span>
                </div>
                <div className={f.field}>
                  <label className={f.label} htmlFor="fw-count">
                    Payments allowed
                  </label>
                  <input
                    id="fw-count"
                    className={f.input}
                    inputMode="numeric"
                    value={count}
                    onChange={(e) => setCount(e.target.value.replace(/\D/g, ""))}
                  />
                  <span className={f.hint}>The gate counts them server side</span>
                </div>
              </div>

              {perTxnPaise > totalPaise && (
                <div className={f.warn}>
                  ⚠️ The per-payment limit is above the whole budget, so it can never bind.
                </div>
              )}

              <div className={f.note}>
                <span>🧾</span>
                <span>
                  {inr(totalPaise)} total · at most {inr(perTxnPaise)} each · at most {countN}{" "}
                  payment{countN === 1 ? "" : "s"}
                </span>
              </div>
            </>
          )}

          {/* ---------------------------------------------------- step 4 -- */}
          {step === 3 && (
            <>
              <div className={f.cardTitle}>Step 4 of 5 · For how long?</div>
              <div className={f.cardSub} style={{ marginTop: 4 }}>
                A narrow window is the cheapest control there is. The default is today.
              </div>

              <div className={f.chips} style={{ marginTop: 16 }}>
                {[
                  ["Next 1 hour", 1],
                  ["Today only", 24],
                  ["This week", 168],
                  ["This month", 720],
                ].map(([label, hours]) => (
                  <button
                    key={label as string}
                    className={`${f.chip} ${
                      Math.abs(Date.parse(until) - Date.now() - (hours as number) * 3600_000) < 60_000
                        ? f.chipOn
                        : ""
                    }`}
                    onClick={() => {
                      setFrom(nowRfc3339());
                      setUntil(hoursFromNow(hours as number));
                    }}
                  >
                    {label as string}
                  </button>
                ))}
              </div>

              <div className={f.row2} style={{ marginTop: 20 }}>
                <div className={f.field}>
                  <label className={f.label} htmlFor="fw-from">
                    Valid from
                  </label>
                  <input
                    id="fw-from"
                    type="datetime-local"
                    className={f.input}
                    value={toLocalInput(from)}
                    onChange={(e) => setFrom(fromLocalInput(e.target.value))}
                  />
                </div>
                <div className={f.field}>
                  <label className={f.label} htmlFor="fw-until">
                    Valid until
                  </label>
                  <input
                    id="fw-until"
                    type="datetime-local"
                    className={f.input}
                    value={toLocalInput(until)}
                    onChange={(e) => setUntil(fromLocalInput(e.target.value))}
                  />
                </div>
              </div>

              <div className={f.bar}>
                <div className={`${f.barFill} ${f.barBlue}`} style={{ width: "100%" }} />
              </div>
              <div className={f.barLabel}>
                {stamp(from)} → {stamp(until)}
              </div>
            </>
          )}

          {/* ---------------------------------------------------- step 5 -- */}
          {step === 4 && (
            <>
              <div className={f.cardTitle}>📋 Step 5 of 5 · Review and sign</div>

              <dl className={f.kv} style={{ marginTop: 18 }}>
                <dt>Intent</dt>
                <dd style={{ textAlign: "left" }}>{intent}</dd>
                <dt>Agent</dt>
                <dd style={{ textAlign: "left" }}>
                  {agents.find((a) => a.agent_id === agentId)?.name ?? agentId}
                </dd>
                <dt>Categories</dt>
                <dd style={{ textAlign: "left" }}>
                  {categories.length ? categories.join(", ") : "— none"}
                </dd>
                <dt>Merchants</dt>
                <dd className={f.mono} style={{ textAlign: "left" }}>
                  {merchants.length ? merchants.join(", ") : "— none"}
                </dd>
                <dt>Budget</dt>
                <dd style={{ textAlign: "left" }}>
                  {inr(totalPaise)} total, {inr(perTxnPaise)} per payment
                </dd>
                <dt>Payments</dt>
                <dd style={{ textAlign: "left" }}>at most {countN}</dd>
                <dt>Window</dt>
                <dd style={{ textAlign: "left" }}>
                  {stamp(from)} → {stamp(until)}
                </dd>
              </dl>

              <div className={f.note} style={{ marginTop: 18 }}>
                <span>🔐</span>
                <span>
                  Signing with your device key. The private key stays in this browser — the agent
                  receives the signed mandate, never the key.
                </span>
              </div>

              <label
                style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 16 }}
                htmlFor="fw-tpl"
              >
                <input
                  id="fw-tpl"
                  type="checkbox"
                  checked={asTemplate}
                  onChange={(e) => setAsTemplate(e.target.checked)}
                />
                <span>💾 Save as a template for reuse</span>
              </label>

              {error && (
                <div className={f.errorBox} style={{ marginTop: 16 }}>
                  <span>⚠️</span>
                  <span>Could not sign: {error}</span>
                </div>
              )}

              {signing === "done" && issued && (
                <div className={f.note} style={{ marginTop: 16 }}>
                  <span>✅</span>
                  <span>
                    Signed and registered. Mandate <span className={f.mono}>{issued}</span>
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        {problems.length > 0 && step === 4 && (
          <div className={f.err} style={{ marginTop: 12 }}>
            {problems[0]}
          </div>
        )}

        <div className={f.wizardNav}>
          <button
            className={f.btn}
            onClick={() => (step === 0 ? onCancel() : setStep(step - 1))}
            disabled={signing === "signing"}
          >
            ← Back
          </button>

          {step < 4 ? (
            <button
              className={`${f.btn} ${f.btnPrimary}`}
              onClick={() => setStep(step + 1)}
              disabled={blockingNow}
            >
              Next →
            </button>
          ) : signing === "done" && issued ? (
            <button className={`${f.btn} ${f.btnPrimary} ${f.btnBig}`} onClick={() => onDone(issued)}>
              View mandate →
            </button>
          ) : (
            <button
              className={`${f.btn} ${f.btnGreen} ${f.btnBig}`}
              onClick={() => void signNow()}
              disabled={problems.length > 0 || signing === "signing"}
            >
              {signing === "signing" ? "🔐 Signing on device…" : "✅ Sign & activate"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export { TEMPLATES };
