"""Entrypoint for the webhook receiver on 8110. Kept separate from the
processor so the chaos suite can deliver events without a server."""

from __future__ import annotations

import logging

from merchant.app import service
from rails.razorpay.webhooks import WebhookProcessor, build_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def _on_applied(order_id: str, state: str, entity: dict) -> None:
    """A webhook moved an order forward. Record it like any other transition."""
    service.audit.append_step(
        order_id=order_id,
        state=state,
        action="webhook.payment.captured",
        outcome="OK",
        detail="confirmed by webhook",
        ref=entity.get("id"),
    )
    service.publish_stats()


app = build_app(WebhookProcessor(service.db, service.rail, on_applied=_on_applied))
