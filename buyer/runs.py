"""
Agent runs: start one, watch it, answer its step-ups.

A run is one goal pursued by the Claude buyer under one mandate. It runs in a
thread, and everything it does is a step — appended to the run and broadcast,
so the run viewer can draw it as it happens and the evaluation can price it
afterwards from the token usage each model turn carries.

Two limits, because a hosted demo is open to anyone from 12 November to 15
December and every run costs money: runs per hour across everyone, and runs at
once. A refused run says which limit it hit and when to try again.

The step-up is relayed. When the gate wants the principal's approval, the run
waits; the console posts the principal's signed answer to the run, the run
forwards it to the gate, and the settlement token the gate returns goes to the
agent that needs it. The agent never sees the principal's key, and the console
never handles a token.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer.llm_agent import EFFORT, MODEL, STEP_UP_WAIT_S, ClaudeBuyer, Shop
from contracts.crypto import b64u_encode, private_key_from_seed
from contracts.ids import new_id

log = logging.getLogger("pact.buyer.runs")

DEFAULT_KEY_PATH = "fixtures/keys/agent_signing_key.hex"
RUNS_PER_HOUR = int(os.environ.get("PACT_AGENT_RUNS_PER_HOUR") or "30")
RUNS_AT_ONCE = int(os.environ.get("PACT_AGENT_RUNS_AT_ONCE") or "2")
#: How many finished runs are kept for the viewer. In memory; a run is a demo
#: artefact, and the money it moved is in the merchant's and the gate's own
#: durable records.
KEEP_RUNS = 200


def load_or_create_agent_key(path: str | None = None) -> Ed25519PrivateKey:
    """The agent's delegate key. Mandates for this agent name its public half."""
    key_path = Path(path or os.environ.get("PACT_AGENT_KEY_PATH") or DEFAULT_KEY_PATH)
    if key_path.exists():
        return private_key_from_seed(bytes.fromhex(key_path.read_text().strip()))
    key = Ed25519PrivateKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key.private_bytes_raw().hex() + "\n")
    key_path.chmod(0o600)
    return key


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RunRefused(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Run:
    run_id: str
    mandate_id: str
    goal: str
    mode: str
    status: str = "running"
    steps: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    outcome: str | None = None
    summary: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    #: The decision waiting on the principal, and the answer once it comes.
    pending: dict | None = None
    _answered: threading.Event = field(default_factory=threading.Event, repr=False)
    _answer: dict | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "mandate_id": self.mandate_id, "goal": self.goal,
                "mode": self.mode, "status": self.status, "steps": list(self.steps),
                "started_at": self.started_at, "ended_at": self.ended_at,
                "outcome": self.outcome, "summary": self.summary, "usage": dict(self.usage),
                "pending_decision_id": (self.pending or {}).get("decision_id")}


class RunManager:
    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any] | None,
        shop_factory: Callable[[], Shop],
        agent_key: Ed25519PrivateKey,
        publish: Callable[[str, dict], None] = lambda _t, _d: None,
        runs_per_hour: int = RUNS_PER_HOUR,
        runs_at_once: int = RUNS_AT_ONCE,
        step_up_wait_s: float = STEP_UP_WAIT_S,
        buyer_kwargs: dict[str, Any] | None = None,
    ) -> None:
        #: None when no model is configured. Runs are then refused, clearly,
        #: rather than started and failed.
        self.llm_factory = llm_factory
        self.shop_factory = shop_factory
        self.agent_key = agent_key
        self.publish = publish
        self.runs_per_hour = runs_per_hour
        self.runs_at_once = runs_at_once
        self.step_up_wait_s = step_up_wait_s
        self.buyer_kwargs = buyer_kwargs or {}
        self._runs: dict[str, Run] = {}
        self._started: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def pubkey(self) -> str:
        return b64u_encode(self.agent_key.public_key().public_bytes_raw())

    # ------------------------------------------------------------ starting --

    def start(self, mandate_id: str, goal: str, mode: str = "normal") -> Run:
        if self.llm_factory is None:
            raise RunRefused(503, "no model is configured for the agent here "
                                  "(set ANTHROPIC_API_KEY); the scripted demo beats still run")
        if mode not in ("normal", "attack"):
            raise RunRefused(422, "mode is normal or attack")
        if not mandate_id or not goal.strip():
            raise RunRefused(422, "a run needs a mandate and a goal")
        with self._lock:
            now = time.monotonic()
            while self._started and now - self._started[0] > 3600:
                self._started.popleft()
            if len(self._started) >= self.runs_per_hour:
                wait = int(3600 - (now - self._started[0])) + 1
                raise RunRefused(429, f"the hourly run limit ({self.runs_per_hour}) is reached; "
                                      f"try again in {wait // 60 + 1} minutes")
            running = sum(1 for r in self._runs.values()
                          if r.status in ("running", "waiting_principal"))
            if running >= self.runs_at_once:
                raise RunRefused(429, f"{running} runs are already going; try again shortly")
            self._started.append(now)
            run = Run(run_id=new_id("run"), mandate_id=mandate_id, goal=goal.strip(), mode=mode)
            self._runs[run.run_id] = run
            self._forget_oldest()

        threading.Thread(target=self._execute, args=(run,), name=run.run_id, daemon=True).start()
        return run

    def _forget_oldest(self) -> None:
        finished = [r for r in self._runs.values() if r.status in ("done", "failed")]
        for r in finished[: max(0, len(self._runs) - KEEP_RUNS)]:
            self._runs.pop(r.run_id, None)

    def _execute(self, run: Run) -> None:
        try:
            buyer = ClaudeBuyer(
                self.llm_factory(),  # type: ignore[misc]
                self.shop_factory(),
                mandate_id=run.mandate_id, goal=run.goal, agent_key=self.agent_key,
                emit=lambda step: self._emit(run, step),
                wait_for_principal=lambda decision: self._wait(run, decision),
                mode=run.mode, **self.buyer_kwargs,
            )
            result = buyer.run()
            run.outcome = result["outcome"]
            run.summary = result.get("summary")
            run.usage = result.get("usage", {})
            run.status = "done" if result["outcome"] == "done" else "failed"
        except Exception as exc:  # noqa: BLE001 - a run that dies says so, it does not vanish
            log.exception("run %s failed", run.run_id)
            run.status = "failed"
            run.outcome = "error"
            self._emit(run, {"kind": "error", "summary": f"The run stopped: {type(exc).__name__}: {exc}"})
        finally:
            run.ended_at = _now()
            self.publish("agent_run", run.public() | {"steps": []})

    # --------------------------------------------------------------- steps --

    def _emit(self, run: Run, step: dict) -> None:
        step = {"run_id": run.run_id, "seq": len(run.steps) + 1, "at": _now(), **step}
        run.steps.append(step)
        self.publish("agent", step)

    # ------------------------------------------------------------- step-up --

    def _wait(self, run: Run, decision: dict) -> dict | None:
        run.pending = decision
        run.status = "waiting_principal"
        run._answered.clear()
        run._answer = None
        answered = run._answered.wait(self.step_up_wait_s)
        run.status = "running"
        run.pending = None
        return run._answer if answered else None

    def step_up(self, run_id: str, payload: dict) -> dict:
        """
        The principal's answer, relayed to the gate. Only the decision the run
        is actually waiting on can be answered, and the answer goes to the gate
        exactly as signed — this never signs anything itself.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise RunRefused(404, "no such run")
        if run.pending is None:
            raise RunRefused(409, "this run is not waiting for an approval")
        decision_id = run.pending["decision_id"]
        result = self.shop_factory().resolve_step_up(decision_id, payload)
        run._answer = result
        run._answered.set()
        return {k: v for k, v in result.items() if k != "settlement_token"}

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def describe(self) -> dict[str, Any]:
        return {"agent_id": "claude_buyer_v1", "pubkey": self.pubkey, "model": MODEL,
                "effort": EFFORT, "enabled": self.llm_factory is not None,
                "runs_per_hour": self.runs_per_hour, "runs_at_once": self.runs_at_once}
