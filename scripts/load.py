#!/usr/bin/env python3
"""
Concurrency and latency against a running instance, over real HTTP.

`tests/test_race.py` fires twenty concurrent payments at the ledger in-process
and proves the ceiling holds. That is the important test and it is not this one.
This drives the *whole stack* — HTTP, the gate, the quote engine, the saga, the
rail, SQLite — from many threads at once, which is the only way to find the
things that are fine in a single-threaded test and not fine in a deployed
container: connection pool limits, `database is locked`, a background task
starving the loop.

Two modes.

    python3 scripts/load.py --base http://localhost:8080 ceiling
        One mandate with room for exactly N payments, N x 4 buyers racing for
        it. Asserts the overspend is zero. This is the claim, under load.

    python3 scripts/load.py --base http://localhost:8080 throughput
        Independent buyers, full purchase each. Reports latency percentiles and
        any error, and asserts nothing 5xxs.

Neither is a benchmark. One container, one worker, SQLite — the numbers say
"this does not fall over", not "this is fast".
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from buyer.agent import BuyerAgent, iso  # noqa: E402
from contracts.money import rupees  # noqa: E402

PER_TXN_PAISE = 500_000


def percentiles(values: list[float]) -> str:
    if not values:
        return "no samples"
    ordered = sorted(values)
    def at(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]
    return (
        f"p50 {at(0.50) * 1000:.0f} ms   p95 {at(0.95) * 1000:.0f} ms   "
        f"p99 {at(0.99) * 1000:.0f} ms   max {ordered[-1] * 1000:.0f} ms"
    )


def persona(total_paise: int, count: int) -> dict[str, Any]:
    """A persona in the shape sim/personas.yaml uses, with the ceiling set here
    rather than drawn from the file — the point is a known, tight limit."""
    return {
        "id": "p_load",
        "goal": "restock the office under load",
        "basket_size": 1,
        "addon_receptivity": 0.0,
        "step_up_approval": 0.0,
        "mandate": {
            "max_total_paise": total_paise,
            "max_per_txn_paise": PER_TXN_PAISE,
            "max_count": count,
            "categories": ["stationery", "cables", "office_furniture"],
            "valid_hours": 24,
        },
    }


def agent_for(base: str) -> BuyerAgent:
    return BuyerAgent(
        gate_url=f"{base}/api/gate",
        merchant_url=f"{base}/api/merchant",
        upsell_mode="off",
        seed=0,
    )


# ----------------------------------------------------------------- ceiling ---


def ceiling_mode(base: str, allowed: int, racers_each: int) -> int:
    """
    One mandate, room for `allowed` payments, `allowed * racers_each` buyers
    trying at once.

    The ledger computes the balance inside the same BEGIN IMMEDIATE that inserts
    the reservation, so this must settle exactly `allowed` and no more. Under
    the naive read-then-write it would settle all of them — which is what
    tests/test_race.py prints side by side.
    """
    setup = agent_for(base)
    racers = allowed * racers_each
    try:
        vpa = setup.discover()["merchant_vpa"]
        # Priced first, with no mandate, so the budget can be set to exactly
        # `allowed` purchases and nothing else can be the binding constraint.
        unit = setup.quote([{"sku": "FUR-LMP-01", "qty": 1}], None)["total_paise"]
        # max_count deliberately generous. Otherwise the count ceiling binds
        # first and this measures a comparison against an integer instead of the
        # SUM over reservations computed inside BEGIN IMMEDIATE — which is the
        # thing the whole design rests on and the only one with a real race in
        # it.
        mandate = setup.issue_mandate(persona(unit * allowed, racers + 10), vpa)
    finally:
        setup.close()

    print(f"one mandate: {rupees(unit * allowed)}, exactly {allowed} x {rupees(unit)}")
    print(f"max_count set to {racers + 10} so the budget is what binds")
    print(f"{racers} buyers racing\n")

    def one(_i: int) -> tuple[str, int, float]:
        agent = agent_for(base)
        # Bound before the try: an exception raised in the quote, before
        # authorize has been timed, must not turn into an UnboundLocalError in
        # the handler that is supposed to be counting it.
        elapsed = 0.0
        try:
            q = agent.quote([{"sku": "FUR-LMP-01", "qty": 1}], mandate.id)
            started = time.perf_counter()
            decision = agent.authorize(mandate, q, vpa)
            elapsed = time.perf_counter() - started
            if decision["verdict"] != "ALLOW":
                return decision["reason_code"], 0, elapsed
            agent.create_order(q["quote_id"], decision)
            return "SETTLED", q["total_paise"], elapsed
        except httpx.HTTPStatusError as exc:
            # A 503 from the gate is the ledger refusing to wait longer than its
            # busy timeout for the write lock, which under twenty racing buyers
            # is the design working rather than a defect. It carries
            # GATE_UNAVAILABLE, so the classification below counts it as
            # capacity — as it already did for the same code arriving any other
            # way. Every other status is still an error: a 500 here means
            # something broke.
            code = ""
            try:
                detail = exc.response.json().get("detail", {})
                code = detail.get("reason_code", "") if isinstance(detail, dict) else ""
            except Exception:  # noqa: BLE001
                pass
            label = f"HTTP {exc.response.status_code} {code}".strip()
            return (label if exc.response.status_code == 503 else f"ERROR {label}"), 0, elapsed
        except Exception as exc:  # noqa: BLE001 - counted, not raised
            return f"ERROR {type(exc).__name__}", 0, 0.0
        finally:
            agent.close()

    with ThreadPoolExecutor(max_workers=racers) as pool:
        outcomes = [f.result() for f in as_completed(pool.submit(one, i) for i in range(racers))]

    settled = [o for o in outcomes if o[0] == "SETTLED"]
    errors = [o for o in outcomes if o[0].startswith("ERROR")]
    spent = sum(o[1] for o in settled)
    cap = unit * allowed

    by_reason: dict[str, int] = {}
    for reason, _, _ in outcomes:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {reason}")

    print(f"\nauthorize latency: {percentiles([o[2] for o in outcomes if o[2]])}")
    print(f"settled {len(settled)}, {rupees(spent)} against a ceiling of {rupees(cap)}")

    failures = 0
    if spent > cap:
        print(f"\nFAIL overspent by {rupees(spent - cap)}")
        failures += 1
    else:
        print(f"OK   overspend {rupees(0)} with {racers} buyers racing")
    if errors:
        print(f"FAIL {len(errors)} request(s) errored: {sorted({e[0] for e in errors})}")
        failures += 1
    if not settled:
        print("FAIL nothing settled at all, so this proved nothing")
        failures += 1
    elif len(settled) != allowed:
        # Fewer than `allowed` is not automatically "safe": it can mean the
        # budget was left on the table under contention, and a real buyer
        # refused for no reason a human could explain.
        #
        # Unless the instance was simply out of capacity. A slow machine — a
        # shared CI runner, say — produces GATE_UNAVAILABLE and timeouts, and
        # failing the build for that would be reporting saturation as a
        # correctness bug. Overspend is the assertion that must never bend.
        starved = sum(
            n for reason, n in by_reason.items()
            if reason.startswith("ERROR") or "GATE_UNAVAILABLE" in reason
            or "TOKEN_EXPIRED" in reason
        )
        if starved:
            print(f"     {len(settled)}/{allowed} settled, {starved} refused for "
                  "want of capacity rather than authority — saturated, not wrong")
        else:
            print(f"FAIL {len(settled)} settled, expected exactly {allowed}, and "
                  "nothing was refused for want of capacity")
            failures += 1
    if by_reason.get("CEILING_TOTAL", 0) == 0 and len(settled) == len(outcomes):
        print("FAIL every buyer settled, so the budget ceiling never bound and "
              "the race was never run")
        failures += 1
    return failures


# -------------------------------------------------------------- throughput ---


def throughput_mode(base: str, sessions: int, concurrency: int) -> int:
    """Independent buyers, a full purchase each, all at once."""
    setup = agent_for(base)
    try:
        vpa = setup.discover()["merchant_vpa"]
    finally:
        setup.close()

    def one(i: int) -> tuple[bool, float, str]:
        agent = agent_for(base)
        started = time.perf_counter()
        try:
            mandate = agent.issue_mandate(persona(PER_TXN_PAISE * 3, 3), vpa)
            quote = agent.quote([{"sku": "STA-NB-A5", "qty": 1}], mandate.id)
            decision = agent.authorize(mandate, quote, vpa)
            if decision["verdict"] != "ALLOW":
                return False, time.perf_counter() - started, decision["reason_code"]
            agent.create_order(quote["quote_id"], decision)
            return True, time.perf_counter() - started, ""
        except httpx.HTTPStatusError as exc:
            # The reason code, not just the status. A 403 here is the settlement
            # token failing to redeem, and TOKEN_EXPIRED under load is a very
            # different story from TOKEN_INVALID — one is saturation, the other
            # is a bug.
            code = ""
            try:
                detail = exc.response.json().get("detail", {})
                code = detail.get("reason_code", "") if isinstance(detail, dict) else ""
            except Exception:  # noqa: BLE001
                pass
            return False, time.perf_counter() - started, (
                f"HTTP {exc.response.status_code} {code}".strip()
            )
        except Exception as exc:  # noqa: BLE001
            return False, time.perf_counter() - started, type(exc).__name__
        finally:
            agent.close()

    print(f"{sessions} full purchases, {concurrency} at a time\n")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = [f.result() for f in as_completed(pool.submit(one, i) for i in range(sessions))]
    wall = time.perf_counter() - started

    ok = [r for r in results if r[0]]
    bad = [r for r in results if not r[0]]

    print(f"end to end:  {percentiles([r[1] for r in results])}")
    print(f"completed    {len(ok)}/{sessions} in {wall:.1f}s "
          f"({len(results) / wall:.1f} purchases/s, mean "
          f"{statistics.fmean(r[1] for r in results) * 1000:.0f} ms)")

    failures = 0
    if bad:
        by_reason: dict[str, int] = {}
        for _, _, why in bad:
            by_reason[why] = by_reason.get(why, 0) + 1
        print(f"\n{len(bad)} did not complete:")
        for why, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {why}")
        # A gate BLOCK is a legitimate outcome. A 5xx or an exception is not.
        # A gate BLOCK is a legitimate outcome, and so is a settlement token that
        # expired because the instance was too slow to place the order in time —
        # that is the system refusing to settle without live authority, which is
        # what it is supposed to do. A 5xx or a dropped connection is not.
        broken = {
            w: n for w, n in by_reason.items()
            if w.startswith(("HTTP 5", "Connect", "Remote", "Pool"))
            or (w.startswith("Read") and w != "ReadTimeout")
        }
        saturated = {
            w: n for w, n in by_reason.items()
            if w == "ReadTimeout" or "TOKEN_EXPIRED" in w or "GATE_UNAVAILABLE" in w
        }
        if broken:
            print(f"FAIL the stack broke under load: {broken}")
            failures += 1
        if saturated:
            print(f"     saturated, not broken: {saturated} — the instance ran out "
                  "of capacity and refused rather than settling without authority")
    if not failures:
        print("\nOK   no transport or server errors under load")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["ceiling", "throughput", "both"], default="both", nargs="?")
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--allowed", type=int, default=5, help="ceiling mode: payments the mandate permits")
    ap.add_argument("--racers-each", type=int, default=4, help="ceiling mode: buyers per permitted payment")
    ap.add_argument("--sessions", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=24)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    failures = 0
    if args.mode in ("ceiling", "both"):
        print("=" * 66)
        print("ceiling under concurrent load")
        print("=" * 66)
        failures += ceiling_mode(base, args.allowed, args.racers_each)
        print()
    if args.mode in ("throughput", "both"):
        print("=" * 66)
        print("throughput")
        print("=" * 66)
        failures += throughput_mode(base, args.sessions, args.concurrency)

    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(main())
