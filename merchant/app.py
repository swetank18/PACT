"""
The merchant service. Port 8100.

Catalog is exposed twice: as MCP tools for agent buyers (`merchant/mcp_server.py`)
and as plain REST here for the console. Both call the same engines, so an agent
and a human cannot be quoted different prices.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from contracts.schemas import (
    AgentCommerceManifest,
    CreateOrderRequest,
    MerchantStats,
    QuoteItemRequest,
    QuoteRequest,
    SuggestAddonsRequest,
    utcnow,
)
from core.audit.store import AuditStore, EventBus
from core.db import Database
from merchant.catalog import (
    BY_SKU,
    CATEGORIES,
    CURRENCY,
    Inventory,
    MERCHANT_NAME,
    MERCHANT_VPA,
    search,
)
from merchant.gate_client import GATE_URL, HttpGateClient
from merchant.quote import QUOTE_TTL_SECONDS, QuoteEngine
from merchant.saga import OrderStore, SagaRunner
from merchant.stats import StatsService
from merchant.upsell import UpsellEngine
from rails.mock_upi.adapter import MockUpiAdapter
from rails.razorpay.adapter import RazorpayAdapter
from rails.razorpay.client import RazorpayClient, credentials_from_env

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("pact.merchant.app")

RECONCILE_INTERVAL_SECONDS = 15
#: Paces the saga for the stage. Zero for the simulation, which runs hundreds of
#: sessions and must not sleep through any of them.
STEP_DELAY_S = float(os.environ.get("PACT_SAGA_STEP_DELAY_S", "0.35"))


def _build_rail(db: Database):  # noqa: ANN201
    """
    Pick the rail from the profile. `rails/` depends on `core`, never the
    reverse, so this is the only place that names a concrete rail.
    """
    name = os.environ.get("PACT_RAIL", "").strip().lower()
    creds = credentials_from_env()

    if name == "mock_upi" or (not name and not creds.configured):
        if not creds.configured:
            log.warning(
                "No Razorpay credentials, using the mock_upi rail. Everything "
                "above the adapter is identical; only settlement is simulated."
            )
        return MockUpiAdapter()
    return RazorpayAdapter(RazorpayClient(db))


class MerchantService:
    def __init__(self, db_url: str | None = None) -> None:
        self.db = Database(db_url)
        self.bus = EventBus()
        self.audit = AuditStore(self.db, self.bus)
        self.inventory = Inventory()
        self.quotes = QuoteEngine(self.db)
        self.upsell = UpsellEngine(self.inventory, self.quotes)
        self.orders = OrderStore(self.db)
        self.gate = HttpGateClient()
        self.rail = _build_rail(self.db)
        self.saga = SagaRunner(
            self.db,
            audit=self.audit,
            rail=self.rail,
            inventory=self.inventory,
            quotes=self.quotes,
            gate=self.gate,
            orders=self.orders,
            step_delay_s=STEP_DELAY_S,
        )
        self.stats = StatsService(self.db, self.upsell)

    def publish_stats(self) -> None:
        self.bus.publish("stats", self.stats.compute().model_dump())


service = MerchantService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.bus.bind_loop(asyncio.get_running_loop())

    async def reconciler() -> None:
        while True:
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
            try:
                n = await asyncio.to_thread(service.saga.reconcile)
                if n:
                    log.info("reconciler resolved %d order(s) the webhook never covered", n)
                    service.publish_stats()
            except Exception:  # noqa: BLE001
                log.exception("reconciler failed; continuing")

    task = asyncio.create_task(reconciler())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="PACT merchant", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ manifest --


@app.get("/.well-known/agent-commerce.json")
async def manifest() -> dict:
    """So an unknown agent can discover us cold, with no partnership."""
    return AgentCommerceManifest(
        merchant=MERCHANT_NAME,
        merchant_vpa=MERCHANT_VPA,
        mcp_endpoint=f"http://localhost:8100/mcp",
        categories=list(CATEGORIES),
        currency=CURRENCY,
        accepts_mandates=["pact/v1"],
        headroom_endpoint=f"{GATE_URL}/v1/mandates/{{id}}/headroom",
        quote_ttl_seconds=QUOTE_TTL_SECONDS,
        return_window_days=7,
        rate_limit_per_minute=60,
    ).model_dump()


# ------------------------------------------------------------------- catalog --


@app.get("/v1/catalog")
async def catalog(q: str | None = None, category: str | None = None,
                  max_price_paise: int | None = None) -> dict:
    products = search(q, category, max_price_paise)
    return {
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "price_paise": p.price_paise,
                "in_stock": service.inventory.level(p.sku),
                "description": p.description,
            }
            for p in products
        ]
    }


@app.get("/v1/catalog/{sku}")
async def get_product(sku: str) -> dict:
    p = BY_SKU.get(sku)
    if p is None:
        raise HTTPException(404, "no such sku")
    return {
        "sku": p.sku, "name": p.name, "category": p.category,
        "price_paise": p.price_paise, "in_stock": service.inventory.level(p.sku),
        "description": p.description, "tags": list(p.tags),
    }


# --------------------------------------------------------------------- quote --


@app.post("/v1/quote")
async def quote(request: QuoteRequest) -> dict:
    headroom = (
        await asyncio.to_thread(service.gate.headroom, request.mandate_id)
        if request.mandate_id
        else None
    )
    try:
        q = await asyncio.to_thread(
            service.quotes.build, request.items,
            mandate_id=request.mandate_id, headroom=headroom,
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    service.bus.publish("quote", q.model_dump())
    return q.model_dump()


@app.post("/v1/suggest_addons")
async def suggest_addons(request: SuggestAddonsRequest) -> dict:
    q = await asyncio.to_thread(service.quotes.get, request.quote_id)
    if q is None:
        raise HTTPException(404, "no such quote")

    naive = os.environ.get("PACT_UPSELL", "headroom").lower() == "naive"
    if naive:
        # Arm C. No authority reading; the gate sorts it out afterwards, and
        # the rejections it produces are the number the pitch turns on.
        offers, filtered = await asyncio.to_thread(service.upsell.suggest_naive, q)
    else:
        headroom = await asyncio.to_thread(service.gate.headroom, request.mandate_id)
        if headroom is None:
            # Fail closed: no headroom means no offers, never "offer everything".
            offers, filtered = [], 0
        else:
            offers, filtered = await asyncio.to_thread(service.upsell.suggest, q, headroom)

    service.publish_stats()
    return {"addons": [a.model_dump() for a in offers], "filtered_out": filtered}


# -------------------------------------------------------------------- orders --


@app.post("/v1/orders")
async def create_order(request: CreateOrderRequest) -> dict:
    q = await asyncio.to_thread(service.quotes.get, request.quote_id)
    if q is None:
        raise HTTPException(409, {"reason_code": "QUOTE_EXPIRED"})

    # The settlement token is single use and the gate owns that fact. Redeem it
    # before doing anything else: an order placed without a redeemed token is an
    # order placed without authority.
    ok, code, decision_id = await asyncio.to_thread(
        service.gate.redeem, request.settlement_token, q.total_paise
    )
    if not ok or decision_id is None:
        raise HTTPException(403, {"reason_code": code})

    order_id_holder: dict = {}

    def run() -> None:
        result = service.saga.run(
            quote=q, mandate_id=_mandate_for(q.quote_id), decision_id=decision_id
        )
        order_id_holder["order_id"] = result.order_id
        if request.recovered_from:
            service.saga.link_recovery(result.order_id, request.recovered_from)
        service.publish_stats()

    # The saga runs behind the response so the console can watch the steps
    # arrive one at a time, which is what the failure demo needs.
    asyncio.create_task(asyncio.to_thread(run))
    await asyncio.sleep(0.05)
    return {"accepted": True, "decision_id": decision_id, "quote_id": q.quote_id}


def _mandate_for(quote_id: str) -> str:
    with service.db.read_tx() as conn:
        row = conn.execute("SELECT mandate_id FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
    return row["mandate_id"] if row and row["mandate_id"] else ""


@app.get("/v1/orders")
async def list_orders(limit: int = 50) -> dict:
    rows = await asyncio.to_thread(service.orders.list, min(limit, 200))
    return {"orders": [o.model_dump() for o in rows]}


@app.get("/v1/orders/{order_id}/saga")
async def order_saga(order_id: str) -> dict:
    return {"steps": await asyncio.to_thread(service.audit.list_steps, order_id)}


@app.post("/v1/orders/{order_id}/accept_alternative")
async def accept_alternative(order_id: str) -> dict:
    """
    The buyer takes the offer made after a rollback.

    Returns a replacement **quote**, not a completed order. The merchant holds
    no key and must never be able to spend on the buyer's behalf, so the buyer
    signs a fresh authorize against this quote and places the order the normal
    way, passing `recovered_from` so the revenue is attributed to the recovery.
    """
    result = await asyncio.to_thread(service.saga.offer_replacement_quote, order_id)
    if result is None:
        raise HTTPException(409, "no alternative on offer for that order")
    replacement, original = result
    service.upsell.record_acceptance()
    return {
        "quote": replacement.model_dump(),
        "recovered_from": original.order_id,
        "mandate_id": original.mandate_id,
    }


# --------------------------------------------------------------------- stats --


@app.get("/v1/stats")
async def stats() -> MerchantStats:
    return await asyncio.to_thread(service.stats.compute)


@app.get("/v1/stream")
async def stream(request: Request) -> EventSourceResponse:
    async def gen():
        async for event in service.bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": json.dumps(event.data)}

    return EventSourceResponse(gen(), ping=5)


# --------------------------------------------------------------------- admin --


@app.post("/admin/force_stockout")
async def force_stockout(payload: dict | None = None) -> dict:
    sku = (payload or {}).get("sku")
    forced = service.inventory.force_stockout(sku)
    return {"ok": True, "sku": forced}


@app.post("/admin/inject_failure")
async def inject_failure(payload: dict) -> dict:
    """
    Deterministic failure injection for the chaos suite.

    A chaos test you trigger by unplugging something is an anecdote. These
    switches make each scenario rerunnable, which is what lets Lane B report a
    rate rather than a story. Only the simulated rail exposes them — you cannot
    ask Razorpay to fail on demand.
    """
    failures = getattr(service.rail, "failures", None)
    if failures is None:
        raise HTTPException(
            400, f"the {service.rail.name} rail has no failure switches; use mock_upi"
        )
    for name in ("capture_fails", "refund_fails", "refund_pending"):
        if name in payload:
            setattr(failures, name, bool(payload[name]))
    return {
        "ok": True,
        "rail": service.rail.name,
        "capture_fails": failures.capture_fails,
        "refund_fails": failures.refund_fails,
        "refund_pending": failures.refund_pending,
    }


@app.post("/admin/simulate_webhook")
async def simulate_webhook(payload: dict) -> dict:
    """
    Deliver a rail callback for an order, signed so it actually verifies.

    Goes through the real WebhookProcessor rather than a shortcut, so the
    duplicate and out-of-order tests exercise the code that will run in
    production rather than a test-only path.
    """
    from rails.razorpay.webhooks import WebhookProcessor

    order = await asyncio.to_thread(service.orders.get, str(payload.get("order_id", "")))
    if order is None or not order.rail_payment_id:
        raise HTTPException(404, "no such order, or it has no payment id yet")

    event = str(payload.get("event", "payment.captured"))
    body = json.dumps(
        {
            "entity": "event",
            "account_id": "acc_SIM",
            "event": event,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": order.rail_payment_id,
                        "status": "captured" if event == "payment.captured" else "authorized",
                        "amount": order.amount_paise,
                    }
                }
            },
            "created_at": 0,
        }
    ).encode()

    signer = getattr(service.rail, "sign_callback", None)
    if signer is None:
        raise HTTPException(400, "this rail cannot self-sign a callback")

    processor = WebhookProcessor(service.db, service.rail, on_applied=_apply_webhook)
    try:
        outcome = await asyncio.to_thread(processor.process, body, signer(body))
    except PermissionError:
        raise HTTPException(400, "signature mismatch") from None

    return {
        "ok": True,
        "applied": outcome.applied,
        "duplicate": outcome.duplicate,
        "detail": outcome.detail,
    }


@app.post("/admin/simulate_dropped_webhook")
async def simulate_dropped_webhook(payload: dict) -> dict:
    """
    Pretend the capture confirmation never arrived, then let the reconciliation
    poller find the truth.

    Rewinding the order is the honest simulation: the money did move, and what
    is missing is only our knowledge of it, which is exactly the situation a
    dropped webhook leaves behind.
    """
    order_id = str(payload.get("order_id", ""))
    order = await asyncio.to_thread(service.orders.get, order_id)
    if order is None:
        raise HTTPException(404, "no such order")

    def rewind() -> None:
        with service.db.immediate_tx() as conn:
            conn.execute(
                "UPDATE orders SET state = 'GATE_ALLOWED', "
                "updated_at = '2000-01-01T00:00:00Z' WHERE order_id = ?",
                (order_id,),
            )

    await asyncio.to_thread(rewind)
    resolved = await asyncio.to_thread(service.saga.reconcile)
    after = await asyncio.to_thread(service.orders.get, order_id)
    service.publish_stats()
    return {
        "ok": True,
        "reconciled": resolved > 0,
        "state_after": after.state if after else "?",
    }


def _apply_webhook(order_id: str, state: str, entity: dict) -> None:
    service.audit.append_step(
        order_id=order_id,
        state=state,
        action="webhook.payment.captured",
        outcome="OK",
        detail="confirmed by webhook",
        ref=entity.get("id"),
    )
    service.publish_stats()


@app.post("/admin/reset")
async def reset() -> dict:
    await asyncio.to_thread(service.db.reset)
    service.inventory.reset()
    service.upsell.reset()
    if hasattr(service.rail, "reset"):
        service.rail.reset()
    service.bus.publish("reset", {"at": utcnow()})
    return {"ok": True}


@app.get("/v1/health")
async def health() -> dict:
    return {"ok": True, "rail": service.rail.name, "at": utcnow()}
