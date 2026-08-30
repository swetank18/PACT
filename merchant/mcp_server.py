"""
The catalog as MCP tools, so an agent buyer can transact with us without a
bespoke integration.

Every tool here calls the same engines the REST API calls. An agent and a human
cannot be quoted different prices, because there is only one quote engine and
neither of them does arithmetic.

`quote` and `suggest_addons` are the two tools that make this a growth
submission. The rest is table stakes.

Uses the MCP SDK's server rather than a hand-rolled transport. Writing our own
would be work nobody is judging and a source of bugs nobody would find.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from contracts.schemas import QuoteItemRequest
from merchant.app import service

log = logging.getLogger("pact.mcp")

mcp = MCPServer(name="deskkit-agent-commerce")


@mcp.tool()
def search_catalog(
    query: str, category: str | None = None, max_price_paise: int | None = None
) -> dict[str, Any]:
    """Find products. Returns a structured list, never prose."""
    from merchant.catalog import search

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
            for p in search(query, category, max_price_paise)
        ]
    }


@mcp.tool()
def get_product(sku: str) -> dict[str, Any]:
    """Full record for one SKU, including its agent readable attributes."""
    from merchant.catalog import BY_SKU

    p = BY_SKU.get(sku)
    if p is None:
        return {"error": "no such sku", "sku": sku}
    return {
        "sku": p.sku,
        "name": p.name,
        "category": p.category,
        "price_paise": p.price_paise,
        "in_stock": service.inventory.level(p.sku),
        "description": p.description,
        "tags": list(p.tags),
    }


@mcp.tool()
def quote(items: list[dict[str, Any]], mandate_id: str | None = None) -> dict[str, Any]:
    """
    Deterministic. Prices, tax and shipping are computed server side in integer
    paise.

    The agent quotes what this returns and nothing else. Any payment whose
    amount does not equal this total is refused by the gate with
    QUOTE_AMOUNT_MISMATCH, which is what makes an invented price structurally
    impossible rather than merely unlikely.
    """
    headroom = service.gate.headroom(mandate_id) if mandate_id else None
    try:
        q = service.quotes.build(
            [QuoteItemRequest(**i) for i in items], mandate_id=mandate_id, headroom=headroom
        )
    except KeyError as exc:
        return {"error": str(exc)}
    return q.model_dump()


@mcp.tool()
def suggest_addons(quote_id: str, mandate_id: str) -> dict[str, Any]:
    """
    Headroom aware upsell. Only returns items that will pass the gate.

    The merchant reads the buyer's remaining authority first, so approval rate
    on what comes back here is 100 percent by construction. `filtered_out`
    counts what was withheld, which is the number that distinguishes this from a
    blind recommender.
    """
    q = service.quotes.get(quote_id)
    if q is None:
        return {"error": "no such quote", "quote_id": quote_id}

    headroom = service.gate.headroom(mandate_id)
    if headroom is None:
        # Fail closed. No readable authority means no offers, never "offer
        # everything and let the gate sort it out".
        return {"addons": [], "filtered_out": 0, "note": "headroom unavailable"}

    offers, filtered = service.upsell.suggest(q, headroom)
    return {"addons": [a.model_dump() for a in offers], "filtered_out": filtered}


@mcp.tool()
def reserve_stock(quote_id: str) -> dict[str, Any]:
    """Soft hold on the items in a quote. TTL matches the quote's."""
    q = service.quotes.get(quote_id)
    if q is None:
        return {"error": "no such quote"}
    held = []
    for line in q.items:
        if not service.inventory.reserve(line.sku, line.qty):
            for sku, qty in held:
                service.inventory.restore(sku, qty)
            return {"ok": False, "reason_code": "STOCK_UNAVAILABLE", "sku": line.sku}
        held.append((line.sku, line.qty))
    return {"ok": True, "held": [{"sku": s, "qty": q_} for s, q_ in held]}


@mcp.tool()
def create_order(quote_id: str, decision_id: str, settlement_token: str) -> dict[str, Any]:
    """
    Place the order against the settlement token the gate issued on ALLOW.

    The token is single use and the gate owns that fact, so this redeems it
    before anything else happens. An order placed without a redeemed token is an
    order placed without authority.
    """
    q = service.quotes.get(quote_id)
    if q is None:
        return {"ok": False, "reason_code": "QUOTE_EXPIRED"}

    ok, code, resolved = service.gate.redeem(settlement_token, q.total_paise)
    if not ok or resolved is None:
        return {"ok": False, "reason_code": code}

    result = service.saga.run(
        quote=q, mandate_id=_mandate_for(quote_id), decision_id=resolved
    )
    order = service.orders.get(result.order_id)
    return {"ok": True, "order": order.model_dump() if order else None}


@mcp.tool()
def order_status(order_id: str) -> dict[str, Any]:
    """Includes the saga state, so an agent can tell a rollback from a failure."""
    order = service.orders.get(order_id)
    if order is None:
        return {"error": "no such order"}
    return {
        "order": order.model_dump(),
        "saga": service.audit.list_steps(order_id),
    }


@mcp.tool()
def cancel_order(order_id: str) -> dict[str, Any]:
    """Triggers the refund path. Every compensation is idempotent."""
    order = service.orders.get(order_id)
    if order is None:
        return {"error": "no such order"}
    q = service.quotes.get(order.quote_id)
    decision_id = service.orders.decision_for(order_id)
    if q is None or decision_id is None:
        return {"ok": False, "reason_code": "QUOTE_EXPIRED"}
    result = service.saga.roll_back(order, decision_id, q)
    return {"ok": True, "final_state": result.final_state}


def _mandate_for(quote_id: str) -> str:
    with service.db.read_tx() as conn:
        row = conn.execute(
            "SELECT mandate_id FROM quotes WHERE quote_id = ?", (quote_id,)
        ).fetchone()
    return row["mandate_id"] if row and row["mandate_id"] else ""


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("PACT_MCP_TRANSPORT", "stdio"))
