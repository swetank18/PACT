#!/usr/bin/env python3
"""
Smoke test for the single-port build.

Six demo beats were verified by hand once, on one machine, in one topology.
That is not a thing you can keep. This asserts them against a running instance
— the container in CI, or a deployed URL — so a regression in a beat fails a
build instead of failing on stage.

    python3 scripts/smoke.py --base http://localhost:8080

Stdlib only, on purpose: it runs against the container from a host that has no
project dependencies installed, and in CI before anything is pip installed.

Exit code is the number of failed checks, capped at 125.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

TIMEOUT = 120.0


class Failure(Exception):
    pass


def request(
    base: str, path: str, method: str = "GET", body: Any = None, timeout: float = 90.0
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def wait_for_health(base: str, timeout: float) -> dict:
    """
    Poll until the app answers, then return the health payload.

    A container can be running and not yet listening; a compose healthcheck has
    its own start period. Rather than sleep a guessed number of seconds, poll.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            return request(base, "/healthz", timeout=5)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = repr(exc)
            time.sleep(1.0)
    raise Failure(f"{base}/healthz never answered within {timeout:.0f}s (last: {last})")


class SseCounter(threading.Thread):
    """
    Counts frames on an SSE stream in the background.

    This is the check that matters most in the single-port build. Starlette does
    not run a mounted sub-app's lifespan, so the event bus can be unbound to the
    loop while every other endpoint answers normally: the console connects and
    then nothing ever arrives. A frame count above zero is the only cheap proof
    that the mounted lifespans actually ran.
    """

    def __init__(self, base: str, path: str) -> None:
        super().__init__(daemon=True)
        self.url = base + path
        self.frames = 0
        self.error: str | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            req = urllib.request.Request(self.url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                for raw in resp:
                    if self._stop.is_set():
                        return
                    if raw.startswith(b"data:"):
                        self.frames += 1
        except Exception as exc:  # noqa: BLE001 - reported, never raised into main
            if not self._stop.is_set():
                self.error = repr(exc)

    def stop(self) -> None:
        self._stop.set()


# ----------------------------------------------------------------- checks ---


def check_health(base: str, timeout: float) -> str:
    health = wait_for_health(base, timeout)
    if not health.get("ok"):
        raise Failure(f"healthz not ok: {health}")
    missing = {"/api/gate", "/api/merchant", "/api/sim", "/api/webhooks"} - set(
        health.get("mounted", [])
    )
    if missing:
        raise Failure(f"not mounted: {sorted(missing)}")
    if not health.get("console"):
        raise Failure(
            "no console build in the image. The Node stage did not produce "
            "console/dist, or it was not copied across."
        )
    return f"rail={health['rail']} auditor={health['auditor']} console=built"


def check_sub_apps(base: str) -> str:
    for path in ("/api/gate/v1/health", "/api/merchant/v1/health", "/api/sim/v1/health"):
        if not request(base, path).get("ok"):
            raise Failure(f"{path} not ok")
    return "gate, merchant and sim all answer behind their mounts"


def check_console_served(base: str) -> str:
    html = request(base, "/")
    if not isinstance(html, str) or "<div id=\"root\"" not in html:
        raise Failure("/ did not serve the console index")
    if "/assets/" not in html:
        raise Failure("index.html references no built asset bundle")
    return "index.html served with its asset bundle"


def check_beat_1(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/1", "POST")
    if not r.get("completed"):
        raise Failure(f"beat 1 did not complete: {r}")
    if r.get("gmv_paise", 0) <= 0:
        raise Failure(f"beat 1 booked no GMV: {r}")
    return f"purchase completed, {r['gmv_paise']} paise"


def check_beat_2(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/2", "POST")
    if not r.get("completed"):
        raise Failure(f"beat 2 did not complete: {r}")
    if not r.get("upsell_offered"):
        raise Failure(f"beat 2 offered no addon: {r}")
    if not r.get("upsell_accepted"):
        raise Failure(f"beat 2 offered but did not land the addon: {r}")
    return f"addon offered and accepted, {r['gmv_paise']} paise"


def check_beat_3(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/3", "POST")
    if not r.get("offers_made"):
        raise Failure(f"beat 3 made no naive offer, so there is no contrast: {r}")
    if not r.get("rejected_by_gate"):
        raise Failure(f"beat 3's naive offer was not rejected: {r}")
    if r.get("completed"):
        raise Failure(f"beat 3 completed; the un-repairable session must die: {r}")
    codes = set(r.get("reason_codes", []))
    if not codes:
        raise Failure(f"beat 3 produced no BLOCK reason code: {r}")
    return f"naive offer refused, {sorted(codes)}"


def check_beat_4(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/4", "POST")
    atks = r.get("attacks", [])
    if len(atks) != 4:
        raise Failure(f"beat 4 ran {len(atks)} attacks, expected 4: {r}")
    allowed = [a for a in atks if a["verdict"] == "ALLOW"]
    if allowed:
        raise Failure(f"beat 4 let an attack through: {allowed}")
    codes = {a["reason_code"] for a in atks}
    if len(codes) != 4:
        raise Failure(
            f"beat 4 must show four *different* failures, got {sorted(codes)}"
        )
    return f"4 attacks stopped, {sorted(codes)}"


def check_beat_5(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/5", "POST")
    if not r.get("recovered"):
        raise Failure(f"beat 5 did not recover the sale: {r}")
    if r.get("refunded_paise", 0) <= 0:
        raise Failure(f"beat 5 recovered without refunding: {r}")
    return f"rolled back, {r['refunded_paise']} paise refunded, sale recovered"


def check_beat_6(base: str) -> str:
    r = request(base, "/api/sim/demo/beat/6", "POST")
    if r.get("error"):
        raise Failure(f"beat 6: {r['error']}")
    if r.get("duplicates_recognised", 0) < 2:
        raise Failure(f"beat 6 did not recognise the duplicates: {r}")
    if not r.get("state_unchanged"):
        raise Failure(f"beat 6 changed order state on a duplicate webhook: {r}")
    return f"{r['duplicates_recognised']} duplicate deliveries absorbed"


def check_reset(base: str) -> str:
    started = time.monotonic()
    r = request(base, "/api/sim/admin/reset", "POST", timeout=30)
    if not r.get("ok"):
        raise Failure(f"reset did not report ok: {r}")
    took = time.monotonic() - started
    if took > 15:
        raise Failure(f"reset took {took:.1f}s; something is blocking the loop")
    return f"cleared in {took * 1000:.0f} ms"


BEATS = (
    ("beat 1  happy path", check_beat_1),
    ("beat 2  headroom upsell", check_beat_2),
    ("beat 3  naive upsell refused", check_beat_3),
    ("beat 4  four attacks", check_beat_4),
    ("beat 5  stockout and recovery", check_beat_5),
    ("beat 6  duplicate webhook", check_beat_6),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--timeout", type=float, default=TIMEOUT,
                    help="seconds to wait for the app to start answering")
    ap.add_argument("--skip-beats", action="store_true",
                    help="health and mounts only; for a deployed instance you "
                         "do not want to write orders into")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    results: list[tuple[str, bool, str]] = []

    def run(name: str, fn, *a) -> bool:
        try:
            detail = fn(*a)
        except Exception as exc:  # noqa: BLE001 - a smoke test reports, never crashes
            results.append((name, False, str(exc)))
            print(f"FAIL  {name}\n      {exc}", flush=True)
            return False
        results.append((name, True, detail))
        print(f"ok    {name}\n      {detail}", flush=True)
        return True

    print(f"smoke: {base}\n", flush=True)

    if not run("health and mounts", check_health, base, args.timeout):
        # Nothing downstream can mean anything if the app is not up.
        return 1

    run("sub-apps answer", check_sub_apps, base)
    run("console served", check_console_served, base)

    if not args.skip_beats:
        # Subscribed before the beats run, so the frames it counts are theirs.
        sse = SseCounter(base, "/api/merchant/v1/stream")
        sse.start()
        time.sleep(1.0)

        # The console binds this to `0`, so a failure here is a failure on
        # stage. It is also the call that deadlocked the mounted build once:
        # blocking HTTP back into the same process, awaited on its own loop.
        run("reset clears state", check_reset, base)

        for name, fn in BEATS:
            run(name, fn, base)

        time.sleep(1.0)
        sse.stop()

        def check_sse() -> str:
            if sse.error and sse.frames == 0:
                raise Failure(f"SSE stream failed: {sse.error}")
            if sse.frames == 0:
                raise Failure(
                    "no SSE frames during six beats. The mounted sub-app "
                    "lifespans did not run, so the event bus is not bound to "
                    "the loop — the console would connect and show nothing."
                )
            return f"{sse.frames} frames delivered to a live subscriber"

        run("SSE live during the beats", check_sse)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("failed: " + ", ".join(r[0] for r in failed), flush=True)
    return min(len(failed), 125)


if __name__ == "__main__":
    sys.exit(main())
