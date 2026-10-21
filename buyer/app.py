"""
The agent service. Mounted at /api/agent in the single-port build.

    GET  /v1/agent                    who the agent is: its delegate key, model, limits
    POST /v1/runs                     {mandate_id, goal, mode} -> the run
    GET  /v1/runs/{run_id}            the run and every step so far
    POST /v1/runs/{run_id}/step_up    the principal's signed answer, relayed to the gate
    GET  /v1/stream                   SSE: "agent" per step, "agent_run" when one ends

A mandate the agent can act under names `pubkey` from /v1/agent as its
delegate. Any other mandate fails at the gate on the agent's first payment,
which is the gate doing its job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from buyer.llm_agent import HttpShop
from buyer.runs import RunManager, RunRefused, load_or_create_agent_key
from core.audit.store import EventBus

log = logging.getLogger("pact.buyer.app")

GATE_URL = os.environ.get("PACT_GATE_URL", "http://localhost:8000")
MERCHANT_URL = os.environ.get("PACT_MERCHANT_URL", "http://localhost:8100")


def _llm_factory():  # noqa: ANN202
    """The SDK client, when there is something to authenticate with."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.warning("No Anthropic credentials. The Claude agent will refuse runs; the "
                    "scripted agent and the demo beats are unaffected.")
        return None
    import anthropic

    return lambda: anthropic.Anthropic()


bus = EventBus()
manager = RunManager(
    llm_factory=_llm_factory(),
    shop_factory=lambda: HttpShop(GATE_URL, MERCHANT_URL),
    agent_key=load_or_create_agent_key(),
    publish=bus.publish,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="PACT agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _refused(exc: RunRefused) -> HTTPException:
    return HTTPException(exc.status, exc.detail)


@app.get("/v1/agent")
async def describe() -> dict:
    return manager.describe()


@app.post("/v1/runs")
async def start_run(body: dict) -> dict:
    try:
        run = manager.start(str(body.get("mandate_id", "")), str(body.get("goal", "")),
                            str(body.get("mode", "normal")))
    except RunRefused as exc:
        raise _refused(exc) from exc
    return run.public()


@app.get("/v1/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(404, "no such run")
    return run.public()


@app.post("/v1/runs/{run_id}/step_up")
async def answer_step_up(run_id: str, body: dict) -> dict:
    try:
        return await asyncio.to_thread(manager.step_up, run_id, body)
    except RunRefused as exc:
        raise _refused(exc) from exc


def _heartbeat() -> ServerSentEvent:
    return ServerSentEvent(event="heartbeat", data="{}")


@app.get("/v1/stream")
async def stream(request: Request) -> EventSourceResponse:
    async def gen():
        async for event in bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": json.dumps(event.data)}

    return EventSourceResponse(gen(), ping=5, ping_message_factory=_heartbeat)


@app.get("/v1/health")
async def health() -> dict:
    return {"ok": True, "model_configured": manager.llm_factory is not None}
