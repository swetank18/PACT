#!/usr/bin/env python3
"""
The simulation harness.

    python sim/run.py --arm D --sessions 200 --seeds 3
    python sim/run.py --suite attacks
    python sim/run.py --suite benign
    python sim/run.py --suite chaos
    python sim/run.py --suite ablation
    python sim/run.py --all            # everything, writes eval/results/results.md

Every arm runs against a **reset** system, so one arm's spend cannot colour the
next. Metrics come from the sessions and are cross-checked against the merchant's
own stats; a disagreement is reported rather than smoothed over.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.reason_codes import CHECK_ORDER  # noqa: E402
from buyer.agent import BuyerAgent, SessionResult  # noqa: E402
from buyer.session import run_session  # noqa: E402
from sim import attacks, benign, chaos, hostile  # noqa: E402
from sim.metrics import ArmMetrics, aggregate, cross_check, mean_and_range  # noqa: E402

#: Where the services are. Overridable because the single-port production build
#: mounts them under /api/* on itself rather than on their own ports.
GATE = os.environ.get("PACT_GATE_URL", "http://localhost:8000")
MERCHANT = os.environ.get("PACT_MERCHANT_URL", "http://localhost:8100")
RESULTS = REPO / "eval" / "results"
SESSIONS_DIR = RESULTS / "sessions"

#: The four arms. One agent, two flags — see buyer/agent.py.
ARMS: dict[str, dict[str, Any]] = {
    "A": {
        "gate": "off",
        "upsell": "off",
        "human_only": True,
        "label": "no agent channel, human checkout only",
    },
    # Arm B is not "the agent skips the gate" — a merchant that issues no
    # settlement token cannot be transacted with at all, so that arm would
    # measure nothing. It is the same merchant with **every check ablated**:
    # agent transactable, no authority checking. Same code path, same tokens,
    # no protection.
    "B": {
        "gate": "pact",
        "upsell": "naive",
        "ablate_all": True,
        "label": "agent transactable, no authority checks",
    },
    # Arm C is what a reasonable team builds: their own hard spend cap in the
    # agent, and a blind upsell. It runs against the same ablated gate as arm B,
    # because a team without an authority protocol does not have one — their cap
    # is client side. That distinction matters and it is the argument: a
    # client-side cap protects against your own mistakes and not at all against
    # a compromised agent.
    "C": {
        "gate": "naive",
        "upsell": "naive",
        "ablate_all": True,
        "label": "naive client-side cap, naive upsell",
    },
    "D": {"gate": "pact", "upsell": "headroom", "label": "PACT: gate, headroom upsell, recovery"},
}

#: Arm A is the merchant today: a human checkout with a drop-off model. It does
#: not run the agent at all, so it is modelled rather than simulated, and the
#: model is stated here rather than buried.
HUMAN_COMPLETION_RATE = 0.34
HUMAN_ADDON_RATE = 0.11


def load_personas() -> list[dict[str, Any]]:
    data = yaml.safe_load((REPO / "sim" / "personas.yaml").read_text())
    return data["personas"]


def reset(client: httpx.Client) -> None:
    for url in (f"{GATE}/v1/admin/reset", f"{MERCHANT}/admin/reset"):
        try:
            client.post(url, json={})
        except httpx.HTTPError:
            pass


def services_up() -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=3) as c:
            g = c.get(f"{GATE}/v1/health").json()
            m = c.get(f"{MERCHANT}/v1/health").json()
        return True, f"gate auditor={g.get('auditor')}, merchant rail={m.get('rail')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"services unreachable: {exc}. Start them with ./scripts/dev.sh"


def auditor_enabled() -> bool:
    try:
        with httpx.Client(timeout=3) as c:
            return c.get(f"{GATE}/v1/health").json().get("auditor") == "enabled"
    except Exception:  # noqa: BLE001
        return False


def pick_persona(personas: list[dict], rng: random.Random, uniform: bool) -> dict:
    if uniform:
        return rng.choice(personas)
    weights = [p.get("weight", 1.0) for p in personas]
    return rng.choices(personas, weights=weights, k=1)[0]


# ------------------------------------------------------------------- arms ---


def run_arm(
    arm: str,
    *,
    sessions: int,
    seed: int,
    uniform: bool,
    save: bool,
    hostile_rate: float = hostile.HOSTILE_SESSION_RATE,
) -> tuple[ArmMetrics, list[SessionResult]]:
    config = ARMS[arm]
    personas = load_personas()
    rng = random.Random(seed)

    with httpx.Client(timeout=10) as client:
        reset(client)

    if config.get("human_only"):
        return _model_arm_a(arm, sessions, seed, personas, rng), []

    with httpx.Client(timeout=10) as client:
        # Arm B runs against a gate with every check turned off. Ablation is the
        # honest way to model "no authority checking" — it is the same service,
        # the same tokens and the same saga, with the protection removed.
        disabled = list(CHECK_ORDER) if config.get("ablate_all") else []
        client.post(f"{GATE}/v1/admin/ablate", json={"disabled": disabled})

    agent = BuyerAgent(
        gate_mode=config["gate"], upsell_mode=config["upsell"], seed=seed
    )
    try:
        manifest = agent.discover()
        results: list[SessionResult] = []
        for i in range(sessions):
            # Each session gets its own generator seeded from the run seed, so a
            # single session can be reproduced without replaying the whole arm.
            agent.rng = random.Random(seed * 100_000 + i)
            persona = pick_persona(personas, rng, uniform)
            results.append(
                run_session(
                    agent, persona, arm=arm, manifest=manifest, hostile_rate=hostile_rate
                )
            )

        metrics = aggregate(arm, results)

        if save:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            for r in results:
                (SESSIONS_DIR / f"{r.sim_id}.json").write_text(
                    json.dumps(asdict(r), indent=2)
                )
        return metrics, results
    finally:
        agent.close()
        # Always leave the gate whole, whatever happened above.
        try:
            with httpx.Client(timeout=5) as client:
                client.post(f"{GATE}/v1/admin/ablate", json={"disabled": []})
        except httpx.HTTPError:
            pass


def _model_arm_a(
    arm: str, sessions: int, seed: int, personas: list[dict], rng: random.Random
) -> ArmMetrics:
    """
    Arm A: the merchant today. Human checkout with a drop-off model.

    Explicitly a model, not a simulation — there is no agent to run. The two
    parameters are stated at the top of this file and in results.md, because an
    unlabelled modelled baseline next to three measured arms would be
    misleading.
    """
    from merchant.catalog import CATALOG
    from merchant.quote import QuoteEngine
    from contracts.schemas import QuoteItemRequest

    m = ArmMetrics(arm=arm, sessions=sessions)
    by_category = {}
    for p in CATALOG:
        by_category.setdefault(p.category, []).append(p)

    for _ in range(sessions):
        persona = pick_persona(personas, rng, uniform=False)
        allowed = [
            p for c in persona["mandate"]["categories"] for p in by_category.get(c, [])
        ]
        if not allowed:
            continue
        item = rng.choice(allowed)
        _, _, _, _, total = QuoteEngine.price([QuoteItemRequest(sku=item.sku, qty=1)])

        if rng.random() < HUMAN_COMPLETION_RATE:
            m.completed += 1
            m.gmv_paise += total
            m.upsell_offers_made += 1
            if rng.random() < HUMAN_ADDON_RATE:
                m.upsell_offers_accepted += 1

    m.avg_order_value_paise = round(m.gmv_paise / m.completed) if m.completed else 0
    return m


# ----------------------------------------------------------------- suites ---


def run_attacks() -> list[attacks.AttackResult]:
    personas = load_personas()
    agent = BuyerAgent(seed=1)
    try:
        manifest = agent.discover()
        with httpx.Client(timeout=10) as c:
            reset(c)
        return attacks.run_all(
            agent, personas[0], manifest["merchant_vpa"], auditor_enabled=auditor_enabled()
        )
    finally:
        agent.close()


def run_benign(count: int = 60) -> list[benign.BenignResult]:
    agent = BuyerAgent(seed=2)
    try:
        manifest = agent.discover()
        with httpx.Client(timeout=10) as c:
            reset(c)
        return benign.run_all(agent, manifest["merchant_vpa"], count=count)
    finally:
        agent.close()


def run_chaos() -> list[chaos.ChaosResult]:
    personas = load_personas()
    agent = BuyerAgent(seed=3)
    try:
        manifest = agent.discover()
        with httpx.Client(timeout=10) as c:
            reset(c)
        return chaos.run_all(agent, personas[0], manifest["merchant_vpa"])
    finally:
        agent.close()


ABLATIONS = [
    ("all on", []),
    ("-replay", ["replay"]),
    ("-scope", ["scope"]),
    ("-ceiling", ["ceiling"]),
    ("-quote", ["quote_binding"]),
    ("-intent", ["intent"]),
]


def run_ablation() -> dict[str, dict[str, str]]:
    """
    Disable one check at a time, rerun the attack set, record what leaks.

    Read the diagonal out loud: every check catches something no other check
    catches. If one turns out redundant, say so and explain why it was kept —
    honesty about a redundant layer beats pretending.
    """
    matrix: dict[str, dict[str, str]] = {}
    personas = load_personas()

    with httpx.Client(timeout=10) as client:
        for label, disabled in ABLATIONS:
            reset(client)
            client.post(f"{GATE}/v1/admin/ablate", json={"disabled": disabled})

            agent = BuyerAgent(seed=4)
            try:
                manifest = agent.discover()
                results = attacks.run_all(
                    agent,
                    personas[0],
                    manifest["merchant_vpa"],
                    auditor_enabled=auditor_enabled(),
                )
            finally:
                agent.close()

            for r in results:
                matrix.setdefault(r.id, {})
                # Worst outcome per attack class wins: if any variant leaks, the
                # class leaks. Averaging would hide the hole.
                current = matrix[r.id].get(label)
                if current == "LEAK":
                    continue
                matrix[r.id][label] = r.outcome

        # Always leave the gate whole.
        client.post(f"{GATE}/v1/admin/ablate", json={"disabled": []})

    return matrix


# ------------------------------------------------------------------- main ---


def main() -> int:
    ap = argparse.ArgumentParser(description="PACT simulation harness")
    ap.add_argument("--arm", choices=list(ARMS))
    ap.add_argument("--sessions", type=int, default=50)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--suite", choices=["attacks", "benign", "chaos", "ablation"])
    ap.add_argument("--all", action="store_true", help="everything, writes results.md")
    ap.add_argument("--uniform", action="store_true", help="ignore persona weights")
    ap.add_argument(
        "--hostile-rate",
        type=float,
        default=hostile.HOSTILE_SESSION_RATE,
        help="share of sessions that are adversarial, in every arm",
    )
    ap.add_argument("--out", default=str(RESULTS))
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    ok, note = services_up()
    if not ok:
        print(note, file=sys.stderr)
        return 2
    print(f"[sim] {note}")

    from sim.report import write_results

    if args.all:
        return write_results(
            sessions=args.sessions,
            seeds=args.seeds,
            uniform=args.uniform,
            out=Path(args.out),
            save_sessions=not args.no_save,
            hostile_rate=args.hostile_rate,
        )

    if args.suite == "attacks":
        for r in run_attacks():
            print(f"  {r.outcome:5} {r.id} {r.name:44} {r.reason_code}")
        return 0

    if args.suite == "benign":
        results = run_benign()
        fpr = benign.false_positive_rate(results)
        for r in results:
            if not r.passed:
                print(f"  FALSE BLOCK  {r.name:50} {r.reason_code}")
        print(f"  {len(results)} benign sessions, false positive rate {fpr:.1%}")
        return 0

    if args.suite == "chaos":
        for r in run_chaos():
            print(f"  {'PASS' if r.passed else 'FAIL'}  {r.id} {r.name:34} -> {r.observed}")
        return 0

    if args.suite == "ablation":
        matrix = run_ablation()
        labels = [label for label, _ in ABLATIONS]
        print("  " + " " * 22 + " | " + " | ".join(f"{l:8}" for l in labels))
        for atk_id, row in sorted(matrix.items()):
            cells = " | ".join(f"{row.get(l, '?'):8}" for l in labels)
            print(f"  {atk_id:22} | {cells}")
        return 0

    if args.arm:
        per_seed: list[ArmMetrics] = []
        for seed in range(args.seeds):
            m, _ = run_arm(
                args.arm,
                sessions=args.sessions,
                seed=seed,
                uniform=args.uniform,
                save=not args.no_save,
                hostile_rate=args.hostile_rate,
            )
            per_seed.append(m)
            print(f"  seed {seed}: {json.dumps(m.to_row())}")
        gmv = [m.gmv_per_100 for m in per_seed]
        mean, lo, hi = mean_and_range(gmv)
        print(f"  GMV/100 across {args.seeds} seed(s): mean {mean:.0f} (range {lo:.0f}-{hi:.0f})")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
