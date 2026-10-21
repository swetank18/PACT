"""
PayPal's webhook receiver.

The same three properties as `rails/razorpay/webhooks.py`, met PayPal's way:

**Verified before anything is believed.** PayPal signs with five headers, and
the adapter asks PayPal itself whether the delivery is genuine, with the raw
body spliced in — never parsed and re-serialised first. A delivery that fails,
or cannot be checked, is refused.

**Duplicated and unordered, and harmless either way.** PayPal gives every
event an `id` that is stable across redeliveries, so that is the key. A
redelivery is a no-op, and an event for an order that has already moved on is
acknowledged and ignored.

**Never the only path.** The reconciler settles a pending capture by polling
the rail. This makes it fast; it does not make it possible.

What it acts on: a capture the saga left `PAYMENT_PENDING` either completes or
is denied. Everything else is acknowledged, because a non-2xx would make PayPal
retry an event for 3 days that we have correctly decided not to act on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request

from contracts.schemas import utcnow
from core.db import Database
from rails.paypal.client import WEBHOOK_HEADERS

log = logging.getLogger("pact.webhooks.paypal")

#: Event type -> what it means for a pending capture, in the rail-neutral
#: vocabulary the saga's `settle_pending` takes.
SETTLES: Mapping[str, str] = {
    "PAYMENT.CAPTURE.COMPLETED": "captured",
    "PAYMENT.CAPTURE.DENIED": "declined",
}


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    applied: bool
    duplicate: bool
    event: str
    capture_id: str | None
    detail: str = ""


class PayPalWebhookProcessor:
    """
    Separate from the HTTP layer so tests can deliver events directly —
    twice, out of order, forged — without a server.
    """

    def __init__(
        self,
        db: Database,
        adapter,  # noqa: ANN001 - a PayPalAdapter; typed loosely like the Razorpay one
        *,
        settle: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.db = db
        self.adapter = adapter
        #: `SagaRunner.settle_pending(order_id, rail_status)`. Injected rather
        #: than imported, because rails/ depends on core and never on merchant.
        self.settle = settle

    def process(self, body: bytes, headers: Mapping[str, str]) -> WebhookOutcome:
        lowered = {k.lower(): v for k, v in headers.items() if k.lower() in WEBHOOK_HEADERS}
        if not self.adapter.verify_callback(body, json.dumps(lowered)):
            log.warning("rejected a PayPal webhook that did not verify (%d bytes)", len(body))
            raise PermissionError("webhook did not verify")

        event = json.loads(body)
        event_id = str(event.get("id") or "")
        event_type = str(event.get("event_type") or "")
        resource = event.get("resource") or {}
        capture_id = resource.get("id")
        if not event_id:
            return WebhookOutcome(False, False, event_type, capture_id, "event has no id")

        meaning = SETTLES.get(event_type)
        if meaning is None:
            return WebhookOutcome(False, False, event_type, capture_id, "not an event we act on")

        key = f"paypal:{event_id}"
        with self.db.immediate_tx() as conn:
            if conn.execute("SELECT 1 FROM rail_events WHERE event_key = ?", (key,)).fetchone():
                return WebhookOutcome(False, True, event_type, capture_id, "already applied, no change")
            conn.execute(
                "INSERT INTO rail_events (event_key, event_type, payment_id, body_json, applied_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, event_type, capture_id, body.decode("utf-8", "replace"), utcnow()),
            )

        try:
            applied, detail = self._apply(capture_id, meaning)
        except Exception:
            # Claimed but not applied. Release the claim so PayPal's redelivery
            # gets another go, rather than being mistaken for a duplicate.
            with self.db.immediate_tx() as conn:
                conn.execute("DELETE FROM rail_events WHERE event_key = ?", (key,))
            raise
        return WebhookOutcome(applied, False, event_type, capture_id, detail)

    def _apply(self, capture_id: str | None, meaning: str) -> tuple[bool, str]:
        if not capture_id:
            return False, "event names no capture"
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT order_id, state FROM orders WHERE rail_payment_id = ?", (capture_id,)
            ).fetchone()
        if row is None:
            # The webhook beat the saga's own write, or names a capture we did
            # not make. The reconciler covers the first; the second is noise.
            return False, "no order holds this capture"
        if row["state"] != "PAYMENT_PENDING":
            return False, f"order is {row['state']}, nothing to settle"
        if self.settle is None:
            return False, "no saga to settle with"
        return bool(self.settle(row["order_id"], meaning)), f"pending capture {meaning}"


def build_app(processor: PayPalWebhookProcessor, *, path: str = "/webhooks/rail") -> FastAPI:
    app = FastAPI(title="PACT PayPal webhook receiver", version="1.0.0")

    @app.post(path)
    async def receive(request: Request):
        # The raw body, read once. Verification is a call to PayPal, so it runs
        # off the event loop.
        body = await request.body()
        try:
            outcome = await asyncio.to_thread(processor.process, body, dict(request.headers))
        except PermissionError as exc:
            raise HTTPException(400, "webhook did not verify") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "unparseable body") from exc
        # 200 for a duplicate and for an event we ignore: anything else makes
        # PayPal retry it for three days.
        return {"ok": True, "applied": outcome.applied, "duplicate": outcome.duplicate,
                "detail": outcome.detail}

    @app.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "at": utcnow()}

    return app
