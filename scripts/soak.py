#!/usr/bin/env python3
"""
The same instance, under steady load, for hours.

`scripts/load.py` answers "does it fall over in a burst". It runs for about a
minute, so everything it measures is a peak. Two questions it cannot answer are
the ones that decide whether a deployed instance survives a weekend:

    Does memory come back down?      Fly gives this 512 MB.
    Does the volume fill?            Fly gives this 1 GB.

Both fail slowly and neither shows up in a burst. A listener that is never
removed, a saga task that is never awaited, a WAL that is never checkpointed —
all of them are invisible at sixty seconds and fatal at six hours, and the way
they present in production is a health check that stays green until the process
is OOM-killed mid-purchase.

So this holds a *modest* rate — deliberately well under the 53/s ceiling
load.py found — and watches the shape of the process instead of its speed:

    python3 scripts/soak.py --base http://localhost:8080 --minutes 60

What it samples, every window:

    RSS, threads, open file descriptors     /proc, so the instance must be
                                            on this machine to be sampled
    database bytes, including -wal and -shm the volume, in other words
    latency and errors in that window       drift, not a peak

What it asserts at the end:

    no transport or server errors           a soak that 5xxs is not a soak
    RSS is flat, not climbing               against the 512 MB the machine has
    file descriptors do not accumulate      the classic SSE listener leak
    latency at the end matches the start    a slow leak shows here first
    the merchant's ledger matches the       the harness counts what it settled;
    purchases this made, to the paise       the merchant counts independently

The console polls and an SSE subscriber stays connected throughout, because
that is what a demo instance is actually doing while it sits there: one browser
open on the merchant console for the length of the event.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from buyer.agent import BuyerAgent  # noqa: E402
from contracts.money import rupees  # noqa: E402

PER_TXN_PAISE = 500_000
SKU = "STA-NB-A5"


# ------------------------------------------------------------- the instance ---


def listening_pid(port: int) -> int | None:
    """
    The pid listening on `port`, found through /proc rather than `lsof`, which
    is not installed everywhere and needs a subprocess where this needs none.

    Returns None whenever the answer would be a guess — a remote instance, a
    process owned by somebody else, a container. The caller reports memory as
    unsampled rather than reporting a number for the wrong process, which is
    the failure mode that makes a soak worse than not running one.
    """
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # 0A = LISTEN
                continue
            if int(fields[1].split(":")[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return None

    want = {f"socket:[{inode}]" for inode in inodes}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                if os.readlink(fd) in want:
                    return int(entry.name)
        except OSError:
            continue  # not ours, or gone between the listdir and the readlink
    return None


def looks_like_the_app(pid: int) -> str | None:
    """
    The command line of `pid`, if it plausibly is this app.

    Behind Docker the process listening on the published port is `docker-proxy`,
    not uvicorn, and sampling its RSS would produce a flat, meaningless line
    reported with total confidence. Better to say nothing: pass `--pid` (the
    container's main process, from `docker inspect -f '{{.State.Pid}}'`) when
    the instance is containerised.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return None
    return cmdline if ("python" in cmdline or "uvicorn" in cmdline) else None


@dataclass
class Process:
    """What /proc can say about the instance, or nothing if it is not local."""

    pid: int | None

    def rss_mb(self) -> float | None:
        if self.pid is None:
            return None
        try:
            status = Path(f"/proc/{self.pid}/status").read_text()
        except OSError:
            return None
        match = re.search(r"^VmRSS:\s+(\d+) kB", status, re.M)
        return int(match.group(1)) / 1024 if match else None

    def threads(self) -> int | None:
        if self.pid is None:
            return None
        try:
            status = Path(f"/proc/{self.pid}/status").read_text()
        except OSError:
            return None
        match = re.search(r"^Threads:\s+(\d+)", status, re.M)
        return int(match.group(1)) if match else None

    def fds(self) -> int | None:
        if self.pid is None:
            return None
        try:
            return len(os.listdir(f"/proc/{self.pid}/fd"))
        except OSError:
            return None


def database_bytes(db_path: Path | None) -> int | None:
    """
    The file, plus its -wal and -shm.

    Counting only the database file understates a WAL-mode SQLite badly: the
    write-ahead log is where the last N pages live, it is what grows between
    checkpoints, and it is on the same volume. A soak that watched `pact.db`
    alone would have reported a flat line while the disk filled.
    """
    if db_path is None:
        return None
    total = 0
    seen = False
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        try:
            total += candidate.stat().st_size
            seen = True
        except OSError:
            continue
    return total if seen else None


def database_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    url = os.environ.get("PACT_DB_URL", "sqlite:///./pact.db")
    if not url.startswith("sqlite:///"):
        return None
    path = Path(url[len("sqlite:///") :])
    return path if path.exists() else None


# ----------------------------------------------------------------- the load ---


def persona() -> dict[str, Any]:
    """One purchase per mandate, so nothing accumulates in the ledger that a
    real buyer would not accumulate. The soak is about the instance, not about
    exhausting a budget — load.py already races the ceiling."""
    return {
        "id": "p_soak",
        "goal": "keep the office stocked, indefinitely",
        "basket_size": 1,
        "addon_receptivity": 0.0,
        "step_up_approval": 0.0,
        "mandate": {
            "max_total_paise": PER_TXN_PAISE * 2,
            "max_per_txn_paise": PER_TXN_PAISE,
            "max_count": 2,
            "categories": ["stationery", "cables", "office_furniture"],
            "valid_hours": 24,
        },
    }


@dataclass
class Tally:
    """Everything the workers record, behind one lock. Contended once per
    purchase, which at these rates is nothing."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    settled: int = 0
    settled_paise: int = 0
    latencies: list[float] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    sse_frames: int = 0
    sse_reconnects: int = 0
    polls: int = 0

    def record(self, reason: str, paise: int, elapsed: float) -> None:
        with self.lock:
            self.reasons[reason] = self.reasons.get(reason, 0) + 1
            self.latencies.append(elapsed)
            if reason == "SETTLED":
                self.settled += 1
                self.settled_paise += paise

    def window(self) -> tuple[int, list[float]]:
        """Drain what has happened since the last call. The sampler owns this;
        nothing else drains, so a window is exactly one sampling interval."""
        with self.lock:
            taken = self.latencies
            self.latencies = []
            return self.settled, taken


def buyer(base: str) -> BuyerAgent:
    return BuyerAgent(
        gate_url=f"{base}/api/gate",
        merchant_url=f"{base}/api/merchant",
        upsell_mode="off",
        seed=0,
    )


def purchase_loop(base: str, vpa: str, tally: Tally, deadline: float, think: float) -> None:
    """A buyer, over and over, with a pause between purchases.

    A fresh agent per purchase on purpose: it is a fresh HTTP connection, which
    is what a real fleet of agents looks like and is the only way this exercises
    connection setup and teardown. A pooled client would hide an fd leak on the
    server by never opening a new socket.
    """
    while time.time() < deadline:
        agent = buyer(base)
        started = time.perf_counter()
        try:
            mandate = agent.issue_mandate(persona(), vpa)
            quote = agent.quote([{"sku": SKU, "qty": 1}], mandate.id)
            decision = agent.authorize(mandate, quote, vpa)
            if decision["verdict"] != "ALLOW":
                tally.record(decision["reason_code"], 0, time.perf_counter() - started)
            else:
                agent.create_order(quote["quote_id"], decision)
                tally.record("SETTLED", quote["total_paise"], time.perf_counter() - started)
        except httpx.HTTPStatusError as exc:
            code = ""
            try:
                detail = exc.response.json().get("detail", {})
                code = detail.get("reason_code", "") if isinstance(detail, dict) else ""
            except Exception:  # noqa: BLE001
                pass
            tally.record(
                f"HTTP {exc.response.status_code} {code}".strip(),
                0,
                time.perf_counter() - started,
            )
        except Exception as exc:  # noqa: BLE001 - counted, not raised
            tally.record(type(exc).__name__, 0, time.perf_counter() - started)
        finally:
            agent.close()
        time.sleep(think)


def sse_loop(base: str, tally: Tally, deadline: float) -> None:
    """One subscriber on the merchant's event stream for the whole run.

    This is the leak that matters most here. Every order publishes to the bus,
    and if a subscriber's queue is unbounded or its listener is never removed on
    disconnect, this is the thread that turns it into a graph that only goes up.
    It reconnects rather than giving up, and the reconnect count is reported:
    a stream that drops every few minutes is a finding, not noise.
    """
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, read=60.0)) as client:
                with client.stream("GET", f"{base}/api/merchant/v1/stream") as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if time.time() > deadline:
                            return
                        if line.startswith("data:"):
                            with tally.lock:
                                tally.sse_frames += 1
        except Exception:  # noqa: BLE001 - a drop is data, not a crash
            with tally.lock:
                tally.sse_reconnects += 1
            time.sleep(1.0)


def restock_loop(base: str, deadline: float, every: float) -> None:
    """Stock back to its initial levels, often.

    Once per sampling window was not enough and the first run proved it: forty
    notebooks against five purchases a second is eight seconds of stock, so two
    thirds of the run captured, failed to fulfil and refunded. That is a real
    path and it has its own tests; it is not what a soak is for, and it makes
    the merchant's GMV disagree with the harness for a reason that says nothing
    about the instance.
    """
    with httpx.Client(timeout=10.0) as client:
        while time.time() < deadline:
            try:
                client.post(f"{base}/api/merchant/admin/restock")
            except Exception:  # noqa: BLE001 - the workers report an outage
                pass
            time.sleep(every)


def console_loop(base: str, tally: Tally, deadline: float) -> None:
    """What an open console does: poll the three feeds it renders.

    Included because a browser left open on the merchant console for the length
    of an event is the most likely long-lived client this will ever have, and
    the feeds it polls are the ones whose result sets grow with every order.
    """
    paths = (
        "/api/merchant/v1/stats",
        "/api/merchant/v1/orders",
        "/api/gate/v1/decisions",
    )
    with httpx.Client(timeout=10.0) as client:
        while time.time() < deadline:
            for path in paths:
                try:
                    client.get(f"{base}{path}")
                    with tally.lock:
                        tally.polls += 1
                except Exception:  # noqa: BLE001 - counted by the workers
                    pass
            time.sleep(2.0)


# ------------------------------------------------------------------ samples ---


@dataclass
class Sample:
    at: float
    rss_mb: float | None
    threads: int | None
    fds: int | None
    db_bytes: int | None
    settled: int
    latencies: list[float]

    def p50(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    def p99(self) -> float | None:
        if not self.latencies:
            return None
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]


def human_bytes(value: int | None) -> str:
    if value is None:
        return "     —"
    return f"{value / 1_048_576:6.1f}M"


def drain(base: str, expected_orders: int, timeout: float = 60.0) -> tuple[dict, float]:
    """
    Wait for the saga to finish the orders that were still in flight.

    The workers stop at the deadline with a few purchases part way through —
    captured, not yet fulfilled — because the saga runs as a background task.
    Reading the merchant's stats at that instant reports six fewer orders than
    the harness settled and calls it a ledger mismatch, which is the harness
    being impatient rather than the instance being wrong.

    How long this takes is itself worth printing: a saga that will not drain in
    a minute after the load stops is a finding.
    """
    started = time.perf_counter()
    stats: dict = {}
    with httpx.Client(timeout=30.0) as client:
        while True:
            stats = client.get(f"{base}/api/merchant/v1/stats").json()
            if stats["orders"] >= expected_orders or time.perf_counter() - started > timeout:
                return stats, time.perf_counter() - started
            time.sleep(0.5)


def order_states(base: str) -> dict[str, int]:
    """The state of the last 200 orders, for diagnosing a ledger mismatch."""
    try:
        with httpx.Client(timeout=15.0) as client:
            orders = client.get(f"{base}/api/merchant/v1/orders", params={"limit": 200}).json()
    except Exception:  # noqa: BLE001
        return {}
    counts: dict[str, int] = {}
    for order in orders.get("orders", []):
        state = str(order.get("state", "?"))
        counts[state] = counts.get(state, 0) + 1
    return counts


def slope_per_hour(samples: list[Sample]) -> float | None:
    """
    Least squares through the RSS samples, in MB/hour.

    First against last is the obvious way to do this and it is wrong at these
    timescales: the difference between two single samples is mostly the noise of
    when the allocator last released, so a ninety-second run extrapolates a
    1.6 MB step into "63 MB/hour, dead in six hours". A fitted line over every
    post-warmup sample says what the run actually did.
    """
    points = [(s.at, s.rss_mb) for s in samples if s.rss_mb is not None]
    if len(points) < 3:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return (numerator / denominator) * 3600


# ------------------------------------------------------------------ the run ---


def soak(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    port = httpx.URL(base).port or (443 if base.startswith("https") else 80)

    setup = buyer(base)
    try:
        vpa = setup.discover()["merchant_vpa"]
    finally:
        setup.close()

    with httpx.Client(timeout=10.0) as client:
        # Stock is finite and in memory — forty of the SKU this buys. Left
        # alone, the first minute exhausts it and every purchase after that
        # captures, fails to fulfil and refunds, so the run would measure the
        # rollback path for an hour and call it a soak. `restock_loop` keeps it
        # topped up; the orders stay, which is the point.
        client.post(f"{base}/api/merchant/admin/restock")
        before = client.get(f"{base}/api/merchant/v1/stats").json()

    if args.pid:
        process = Process(args.pid)
        found = f"process {args.pid}, given with --pid"
    elif httpx.URL(base).host in ("localhost", "127.0.0.1"):
        pid = listening_pid(port)
        if pid and looks_like_the_app(pid):
            process, found = Process(pid), f"process {pid}"
        elif pid:
            process = Process(None)
            found = (f"pid {pid} holds the port but is not this app — a container's "
                     "published port, most likely. Pass --pid.")
        else:
            process, found = Process(None), "nothing on this machine owns that port"
    else:
        process, found = Process(None), "the instance is not on this machine"
    db_path = database_path(args.db)

    print(f"soak: {base}")
    print(f"  {args.minutes} minutes, {args.concurrency} buyers, {args.think}s between purchases")
    if process.pid:
        print(f"  {found}, sampled from /proc every {args.sample}s")
    else:
        print(f"  memory NOT sampled — {found}")
    if db_path:
        print(f"  database {db_path} (+ -wal, -shm)")
    else:
        print("  database NOT sampled — pass --db, or PACT_DB_URL is not a local sqlite file")
    print()

    tally = Tally()
    deadline = time.time() + args.minutes * 60
    started = time.time()
    samples: list[Sample] = []

    print("   elapsed      rss  thr   fd       db   settled     p50     p99")
    print("   " + "-" * 63)

    pool = ThreadPoolExecutor(max_workers=args.concurrency + 3)
    for _ in range(args.concurrency):
        pool.submit(purchase_loop, base, vpa, tally, deadline, args.think)
    pool.submit(sse_loop, base, tally, deadline)
    pool.submit(console_loop, base, tally, deadline)
    pool.submit(restock_loop, base, deadline, args.restock)

    last_settled = 0
    try:
        while time.time() < deadline:
            time.sleep(min(args.sample, max(0.0, deadline - time.time())))
            settled, latencies = tally.window()
            sample = Sample(
                at=time.time() - started,
                rss_mb=process.rss_mb(),
                threads=process.threads(),
                fds=process.fds(),
                db_bytes=database_bytes(db_path),
                settled=settled - last_settled,
                latencies=latencies,
            )
            last_settled = settled
            samples.append(sample)
            p50 = sample.p50()
            p99 = sample.p99()
            print(
                f"   {sample.at / 60:6.1f}m  "
                f"{(f'{sample.rss_mb:6.1f}M' if sample.rss_mb else '     —')}  "
                f"{(sample.threads or 0):3d}  "
                f"{(sample.fds or 0):3d}  "
                f"{human_bytes(sample.db_bytes)}  "
                f"{sample.settled:8d}  "
                f"{(f'{p50 * 1000:5.0f}' if p50 else '    —')}   "
                f"{(f'{p99 * 1000:5.0f}' if p99 else '    —')}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\n  interrupted — reporting what was measured up to here")
        deadline = 0.0
    finally:
        pool.shutdown(wait=True)

    # The load stopped here; the drain that follows is not part of the run and
    # would otherwise be counted into every per-second figure below.
    wall = time.time() - started
    after, drained_in = drain(base, before["orders"] + tally.settled)
    failures = report(args, tally, samples, before, after, wall, drained_in)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "base": base,
            "minutes": args.minutes,
            "concurrency": args.concurrency,
            "wall_seconds": round(wall, 1),
            "settled": tally.settled,
            "settled_paise": tally.settled_paise,
            "reasons": tally.reasons,
            "sse_frames": tally.sse_frames,
            "sse_reconnects": tally.sse_reconnects,
            "console_polls": tally.polls,
            "stats_before": before,
            "stats_after": after,
            "drained_seconds": round(drained_in, 2),
            "failures": failures,
            "samples": [
                {
                    "at_seconds": round(s.at, 1),
                    "rss_mb": s.rss_mb,
                    "threads": s.threads,
                    "fds": s.fds,
                    "db_bytes": s.db_bytes,
                    "settled": s.settled,
                    "p50_ms": round(s.p50() * 1000, 1) if s.p50() else None,
                    "p99_ms": round(s.p99() * 1000, 1) if s.p99() else None,
                }
                for s in samples
            ],
        }, indent=2) + "\n")
        print(f"samples written to {args.json}")

    return failures


# ------------------------------------------------------------------ verdict ---


def report(
    args: argparse.Namespace,
    tally: Tally,
    samples: list[Sample],
    before: dict,
    after: dict,
    wall: float,
    drained_in: float,
) -> int:
    print()
    print("=" * 66)
    print(f"{wall / 60:.1f} minutes, {tally.settled} purchases settled, "
          f"{tally.settled / max(wall, 1):.1f}/s")
    print("=" * 66)

    for reason, n in sorted(tally.reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {reason}")
    print(f"  {tally.sse_frames:>6}  SSE frames on one subscriber "
          f"({tally.sse_reconnects} reconnects)")
    print(f"  {tally.polls:>6}  console polls")
    print()

    failures = 0

    # ------------------------------------------------------------ errors ---
    # Same classification load.py uses. A gate BLOCK is an outcome; a 5xx or a
    # dropped connection is a bug. TOKEN_EXPIRED is saturation, which at this
    # rate would itself be a finding, so it is called out rather than excused.
    broken = {
        reason: n for reason, n in tally.reasons.items()
        if reason.startswith(("HTTP 5", "Connect", "Remote", "Pool", "ReadError"))
    }
    saturated = {
        reason: n for reason, n in tally.reasons.items()
        if "TOKEN_EXPIRED" in reason or "GATE_UNAVAILABLE" in reason
        or reason == "ReadTimeout"
    }
    if broken:
        print(f"FAIL the stack broke: {broken}")
        failures += 1
    else:
        print("OK   no transport or server errors for the length of the run")
    if saturated:
        print(f"     {saturated} — refused for want of capacity, at a rate chosen to "
              "be well below the ceiling. Worth explaining.")

    if not samples:
        print("FAIL nothing was sampled, so this proved nothing")
        return failures + 1

    # ------------------------------------------------------------ memory ---
    # The first sample is discarded everywhere below: the process is still
    # warming its connection pools and importing lazily, so including it turns
    # ordinary start-up into "a leak" and would make this fail every run.
    warm = samples[1:] or samples
    rss = [s.rss_mb for s in warm if s.rss_mb is not None]
    if not rss:
        print("     memory not sampled — remote instance")
    else:
        floor, ceiling = min(rss), max(rss)
        per_hour = slope_per_hour(warm)
        trend = f"{per_hour:+.1f} MB/hour" if per_hour is not None else "too few samples to fit"
        print(f"OK   RSS {floor:.0f}–{ceiling:.0f} MB, ended {rss[-1]:.0f} MB ({trend})")
        # Two ways to fail, because either alone is wrong. A flat 480 MB does
        # not survive a 512 MB machine, and 40 MB/hour is a leak even though
        # every sample is small.
        if ceiling > args.max_rss_mb:
            print(f"FAIL RSS reached {ceiling:.0f} MB against a "
                  f"{args.max_rss_mb:.0f} MB machine")
            failures += 1
        if per_hour is None or wall < args.min_trend_minutes * 60:
            print(f"     the trend is not judged under {args.min_trend_minutes:.0f} minutes — "
                  "start-up dominates a short run and would be reported as a leak")
        elif per_hour > args.max_growth_mb_hour:
            headroom = (args.max_rss_mb - rss[-1]) / per_hour
            print(f"FAIL RSS is climbing at {per_hour:.1f} MB/hour — at this rate it "
                  f"reaches {args.max_rss_mb:.0f} MB in {headroom:.1f} hours")
            failures += 1

    # ------------------------------------------- descriptors and threads ---
    fds = [s.fds for s in warm if s.fds is not None]
    if fds:
        print(f"OK   file descriptors {min(fds)}–{max(fds)}, ended {fds[-1]}")
        if fds[-1] > fds[0] + args.max_fd_growth:
            print(f"FAIL {fds[-1] - fds[0]} descriptors accumulated — a listener or a "
                  "connection is not being closed")
            failures += 1
    threads = [s.threads for s in warm if s.threads is not None]
    if threads:
        print(f"OK   threads {min(threads)}–{max(threads)}, ended {threads[-1]}")
        if threads[-1] > threads[0] + args.max_thread_growth:
            print(f"FAIL {threads[-1] - threads[0]} threads accumulated")
            failures += 1

    # ------------------------------------------------------------ volume ---
    sizes = [s.db_bytes for s in warm if s.db_bytes is not None]
    if sizes and tally.settled:
        grown = sizes[-1] - sizes[0]
        per_order = grown / max(tally.settled, 1)
        print(f"OK   database {sizes[0] / 1_048_576:.1f} → {sizes[-1] / 1_048_576:.1f} MB, "
              f"peak {max(sizes) / 1_048_576:.1f} MB "
              f"({per_order:+.0f} bytes per order)")
        if per_order > 0:
            room = args.volume_mb * 1_048_576 - sizes[-1]
            orders = room / per_order
            print(f"     a {args.volume_mb:.0f} MB volume holds "
                  f"{orders:,.0f} more orders at this rate "
                  f"({orders / max(tally.settled / (wall / 3600), 1):,.0f} hours "
                  "at the rate this ran)")
        else:
            print("     the database did not grow across the run")

    # ------------------------------------------------------------ drift ---
    first, last = warm[0], warm[-1]
    if first.p50() and last.p50():
        drift = last.p50() / first.p50()
        print(f"OK   p50 {first.p50() * 1000:.0f} ms at the start, "
              f"{last.p50() * 1000:.0f} ms at the end ({drift:.2f}x)")
        if drift > args.max_latency_drift and last.p50() > 0.25:
            print(f"FAIL latency degraded {drift:.1f}x over the run")
            failures += 1

    # --------------------------------------------------------- the ledger ---
    # The harness counted what it settled. The merchant counted independently,
    # server side, from its own tables. If those two disagree the soak found
    # something far more interesting than a memory graph.
    gmv_delta = after["gmv_paise"] - before["gmv_paise"]
    order_delta = after["orders"] - before["orders"]
    print()
    print(f"OK   the saga drained the in-flight orders in {drained_in:.1f}s after the "
          "load stopped")
    if gmv_delta == tally.settled_paise and order_delta == tally.settled:
        print(f"OK   the merchant's ledger agrees to the paise: {order_delta} orders, "
              f"{rupees(gmv_delta)}")
    else:
        print(f"FAIL harness settled {tally.settled} orders / "
              f"{rupees(tally.settled_paise)}; the merchant recorded "
              f"{order_delta} / {rupees(gmv_delta)}")
        # The difference is almost always states, not money: an order the
        # harness placed and the merchant then rolled back is settled to one
        # and not GMV to the other, and both are right. Print the distribution
        # rather than leaving whoever reads this to go and query SQLite.
        print("     recent order states, which is where the difference will be:")
        for state, n in sorted(order_states(args.base.rstrip("/")).items(),
                               key=lambda kv: -kv[1]):
            print(f"       {n:>5}  {state}")
        failures += 1

    if after.get("needs_attention"):
        print(f"FAIL {after['needs_attention']} order(s) parked in NEEDS_ATTENTION")
        failures += 1

    print()
    print("PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--concurrency", type=int, default=4,
                    help="buyers in flight; deliberately far below the ceiling")
    ap.add_argument("--think", type=float, default=0.5,
                    help="seconds a buyer waits between purchases")
    ap.add_argument("--sample", type=float, default=30, help="seconds between samples")
    ap.add_argument("--restock", type=float, default=5.0,
                    help="seconds between restocks; stock is finite and this outruns it")
    ap.add_argument("--pid", type=int, default=None,
                    help="the instance's process; needed when it is in a container, where "
                         "the port is held by docker-proxy rather than by uvicorn")
    ap.add_argument("--db", default=None, help="sqlite file to watch; defaults to PACT_DB_URL")
    ap.add_argument("--volume-mb", type=float, default=1024,
                    help="the volume this would deploy onto (fly.toml and render.yaml say 1 GB)")
    ap.add_argument("--max-rss-mb", type=float, default=512,
                    help="the machine this would deploy onto (fly.toml says 512 MB)")
    ap.add_argument("--max-growth-mb-hour", type=float, default=8.0)
    ap.add_argument("--max-fd-growth", type=int, default=64)
    ap.add_argument("--max-thread-growth", type=int, default=16)
    ap.add_argument("--max-latency-drift", type=float, default=3.0)
    ap.add_argument("--min-trend-minutes", type=float, default=15.0,
                    help="below this the memory trend is reported and not judged")
    ap.add_argument("--json", default=None, help="also write every sample here")
    args = ap.parse_args()

    return min(soak(args), 125)


if __name__ == "__main__":
    sys.exit(main())
