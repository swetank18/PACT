"""
Writes `eval/results/results.md`: paste-ready tables, and the caveats.

Everything reported here is generated from a run. Nothing is typed in by hand,
because a hand-typed number on a results slide is the one thing a national panel
will catch and never forgive.

Where a number is modelled rather than measured — arm A — the table says so in
the row itself, not in a footnote nobody reads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts.money import rupees
from sim.metrics import ArmMetrics, cross_check, mean_and_range


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_results(
    *,
    sessions: int,
    seeds: int,
    uniform: bool,
    out: Path,
    save_sessions: bool = True,
) -> int:
    from sim.run import (
        ABLATIONS,
        ARMS,
        HUMAN_ADDON_RATE,
        HUMAN_COMPLETION_RATE,
        auditor_enabled,
        run_ablation,
        run_arm,
        run_attacks,
        run_benign,
        run_chaos,
    )
    from sim import benign as benign_mod

    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------- arms ---
    print(f"[sim] four arms, {sessions} sessions x {seeds} seed(s)")
    by_arm: dict[str, list[ArmMetrics]] = {}
    all_sessions: dict[str, list] = {}
    for arm in ARMS:
        by_arm[arm] = []
        for seed in range(seeds):
            print(f"[sim]   arm {arm} seed {seed}…", flush=True)
            m, results = run_arm(
                arm, sessions=sessions, seed=seed, uniform=uniform, save=save_sessions
            )
            by_arm[arm].append(m)
            all_sessions.setdefault(arm, []).extend(results)

    check = cross_check(all_sessions.get("D", []))

    # ---------------------------------------------------------- suites ---
    print("[sim] attacks…", flush=True)
    attack_results = run_attacks()
    print("[sim] benign set…", flush=True)
    benign_results = run_benign()
    print("[sim] chaos…", flush=True)
    chaos_results = run_chaos()
    print("[sim] ablation…", flush=True)
    matrix = run_ablation()

    fpr = benign_mod.false_positive_rate(benign_results)
    blocked = sum(1 for r in attack_results if r.blocked and not r.not_applicable)
    applicable = sum(1 for r in attack_results if not r.not_applicable)

    # ----------------------------------------------------------- write ---
    L: list[str] = []
    a = L.append

    a("# Results\n")
    a(f"Generated {started} by `python sim/run.py --all`.")
    a(f"{sessions} sessions per arm, {seeds} seed(s), "
      f"{'uniform persona weights' if uniform else 'weighted personas'}.\n")
    a("Every number here comes from a run. Nothing is typed in by hand.\n")

    a("## The four arms\n")
    a("| Arm | Configuration | GMV / 100 | Completion | AOV | Attach | Upsell rejected | "
      "False block | Step-up recovery | Losses | Net / 100 |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for arm, runs in by_arm.items():
        gmv_mean, gmv_lo, gmv_hi = mean_and_range([m.gmv_per_100 for m in runs])
        net_mean, _, _ = mean_and_range([m.net_per_100 for m in runs])
        agg = runs[0]
        total = ArmMetrics(arm=arm)
        for m in runs:
            total.sessions += m.sessions
            total.completed += m.completed
            total.gmv_paise += m.gmv_paise
            total.upsell_offers_made += m.upsell_offers_made
            total.upsell_offers_accepted += m.upsell_offers_accepted
            total.upsell_rejected_by_gate += m.upsell_rejected_by_gate
            total.false_blocks += m.false_blocks
            total.step_ups += m.step_ups
            total.step_ups_recovered += m.step_ups_recovered
            total.loss_paise += m.loss_paise
            total.saga_recoveries += m.saga_recoveries
        total.avg_order_value_paise = (
            round(total.gmv_paise / total.completed) if total.completed else 0
        )
        label = ARMS[arm]["label"]
        if ARMS[arm].get("human_only"):
            label += " *(modelled, not simulated)*"
        a(
            f"| **{arm}** | {label} | {rupees(round(gmv_mean))} | "
            f"{_pct(total.completion_rate)} | {rupees(total.avg_order_value_paise)} | "
            f"{_pct(total.attach_rate)} | {_pct(total.upsell_rejection_rate)} | "
            f"{_pct(total.false_block_rate)} | {_pct(total.step_up_recovery_rate)} | "
            f"{rupees(total.loss_paise)} | {rupees(round(net_mean))} |"
        )
    a("")

    a("### Range across seeds\n")
    a("A single run of a stochastic agent is not a result.\n")
    a("| Arm | GMV / 100 mean | min | max |")
    a("|---|---:|---:|---:|")
    for arm, runs in by_arm.items():
        mean, lo, hi = mean_and_range([m.gmv_per_100 for m in runs])
        a(f"| {arm} | {rupees(round(mean))} | {rupees(round(lo))} | {rupees(round(hi))} |")
    a("")

    a("### How arm A is produced\n")
    a(f"Arm A is the merchant today: human checkout, no agent channel. There is no "
      f"agent to run, so it is **modelled** with a completion rate of "
      f"{HUMAN_COMPLETION_RATE:.0%} and an addon rate of {HUMAN_ADDON_RATE:.0%}, "
      f"priced through the same quote engine as every other arm. Those two numbers "
      f"are assumptions, stated here rather than buried, and the comparison that "
      f"matters is C against D — both fully simulated.\n")

    a("## Attacks\n")
    a(f"{blocked} of {applicable} applicable attack variants blocked.\n")
    a("| id | Variant | Expected | Verdict | Reason code | Outcome |")
    a("|---|---|---|---|---|---|")
    for r in attack_results:
        a(f"| `{r.id}` | {r.name} | {r.expected} | {r.verdict} | `{r.reason_code}` | "
          f"**{r.outcome}** |")
    a("")
    na = [r for r in attack_results if r.not_applicable]
    if na:
        a(f"> {len(na)} variant(s) reported **N/A** rather than as a pass. "
          f"{na[0].detail}\n")

    a("## Benign set\n")
    a(f"{len(benign_results)} legitimate sessions that must complete. "
      f"**False positive rate {fpr:.1%}** "
      f"({'under' if fpr < 0.05 else 'ABOVE'} the 5% target).\n")
    failures = [r for r in benign_results if not r.passed]
    if failures:
        a("| Session | Verdict | Reason code |")
        a("|---|---|---|")
        for r in failures:
            a(f"| {r.name} | {r.verdict} | `{r.reason_code}` |")
        a("")
    else:
        a("No legitimate session was refused, including every boundary case: an "
          "amount exactly at the cap, a purchase phrased nothing like the stated "
          "goal, the last seconds of the validity window, and product copy "
          "containing the words transfer, verify and ignore.\n")

    a("## Chaos\n")
    a("| id | Scenario | Expected | Observed | Result |")
    a("|---|---|---|---|---|")
    for r in chaos_results:
        a(f"| `{r.id}` | {r.name} | {r.expected} | {r.observed} | "
          f"{'**PASS**' if r.passed else '**FAIL**'} |")
    a("")
    c5 = next((r for r in chaos_results if r.id == "chs_05"), None)
    if c5:
        a(f"> `chs_05` is the one to volunteer unprompted. {c5.detail}\n")

    a("## Ablation matrix\n")
    a("Disable one check, rerun the attack set, record what leaks. "
      "Read the diagonal.\n")
    labels = [label for label, _ in ABLATIONS]
    a("| Attack | " + " | ".join(labels) + " |")
    a("|---" * (len(labels) + 1) + "|")
    for atk_id, row in sorted(matrix.items()):
        cells = " | ".join(row.get(l, "?") for l in labels)
        a(f"| `{atk_id}` | {cells} |")
    a("")

    redundant = _redundant_checks(matrix, labels)
    if redundant:
        a(f"> **{', '.join(redundant)} caught nothing that another check did not also "
          f"catch in this run.** Reported rather than hidden. Kept because the attack "
          f"set is not exhaustive and a layer that is redundant against these variants "
          f"need not be against others.\n")
    else:
        a("> Every check caught something no other check caught. That is the diagonal.\n")

    a("## Cross-check against the services\n")
    if check.agrees:
        a(f"The harness and the merchant agree: GMV {rupees(check.engine_gmv_paise)}.\n")
    else:
        a("**The harness and the services disagree. The services are right.**\n")
        for d in check.discrepancies:
            a(f"- {d}")
        a("")

    a("## Caveats\n")
    a("- Test mode only. No live money moved.")
    a(f"- The rail is simulated unless Razorpay test credentials are configured; "
      f"everything above the adapter is identical either way.")
    if not auditor_enabled():
        a("- **No intent auditor was configured for this run.** The gate ran in "
          "deterministic mode: eight checks plus quote binding plus a pattern scan. "
          "`atk_06` targets the auditor and is reported N/A, not as a pass.")
    a("- Arm A is modelled, not simulated. See above.")
    a("- Personas are weighted to look like a plausible merchant mix. "
      "`--uniform` reruns with equal weights so the result's sensitivity to that "
      "guess is measurable.")
    a("")

    path = out / "results.md"
    path.write_text("\n".join(L))

    (out / "raw.json").write_text(
        json.dumps(
            {
                "generated": started,
                "sessions_per_arm": sessions,
                "seeds": seeds,
                "arms": {a_: [m.to_row() for m in runs] for a_, runs in by_arm.items()},
                "attacks": [r.__dict__ for r in attack_results],
                "benign_false_positive_rate": fpr,
                "chaos": [r.__dict__ for r in chaos_results],
                "ablation": matrix,
            },
            indent=2,
        )
    )
    print(f"[sim] wrote {path}")
    return 0


def _redundant_checks(matrix: dict[str, dict[str, str]], labels: list[str]) -> list[str]:
    """A check is redundant here if disabling it leaked nothing."""
    out = []
    for label in labels:
        if label == "all on":
            continue
        if not any(row.get(label) == "LEAK" for row in matrix.values()):
            out.append(label)
    return out
