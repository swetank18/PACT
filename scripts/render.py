#!/usr/bin/env python3
"""
Create and drive the Render service, through Render's API rather than the
dashboard.

    export RENDER_API_KEY=rnd_…
    python3 scripts/render.py create            # make the service, wait, smoke it
    python3 scripts/render.py status            # what is deployed right now
    python3 scripts/render.py deploy            # redeploy :latest onto it
    python3 scripts/render.py env KEY=VALUE …   # set secrets, then redeploy

It creates exactly what `render.yaml` describes — one instance, a 1 GB disk at
/data, the Starter plan — so the live service and the committed blueprint say
the same thing. `render.yaml` remains the record; this is the way to execute it
without the dashboard.

`--plan free` is accepted and drops the disk, because Render does not allow one
on a free instance. What that costs is printed rather than left implicit:

  * `/data` is ephemeral. Every restart issues a new gate signing key and
    forgets every mandate, so headroom envelopes already handed out stop
    verifying.
  * A free instance sleeps after fifteen minutes of no traffic. Asleep, it is
    not running the reservation sweeper or the reconciliation poller, and the
    first request afterwards waits on a cold start.

Stdlib only, like `smoke.py`, so it runs from anywhere this repo is checked out.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = "https://api.render.com/v1"

# The image CI publishes *after* the beats have run against it, which is the
# only artefact worth deploying. Kept in step with render.yaml.
IMAGE = "ghcr.io/swetank18/pact:latest"
SERVICE = "pact"
REGION = "singapore"
HEALTH = "/healthz"

# The volume the ledger and the gate's signing key live on. Render allows a
# disk only on a paid instance, which is the whole reason `render.yaml` says
# `starter` rather than `free`.
DISK = {"name": "pact-data", "mountPath": "/data", "sizeGB": 1}

# The non-secret half of render.yaml's envVars. The four marked `sync: false`
# there are secrets and are never defaulted here — unset runs the mock_upi rail
# and the gate's deterministic mode, which is a complete system by design.
ENV: dict[str, str] = {
    "PACT_PROFILE": "razorpay-track01",
    "PACT_DB_URL": "sqlite:////data/pact.db",
    "PACT_GATE_KEY_PATH": "/data/gate_signing_key.hex",
    "PORT": "8080",
    "PACT_SAGA_STEP_DELAY_S": "0.35",
}


class RenderError(RuntimeError):
    pass


def agrees_with_blueprint(plan: str) -> None:
    """
    Refuse to create a service that says something `render.yaml` does not.

    The blueprint is the committed record of this deployment, and a live
    service that quietly disagrees with it is worse than no blueprint at all —
    the next person reads the file and believes it. So every value this script
    is about to send is looked for in that file first, and a mismatch stops the
    deploy rather than producing one nobody can reason about.

    Read as text rather than parsed: this is stdlib only, and a substring check
    that is occasionally too strict is a better failure than a YAML dependency.
    """
    path = pathlib.Path(__file__).resolve().parent.parent / "render.yaml"
    if not path.exists():
        raise RenderError(f"{path} is missing — nothing to check against")
    blueprint = path.read_text()

    expected = {
        "image": IMAGE,
        "region": REGION,
        "health check path": HEALTH,
        "disk mount path": DISK["mountPath"],
        "disk name": DISK["name"],
        **{f"env {k}": v for k, v in ENV.items()},
    }
    if plan != "free":
        expected["plan"] = plan

    off = [f"{what} ({value!r})" for what, value in expected.items() if value not in blueprint]
    if off:
        raise RenderError(
            "render.yaml does not mention " + ", ".join(off) + " — the blueprint and "
            "this script have drifted, and one of them is wrong. Fix that before deploying."
        )


def api(path: str, method: str = "GET", body: Any = None) -> Any:
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        raise RenderError("RENDER_API_KEY is not set")

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        # Render's message is more useful than anything this script could
        # infer, so it is printed rather than summarised away.
        raise RenderError(f"{e.code} {method} {path}: {e.read().decode()[:600]}") from None


def owner_id() -> str:
    owners = api("/owners?limit=20")
    if not owners:
        raise RenderError("this key can see no workspaces")
    owner = owners[0]["owner"]
    print(f"   workspace: {owner['name']} ({owner['id']})")
    return owner["id"]


def find_service() -> dict | None:
    for row in api(f"/services?name={SERVICE}&limit=20") or []:
        svc = row["service"]
        if svc["name"] == SERVICE:
            return svc
    return None


def create(plan: str) -> dict:
    agrees_with_blueprint(plan)
    owner = owner_id()
    details: dict[str, Any] = {
        "runtime": "image",
        "region": REGION,
        "plan": plan,
        "healthCheckPath": HEALTH,
        # One instance, always. The ledger's correctness rests on SQLite's
        # write lock and the saga runs in-process; a second instance would
        # keep its own answer to how much budget is left.
        "numInstances": 1,
    }
    if plan != "free":
        details["disk"] = dict(DISK)

    payload = {
        "type": "web_service",
        "name": SERVICE,
        "ownerId": owner,
        "image": {"ownerId": owner, "imagePath": IMAGE},
        "serviceDetails": details,
        "envVars": [{"key": k, "value": v} for k, v in ENV.items()],
    }
    disk = "1 GB disk at /data" if "disk" in details else "no disk"
    print(f"   creating {SERVICE} · {plan} · {REGION} · {disk} · {IMAGE}")
    created = api("/services", "POST", payload)
    return created["service"] if "service" in created else created


def wait_live(service_id: str, timeout: float = 900.0) -> str:
    """Block until the newest deploy settles, and say which way it settled."""
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        deploys = api(f"/services/{service_id}/deploys?limit=1")
        if deploys:
            d = deploys[0]["deploy"]
            status = d.get("status", "?")
            if status != last:
                print(f"   {int(time.time() - started):>4}s  {status}")
                last = status
            if status == "live":
                return status
            if status in {"build_failed", "update_failed", "canceled", "pre_deploy_failed"}:
                raise RenderError(f"deploy ended {status}: https://dashboard.render.com/web/{service_id}")
        time.sleep(10)
    raise RenderError(f"still {last!r} after {timeout:.0f}s")


def free_tier_warning() -> None:
    print()
    print("   free plan, so no disk. Both of these are true of what comes up:")
    print("     /data is ephemeral — a restart issues a new gate signing key and")
    print("     forgets every mandate, so envelopes already handed out stop verifying.")
    print("     The instance sleeps after ~15 min idle, and asleep it is not running")
    print("     the reservation sweeper or the reconciliation poller.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["create", "status", "deploy", "env"])
    ap.add_argument("pairs", nargs="*", metavar="KEY=VALUE")
    ap.add_argument("--plan", default="starter", choices=["starter", "free"])
    args = ap.parse_args()

    try:
        if args.command == "create":
            if find_service():
                print(f"   {SERVICE} already exists — use `deploy` to update it")
                return 1
            svc = create(args.plan)
            print(f"   service:   {svc['id']}")
            print(f"   url:       {svc.get('serviceDetails', {}).get('url', '?')}")
            wait_live(svc["id"])
            if args.plan == "free":
                free_tier_warning()
            url = svc.get("serviceDetails", {}).get("url", "")
            print(f"   live: {url}")
            print(f"   smoke it: ./scripts/deploy.sh check {url}")
            return 0

        svc = find_service()
        if not svc:
            raise RenderError(f"no service named {SERVICE} — run `create` first")

        if args.command == "status":
            deploys = api(f"/services/{svc['id']}/deploys?limit=3")
            print(f"   {svc['name']}  {svc['id']}")
            print(f"   url:  {svc.get('serviceDetails', {}).get('url', '?')}")
            print(f"   plan: {svc.get('serviceDetails', {}).get('plan', '?')}")
            for row in deploys or []:
                d = row["deploy"]
                print(f"   {d['createdAt']}  {d.get('status', '?')}")
            return 0

        if args.command == "env":
            if not args.pairs:
                raise RenderError("env needs at least one KEY=VALUE")
            current = {
                e["envVar"]["key"]: e["envVar"].get("value", "")
                for e in api(f"/services/{svc['id']}/env-vars?limit=100") or []
            }
            for pair in args.pairs:
                k, _, v = pair.partition("=")
                current[k] = v
            api(
                f"/services/{svc['id']}/env-vars",
                "PUT",
                [{"key": k, "value": v} for k, v in current.items()],
            )
            print(f"   set {', '.join(p.split('=')[0] for p in args.pairs)}")

        print("   deploying")
        api(f"/services/{svc['id']}/deploys", "POST", {"imageUrl": IMAGE})
        wait_live(svc["id"])
        print(f"   live: {svc.get('serviceDetails', {}).get('url', '?')}")
        return 0

    except RenderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
