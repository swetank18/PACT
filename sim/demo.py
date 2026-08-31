#!/usr/bin/env python3
"""
The six demo beats, on port 8300.

    python sim/demo.py --beat 5        # run one from the terminal
    python sim/demo.py --serve         # the console's keys 1-6 hit this

Never type a command on stage. The console binds 1 through 6 to `/demo/beat/N`,
`s` to force a stockout and `0` to reset, so the whole pitch is key presses.

Beat 3 exists so the audience sees what the obvious build does wrong. Beat 5 is
the brief's graceful failure and has the most marks in it, so it gets room.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from buyer.agent import BuyerAgent  # noqa: E402
from buyer.session import run_session  # noqa: E402
from contracts.reason_codes import CHECK_ORDER  # noqa: E402
from sim import attacks, hostile  # noqa: E402
from sim.run import GATE, MERCHANT, load_personas, reset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("pact.demo")

#: Curated baskets. A stage demo cannot depend on a random draw: beat 5 stopped
#: reaching the stockout at all when the sampler happened to pick an item over
#: the per-transaction cap. Each basket is chosen to sit inside its persona's
#: mandate so the beat shows what it is meant to show.
BEAT_BASKETS: dict[int, list[str]] = {
    1: ["STA-NB-A5", "STA-PEN-12"],       # clean pass, no repair
    2: ["STA-NB-A5"],                      # room for an addon to land visibly
    3: ["CBL-USBC-2M"],                    # cables-only mandate, furniture offered
    5: ["FUR-LMP-01"],                     # one lamp, and the stock vanishes
}

BEATS: dict[int, str] = {
    1: "happy path, an agent buys end to end",
    2: "headroom upsell, offered and accepted, AOV rises",
    3: "naive upsell for contrast, the gate rejects the offer",
    4: "four attacks, rapid fire",
    5: "forced stockout, refund, budget released, alternative accepted",
    6: "a duplicate webhook arrives and does nothing",
}


def _agent(**kw: Any) -> BuyerAgent:
    return BuyerAgent(gate_url=GATE, merchant_url=MERCHANT, seed=7, **kw)


def _persona(pid: str) -> dict[str, Any]:
    for p in load_personas():
        if p["id"] == pid:
            return p
    return load_personas()[0]


def _ablate(client: httpx.Client, names: list[str]) -> None:
    client.post(f"{GATE}/v1/admin/ablate", json={"disabled": names})


# ------------------------------------------------------------------ beats ---


def beat_1() -> dict:
    """An AI buyer discovers the merchant cold and pays. Eight checks, milliseconds."""
    agent = _agent(upsell_mode="off")
    try:
        manifest = agent.discover()
        r = run_session(
            agent,
            _persona("p_office_manager"),
            arm="demo",
            manifest=manifest,
            force_skus=BEAT_BASKETS[1],
        )
        return {"beat": 1, "completed": r.completed, "gmv_paise": r.gmv_paise,
                "transcript": r.transcript}
    finally:
        agent.close()


def beat_2() -> dict:
    """The upsell lands inside the headroom bar and the order value rises."""
    agent = _agent(upsell_mode="headroom")
    try:
        manifest = agent.discover()
        persona = dict(_persona("p_office_manager"))
        persona["addon_receptivity"] = 1.0  # the human says yes, on stage
        r = run_session(
            agent, persona, arm="demo", manifest=manifest, force_skus=BEAT_BASKETS[2]
        )
        return {"beat": 2, "completed": r.completed, "gmv_paise": r.gmv_paise,
                "upsell_offered": r.upsell_offered, "upsell_accepted": r.upsell_accepted,
                "transcript": r.transcript}
    finally:
        agent.close()


def beat_3() -> dict:
    """
    The contrast. A blind upsell against a cables-only mandate offers furniture,
    the gate refuses it, and the session dies.

    Runs the naive upsell by flipping the merchant's mode for one beat, then
    flips it back — the audience sees the same merchant behaving the way one
    without a readable authority envelope has to.
    """
    with httpx.Client(timeout=10) as client:
        client.post(f"{MERCHANT}/admin/set_upsell_mode", json={"mode": "naive"})
    agent = _agent(upsell_mode="naive")
    try:
        manifest = agent.discover()
        persona = dict(_persona("p_narrow_category"))
        persona["addon_receptivity"] = 1.0
        r = run_session(
            agent,
            persona,
            arm="demo",
            manifest=manifest,
            force_skus=BEAT_BASKETS[3],
            # The contrast is not just that the offer is refused — it is that a
            # refusal the agent cannot act on ends the session. Our agent repairs;
            # this beat shows the one that does not.
            allow_repair=False,
        )
        rejected = [
            d for d in r.decisions
            if d.get("verdict") == "BLOCK"
        ]
        return {
            "beat": 3,
            "completed": r.completed,
            "offers_made": r.upsell_offered,
            "rejected_by_gate": r.upsell_rejected_by_gate,
            "reason_codes": [d["reason_code"] for d in rejected],
            "transcript": r.transcript,
        }
    finally:
        agent.close()
        with httpx.Client(timeout=10) as client:
            client.post(f"{MERCHANT}/admin/set_upsell_mode", json={"mode": "headroom"})


def beat_4() -> dict:
    """Four attacks in a row, four different reason codes."""
    agent = _agent()
    try:
        manifest = agent.discover()
        vpa = manifest["merchant_vpa"]
        persona = _persona("p_office_manager")
        out = []
        out += attacks.atk_05_price_hallucination(agent, agent.issue_mandate(persona, vpa), vpa)[:1]
        out += attacks.atk_04_lookalike_vpa(agent, agent.issue_mandate(persona, vpa), vpa)[:1]
        out += attacks.atk_02_replay(agent, agent.issue_mandate(persona, vpa), vpa)
        out += attacks.atk_01_injection(agent, agent.issue_mandate(persona, vpa), vpa)[:1]
        return {
            "beat": 4,
            "attacks": [
                {"id": r.id, "name": r.name, "verdict": r.verdict,
                 "reason_code": r.reason_code, "outcome": r.outcome}
                for r in out
            ],
        }
    finally:
        agent.close()


def beat_5() -> dict:
    """
    The graceful failure. Capture succeeds, fulfilment does not, and the sale
    comes back.

    The beat with the most marks in it. The console draws the timeline one row
    at a time, so this deliberately does not rush.
    """
    agent = _agent(upsell_mode="headroom")
    try:
        manifest = agent.discover()
        with httpx.Client(timeout=10) as client:
            client.post(f"{MERCHANT}/admin/force_stockout", json={"sku": "FUR-LMP-01"})
        persona = dict(_persona("p_desk_refresh"))
        persona["addon_receptivity"] = 0.0
        r = run_session(
            agent, persona, arm="demo", manifest=manifest, force_skus=BEAT_BASKETS[5]
        )
        return {
            "beat": 5,
            "recovered": r.saga_recoveries > 0,
            "refunded_paise": r.refunded_paise,
            "gmv_paise": r.gmv_paise,
            "orders": r.order_ids,
            "transcript": r.transcript,
        }
    finally:
        agent.close()


def beat_6() -> dict:
    """A duplicate webhook arrives. Nothing happens, and that is the point."""
    with httpx.Client(timeout=15) as client:
        orders = client.get(f"{MERCHANT}/v1/orders", params={"limit": 20}).json()["orders"]
        settled = next((o for o in orders if o.get("rail_payment_id")), None)
        if settled is None:
            return {"beat": 6, "error": "no settled order yet; run beat 1 first"}

        before = client.get(f"{MERCHANT}/v1/orders/{settled['order_id']}/saga").json()["steps"]
        outcomes = []
        for _ in range(3):
            r = client.post(
                f"{MERCHANT}/admin/simulate_webhook",
                json={"order_id": settled["order_id"], "event": "payment.captured"},
            )
            outcomes.append(r.json() if r.status_code == 200 else {"error": r.status_code})
        after = client.get(f"{MERCHANT}/v1/orders/{settled['order_id']}/saga").json()["steps"]

        return {
            "beat": 6,
            "order_id": settled["order_id"],
            "deliveries": outcomes,
            "duplicates_recognised": sum(1 for o in outcomes if o.get("duplicate")),
            "state_unchanged": len(before) <= len(after),
        }


RUNNERS = {1: beat_1, 2: beat_2, 3: beat_3, 4: beat_4, 5: beat_5, 6: beat_6}


# ----------------------------------------------------------------- server ---


def build_app():  # noqa: ANN201
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="PACT demo beats", version="1.0.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.post("/demo/beat/{n}")
    async def run_beat(n: int) -> dict:
        runner = RUNNERS.get(n)
        if runner is None:
            raise HTTPException(404, f"no beat {n}")
        # Off the event loop: a beat makes many blocking HTTP calls and would
        # otherwise stall the SSE streams the console is watching.
        return await asyncio.to_thread(runner)

    def _reset_all() -> dict:
        with httpx.Client(timeout=10) as client:
            reset(client)
            _ablate(client, [])
            client.post(f"{MERCHANT}/admin/set_upsell_mode", json={"mode": "headroom"})
            client.post(f"{MERCHANT}/admin/inject_failure",
                        json={"capture_fails": False, "refund_fails": False,
                              "refund_pending": False})
        return {"ok": True}

    @app.post("/admin/reset")
    async def reset_all() -> dict:
        # Off the event loop, for the same reason the beats are: these are
        # blocking calls to the gate and the merchant, and in the single-port
        # build those are *this same process*. Awaiting them on the loop that
        # has to serve them deadlocks until the client times out — the console's
        # `0` key would hang for ten seconds and then 500. Separate processes in
        # development hide this entirely, which is what makes it worth a note.
        return await asyncio.to_thread(_reset_all)

    @app.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "beats": BEATS}

    return app


app = build_app()


def main() -> int:
    ap = argparse.ArgumentParser(description="PACT demo beats")
    ap.add_argument("--beat", type=int, choices=list(BEATS))
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8300)
    args = ap.parse_args()

    if args.serve:
        import uvicorn

        uvicorn.run(app, port=args.port, log_level="warning")
        return 0

    if args.beat:
        import json

        print(f"[beat {args.beat}] {BEATS[args.beat]}")
        print(json.dumps(RUNNERS[args.beat](), indent=2))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
