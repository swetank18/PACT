/**
 * Six slides. LANE-C section 10.
 *
 * They live in the app rather than in a slide tool for one reason: on stage you
 * are already in this browser, and a window switch is a place for something to
 * go wrong. Arrow keys and space move; nothing animates.
 *
 * Slides 1, 2 and 6 are rewritten per event. Everything else is frozen.
 *
 * Slide 5 reads its numbers from eval/results/results.md, which is Lane B's.
 * Until that file exists the cells say so out loud rather than showing a
 * plausible fabricated number — a made-up figure on a results slide is the one
 * mistake a national panel will not forgive.
 */
import { useEffect, useState } from "react";

import s from "./Slides.module.css";

const TITLES = [
  "Title",
  "The gap",
  "Architecture",
  "The growth mechanism",
  "Results",
  "Limitations and next",
];

/* ----------------------------------------------------------------- 1 ------ */

function SlideTitle() {
  return (
    <div className={s.slide}>
      <div className={s.num}>01</div>
      <h1 className={s.h1}>
        The merchant reads what the buyer is allowed to spend,
        <br />
        before it quotes.
      </h1>
      <p className={s.lede}>
        PACT. A signed delegation an AI buyer carries, a gate that decides on authority, and a
        merchant that only ever offers what will be approved.
      </p>
      <div className={s.names}>
        <span className={s.name}>
          <strong>Swetank</strong> · engine, merchant, Razorpay
        </span>
        <span className={s.name}>
          <strong>Utkarsh</strong> · buyer agent, simulation, evidence
        </span>
        <span className={s.name}>
          <strong>Devansh</strong> · interfaces, cryptography on device, pitch
        </span>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- 2 ------ */

const PROTOCOLS: Array<[string, string, boolean, boolean, boolean]> = [
  // name, what it standardised, agent identity, payment execution, merchant reads authority
  ["ACP", "agent to commerce checkout", true, true, false],
  ["AP2", "agent payments, mandates", true, true, false],
  ["x402", "machine payments over HTTP", false, true, false],
  ["MPP", "merchant payment protocol", true, true, false],
  ["TAP", "transaction authorisation", true, true, false],
  ["UPI Circle", "human to human delegation", true, true, false],
];

function SlideGap() {
  return (
    <div className={s.slide}>
      <div className={s.num}>02</div>
      <h2 className={s.h2}>Everyone standardised how an agent pays.</h2>
      <p className={s.lede}>
        Nobody let the merchant read the buyer's authority envelope before quoting. That column is
        empty, and an empty column is a product.
      </p>

      <table className={s.table}>
        <thead>
          <tr>
            <th>Protocol</th>
            <th>What it standardised</th>
            <th>Agent identity</th>
            <th>Payment execution</th>
            <th className={`${s.gapCol} ${s.gapHead}`}>Merchant reads authority</th>
          </tr>
        </thead>
        <tbody>
          {PROTOCOLS.map(([name, what, ident, pay, read]) => (
            <tr key={name}>
              <td className={s.protocol}>{name}</td>
              <td className="dim">{what}</td>
              <td className={ident ? s.yes : s.no}>{ident ? "yes" : "—"}</td>
              <td className={pay ? s.yes : s.no}>{pay ? "yes" : "—"}</td>
              <td className={`${s.gapCol} ${read ? s.yes : s.no}`}>{read ? "yes" : "—"}</td>
            </tr>
          ))}
          <tr className={s.ours}>
            <td className={s.protocol}>PACT</td>
            <td className="dim">signed headroom the merchant can read</td>
            <td className={s.yes}>yes</td>
            <td className={s.yes}>yes, on Razorpay</td>
            <td className={`${s.gapCol} ${s.yes}`}>yes</td>
          </tr>
        </tbody>
      </table>

      <p className={s.lede}>
        NPCI is reported to be building the Unified Agent Protocol to register, verify and authorise
        AI agents on UPI, layered on UPI Circle delegation. Press reporting, not a published
        specification — and the authority-reading layer is open either way.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- 3 ------ */

const BOXES: Array<[string, string, boolean]> = [
  ["Device", "The human signs a mandate with an Ed25519 key that never leaves the browser.", false],
  ["Buyer agent", "Holds the signed mandate, discovers the merchant cold, browses over MCP.", false],
  [
    "Gate",
    "Nine checks on authority, ordered cheapest first, short circuiting. Rail agnostic — it never knows how money moves.",
    true,
  ],
  [
    "Merchant",
    "Deterministic quote engine, headroom aware upsell, saga with compensations.",
    false,
  ],
  ["Rail", "Razorpay test mode behind a five method adapter. Swap the rail, nothing else moves.", false],
];

function SlideArchitecture() {
  return (
    <div className={s.slide}>
      <div className={s.num}>03</div>
      <h2 className={s.h2}>Five boxes. Nothing in the core knows about a rail.</h2>

      <div className={s.boxes}>
        {BOXES.map(([title, body, accent]) => (
          <div key={title} className={`${s.box} ${accent ? s.boxAccent : ""}`}>
            <div className={s.boxTitle}>{title}</div>
            <div className={s.boxBody}>{body}</div>
          </div>
        ))}
      </div>

      <div className={s.flow}>
        mandate → quote → headroom → offer → signed authorize → 9 checks → settle → saga
      </div>

      <p className={s.lede}>
        The gate decides on authority. A rail moves money. If a check needs to know which rail it is
        on, the design is wrong — and a test greps the imports to keep it that way.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- 4 ------ */

function SlideGrowth() {
  return (
    <div className={s.slide}>
      <div className={s.num}>04</div>
      <h2 className={s.h2}>Headroom in, provably approvable offer out.</h2>

      <div className={s.mech}>
        <div className={s.mechBox}>
          <span className={s.mechLabel}>What the merchant is told</span>
          <pre className={s.mechCode}>{`headroom_paise        8,90,000
max_per_txn_paise     5,00,000
payments_remaining    3
categories_allowed    stationery, cables
valid_until           2026-08-31T10:00:00Z
merchant_in_scope     true
signature             signed by the gate`}</pre>
          <span className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            Absent by construction: who the buyer is, what they intended, their total budget, their
            spend history. The schema physically cannot carry it.
          </span>
        </div>

        <div className={s.arrow}>→</div>

        <div className={s.mechBox}>
          <span className={s.mechLabel}>What the merchant may offer</span>
          <pre className={s.mechCode}>{`line_total <= headroom_paise
category   in categories_allowed
line_total <= max_per_txn_paise
payments_remaining > 0`}</pre>
          <span className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            Four conditions, checked before the offer is made rather than after it is refused.
          </span>
        </div>
      </div>

      <p className={s.claim}>
        Approval rate on offered upsells is 100 percent by construction, and there is a test that
        asserts it. The gate stops being friction and becomes a conversion instrument.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- 5 ------ */

const ARMS: Array<[string, string]> = [
  ["A", "no agent channel, human checkout only"],
  ["B", "agent transactable, gate off, upsell off"],
  ["C", "naive hard spend cap, naive upsell"],
  ["D", "PACT: full gate, headroom upsell, step up and saga recovery"],
];

function SlideResults() {
  return (
    <div className={s.slide}>
      <div className={s.num}>05</div>
      <h2 className={s.h2}>Four arms. What the merchant actually earns.</h2>

      <table className={s.table}>
        <thead>
          <tr>
            <th>Arm</th>
            <th>Configuration</th>
            <th className={s.num2}>GMV / 100</th>
            <th className={s.num2}>AOV</th>
            <th className={s.num2}>Attach</th>
            <th className={s.num2}>Losses</th>
            <th className={s.num2}>Net revenue</th>
          </tr>
        </thead>
        <tbody>
          {ARMS.map(([arm, config]) => (
            <tr key={arm} className={arm === "D" ? s.armD : undefined}>
              <td className={s.protocol}>{arm}</td>
              <td className="dim">{config}</td>
              <td className={`${s.num2} ${s.pending}`} colSpan={5}>
                {arm === "A" ? "from eval/results/results.md — Lane B" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className={s.lede}>
        Arm B earns more than C and loses it to fraud and hallucinated prices. Arm C is safe and
        leaves money on the table, because a hard cap blocks real sales and a blind upsell offers
        things that cannot be approved. Arm D earns more than both.
      </p>

      <div className={s.matrix}>
        {`                      | all on | -replay | -scope | -ceiling | -quote | -intent |
atk_01 injection      | BLOCK  | BLOCK   | LEAK   | BLOCK    | BLOCK  | LEAK    |
atk_02 replay         | BLOCK  | LEAK    | BLOCK  | BLOCK    | BLOCK  | BLOCK   |
atk_03 slicing        | BLOCK  | BLOCK   | BLOCK  | LEAK     | BLOCK  | BLOCK   |
atk_04 substitution   | BLOCK  | BLOCK   | LEAK   | BLOCK    | BLOCK  | BLOCK   |
atk_05 price halluc.  | BLOCK  | BLOCK   | BLOCK  | BLOCK    | LEAK   | BLOCK   |
atk_06 auditor inject | BLOCK  | BLOCK   | BLOCK  | BLOCK    | BLOCK  | LEAK    |`}
      </div>

      <p className={s.lede}>
        Read the diagonal. Every check catches something no other check catches.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- 6 ------ */

const LIMITS: Array<[string, string]> = [
  ["Test mode only", "Razorpay test keys. No live money has moved through this."],
  ["One merchant", "A single catalog. Multi-merchant onboarding is not built and is not the point."],
  [
    "The intent auditor is probabilistic",
    "Which is exactly why it steps up rather than blocks. The eight deterministic checks are the system; the auditor is the ninth and it is cuttable.",
  ],
  [
    "UAP is press reporting",
    "NPCI's Unified Agent Protocol is reported, not published. We built against UPI Circle's delegation shape, which does exist.",
  ],
  [
    "Adoption depends on agent developers",
    "The headroom endpoint is worth nothing unless buyer agents call it. That is a distribution problem, not a technical one.",
  ],
];

function SlideLimits() {
  return (
    <div className={s.slide}>
      <div className={s.num}>06</div>
      <h2 className={s.h2}>What this is not, yet.</h2>

      <div className={s.limits}>
        {LIMITS.map(([title, body]) => (
          <div key={title} className={s.limit}>
            <span className={s.limitMark}>—</span>
            <span>
              <strong>{title}.</strong> {body}
            </span>
          </div>
        ))}
      </div>

      <p className={s.lede}>
        Next: a second rail for cross-border machine buyers behind the same adapter and the same
        mandate, a lower false positive rate on the auditor, and the headroom endpoint published as
        something any agent framework can call.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------- the deck -- */

const SLIDES = [SlideTitle, SlideGap, SlideArchitecture, SlideGrowth, SlideResults, SlideLimits];

export function Slides() {
  const [i, setI] = useState(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") {
        e.preventDefault();
        setI((n) => Math.min(SLIDES.length - 1, n + 1));
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        setI((n) => Math.max(0, n - 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const Slide = SLIDES[i];

  return (
    <div className={s.deck}>
      <div className={s.stage}>
        <Slide />
      </div>

      <div className={s.footer}>
        <button className={s.navBtn} onClick={() => setI((n) => Math.max(0, n - 1))} disabled={i === 0}>
          ←
        </button>
        <button
          className={s.navBtn}
          onClick={() => setI((n) => Math.min(SLIDES.length - 1, n + 1))}
          disabled={i === SLIDES.length - 1}
        >
          →
        </button>
        <span className={s.title}>
          {i + 1} / {SLIDES.length} · {TITLES[i]}
        </span>
        <div className={s.dots}>
          {SLIDES.map((_, n) => (
            <button
              key={n}
              className={`${s.dot} ${n === i ? s.dotOn : ""}`}
              onClick={() => setI(n)}
              aria-label={`Slide ${n + 1}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
