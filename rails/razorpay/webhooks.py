"""
The webhook receiver. Port 8110.

Three properties, all of them things a payments engineer will look for first:

**Signature verification is mandatory.** HMAC-SHA256 over the **raw** body. An
unverified webhook endpoint in a payments project is the first thing anyone
checks, and re-serialising the body before verifying is the standard way this
check silently stops working — so the raw bytes are read once and never parsed
before the check.

**Webhooks are unordered and duplicated.** Every handler is idempotent, keyed on
the rail's payment id and the event type. `payment.captured` arriving twice, or
arriving after the reconciliation poller already resolved the order, changes
nothing. That no-op is a demo beat.

**A webhook is never the only path.** The reconciliation poller in the merchant
service resolves anything pending and older than 30 seconds, so a webhook that
never arrives is a delay, not a lost order.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from fastapi import FastAPI, Header, HTTPException, Request

from contracts.schemas import utcnow
from core.db import Database

log = logging.getLogger("pact.webhooks")

#: Events we act on. Everything else is acknowledged and ignored — returning a
#: non-2xx for an event we simply do not care about would make the rail retry
#: forever.
HANDLED = frozenset({"payment.authorized", "payment.captured", "payment.failed"})


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    applied: bool
    duplicate: bool
    event: str
    payment_id: str | None
    detail: str = ""


class WebhookProcessor:
    """
    Separated from the HTTP layer so the chaos suite can deliver events
    directly — duplicated, out of order, or three at once — without a server.
    """

    def __init__(self, db: Database, adapter, on_applied=None) -> None:  # noqa: ANN001
        self.db = db
        self.adapter = adapter
        self.on_applied = on_applied

    def verify(self, body: bytes, signature: str) -> bool:
        return self.adapter.verify_callback(body, signature)

    def process(self, body: bytes, signature: str) -> WebhookOutcome:
        if not self.verify(body, signature):
            # Logged, not silently dropped. A rejected webhook is a signal.
            log.warning("rejected a webhook with a bad signature (%d bytes)", len(body))
            raise PermissionError("webhook signature mismatch")

        payload = json.loads(body)
        event = str(payload.get("event", ""))
        entity = (payload.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
        payment_id = entity.get("id")

        if event not in HANDLED:
            return WebhookOutcome(False, False, event, payment_id, "not a handled event")

        # The idempotency key. Razorpay does not guarantee a delivery id we can
        # trust, so the key is what the event *means*: this payment, this
        # transition. A redelivery of the same transition is the same key.
        event_key = f"{event}:{payment_id}"

        with self.db.immediate_tx() as conn:
            existing = conn.execute(
                "SELECT 1 FROM rail_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing:
                return WebhookOutcome(False, True, event, payment_id, "already applied, no change")

            conn.execute(
                "INSERT INTO rail_events (event_key, event_type, payment_id, body_json, applied_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_key, event, payment_id, body.decode("utf-8", "replace"), utcnow()),
            )

        applied = self._apply(event, payment_id, entity)
        return WebhookOutcome(applied, False, event, payment_id)

    def _apply(self, event: str, payment_id: str | None, entity: dict) -> bool:
        """
        Move the order forward, but only forwards.

        Out of order delivery is normal — `payment.authorized` can arrive after
        `payment.captured`. Applying it would walk the order backwards, so a
        transition is only applied when the order is behind it. That is what
        "converges to the correct state" means in practice.
        """
        if not payment_id:
            return False

        with self.db.immediate_tx() as conn:
            row = conn.execute(
                "SELECT order_id, state FROM orders WHERE rail_payment_id = ?", (payment_id,)
            ).fetchone()
            if row is None:
                # The webhook beat our own write. The reconciliation poller will
                # pick this up; dropping it here is safe because we never rely
                # on the webhook as the only path.
                log.info("webhook for %s arrived before the order existed", payment_id)
                return False

            order_id, state = row["order_id"], row["state"]

        terminal = {"FULFILLED", "RECOVERED", "ROLLED_BACK", "NEEDS_ATTENTION"}
        if state in terminal or state == "PAYMENT_CAPTURED":
            log.info("webhook %s for %s is a no op; order is %s", event, payment_id, state)
            return False

        if event == "payment.captured" and self.on_applied:
            self.on_applied(order_id, "PAYMENT_CAPTURED", entity)
            return True
        return False


def build_app(processor: WebhookProcessor, *, path: str = "/webhooks/rail") -> FastAPI:
    """
    `path` is configurable so the single-container build can mount this app
    under /api/webhooks and still expose a sane URL, rather than the
    /api/webhooks/webhooks/rail a fixed path would produce.
    """
    app = FastAPI(title="PACT webhook receiver", version="1.0.0")

    @app.post(path)
    async def receive(request: Request, x_razorpay_signature: str = Header(default="")):
        # The RAW body. Never `await request.json()` before verifying — the
        # signature is over the bytes the rail sent, and re-serialising changes
        # them.
        body = await request.body()
        try:
            outcome = processor.process(body, x_razorpay_signature)
        except PermissionError as exc:
            raise HTTPException(400, "signature mismatch") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "unparseable body") from exc

        # 200 even for a duplicate. A non-2xx would make the rail retry an event
        # we have correctly decided to ignore.
        return {
            "ok": True,
            "applied": outcome.applied,
            "duplicate": outcome.duplicate,
            "detail": outcome.detail,
        }

    @app.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "at": utcnow()}

    return app
