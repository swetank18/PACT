"""
The audit trail, and the event bus that streams it.

Judges asked to see the audit trail, so this is a first class store, not a log
file. Every decision and every saga transition is a row, and the same write that
persists it publishes it to anyone streaming.

The bus is deliberately lossy and unbuffered. Subscribers get what happens while
they are attached and nothing else, because the console refetches on every
reconnect rather than trusting the stream to have held anything for it. A
replay buffer here would be a second source of truth and a memory leak.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

from contracts.schemas import Decision, SagaStep, utcnow
from core.db import Database

log = logging.getLogger("pact.audit")

#: Dropped frames are better than unbounded memory when a subscriber stalls.
SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    data: dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Remember the serving loop so `publish` can be called from a worker
        thread. FastAPI runs sync endpoints in a threadpool, and the saga runs
        in a background thread, so most publishes do not originate on the loop.
        """
        self._loop = loop

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        event = Event(type=event_type, data=data)
        loop = self._loop
        if loop is None or not loop.is_running():
            self._fanout(event)
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            # The loop went away mid-publish. Losing a frame is acceptable;
            # taking down the saga that produced it is not.
            log.debug("event loop unavailable, dropping %s", event_type)

    def _fanout(self, event: Event) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("subscriber queue full, dropping %s", event.type)

    async def subscribe(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class AuditStore:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or EventBus()

    # -------------------------------------------------------- decisions ----

    def record_decision(self, decision: Decision) -> None:
        body = decision.model_dump()
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                    (decision_id, mandate_id, verdict, reason_code, amount_paise,
                     payee_vpa, quote_id, elapsed_ms, body_json, at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.mandate_id,
                    str(decision.verdict),
                    str(decision.reason_code),
                    decision.amount_paise,
                    decision.payee_vpa,
                    decision.quote_id,
                    decision.elapsed_ms,
                    json.dumps(body, separators=(",", ":")),
                    decision.at,
                ),
            )
        # The settlement token is a bearer credential. It goes to the caller in
        # the HTTP response and never onto the broadcast stream, which anyone
        # watching the console is attached to.
        self.bus.publish("decision", {**body, "settlement_token": None})

    def list_decisions(self, limit: int = 50) -> list[dict]:
        with self.db.read_tx() as conn:
            rows = conn.execute(
                "SELECT body_json FROM decisions ORDER BY at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**json.loads(r["body_json"]), "settlement_token": None} for r in rows]

    def get_decision(self, decision_id: str) -> dict | None:
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT body_json FROM decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["body_json"]), "settlement_token": None}

    def update_decision(self, decision: Decision) -> None:
        """Used when a STEP_UP is resolved, so the trail shows the final state."""
        self.record_decision(decision)

    # ------------------------------------------------------------- saga ----

    def append_step(
        self,
        *,
        order_id: str,
        state: str,
        action: str,
        outcome: str,
        detail: str = "",
        ref: str | None = None,
    ) -> SagaStep:
        """
        Append the next step. The sequence number is allocated inside the write
        transaction, so two threads appending to the same order cannot collide
        on the (order_id, seq) primary key.
        """
        with self.db.immediate_tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM saga_steps WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            seq = int(row["n"]) + 1
            at = utcnow()
            conn.execute(
                """
                INSERT INTO saga_steps
                    (order_id, seq, state, action, outcome, detail_json, ref, at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, seq, state, action, outcome, json.dumps(detail), ref, at),
            )

        step = SagaStep(
            order_id=order_id,
            seq=seq,
            state=state,  # type: ignore[arg-type]
            action=action,
            outcome=outcome,  # type: ignore[arg-type]
            detail=detail,
            ref=ref,
            at=at,
        )
        self.bus.publish("saga_step", step.model_dump())
        return step

    def list_steps(self, order_id: str) -> list[dict]:
        with self.db.read_tx() as conn:
            rows = conn.execute(
                "SELECT order_id, seq, state, action, outcome, detail_json, ref, at "
                "FROM saga_steps WHERE order_id = ? ORDER BY seq",
                (order_id,),
            ).fetchall()
        return [
            {
                "order_id": r["order_id"],
                "seq": r["seq"],
                "state": r["state"],
                "action": r["action"],
                "outcome": r["outcome"],
                "detail": json.loads(r["detail_json"]),
                "ref": r["ref"],
                "at": r["at"],
            }
            for r in rows
        ]
