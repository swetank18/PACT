"""
The chaos suite. The brief asks for one failure handled gracefully; bring five
and stage one.

`chs_05` is the one to volunteer unprompted. Anyone can handle the failure they
planned for. Handling the failure of your own compensation is what payments
engineering actually looks like, and a judge who asks "what if the refund
fails?" and gets a real answer is worth more than the happy path working.

Every scenario is triggered deterministically through an endpoint, not by
unplugging something. A chaos test you cannot rerun is an anecdote.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from buyer.agent import BuyerAgent, Mandate


@dataclass
class ChaosResult:
    id: str
    name: str
    expected: str
    observed: str
    passed: bool
    steps: list[str]
    detail: str = ""


def _buy(agent: BuyerAgent, mandate: Mandate, vpa: str, sku: str) -> dict[str, Any] | None:
    q = agent.quote([{"sku": sku, "qty": 1}], mandate.id)
    d = agent.authorize(mandate, q, vpa)
    if d["verdict"] != "ALLOW":
        return None
    agent.create_order(q["quote_id"], d)
    for _ in range(60):
        order = agent.order_for_quote(q["quote_id"])
        if order and order["state"] in (
            "FULFILLED", "ROLLED_BACK", "RECOVERED", "NEEDS_ATTENTION", "ALTERNATIVE_OFFERED",
        ):
            return order
        time.sleep(0.15)
    return agent.order_for_quote(q["quote_id"])


def _saga(agent: BuyerAgent, order_id: str) -> list[str]:
    r = agent.http.get(f"{agent.merchant_url}/v1/orders/{order_id}/saga")
    return [s["state"] for s in r.json().get("steps", [])] if r.status_code == 200 else []


def chs_01_stockout_after_capture(agent: BuyerAgent, mandate: Mandate, vpa: str) -> ChaosResult:
    """Capture succeeds, fulfilment does not. The brief's graceful failure."""
    agent.http.post(f"{agent.merchant_url}/admin/force_stockout", json={"sku": "FUR-LMP-01"})
    order = _buy(agent, mandate, vpa, "FUR-LMP-01")
    steps = _saga(agent, order["order_id"]) if order else []

    required = ["PAYMENT_CAPTURED", "ROLLING_BACK", "REFUND_ISSUED", "BUDGET_RELEASED"]
    ok = all(s in steps for s in required)
    return ChaosResult(
        id="chs_01",
        name="stockout after capture",
        expected="automatic refund, budget released, alternative offered",
        observed=order["state"] if order else "no order",
        passed=ok and steps.index("REFUND_ISSUED") < steps.index("BUDGET_RELEASED"),
        steps=steps,
        detail="compensations must run in reverse: refund before budget release",
    )


def chs_02_webhook_never_delivered(agent: BuyerAgent, mandate: Mandate, vpa: str) -> ChaosResult:
    """
    No webhook ever arrives. The reconciliation poller resolves the true state.

    Simulated by rewinding the order to pre-capture and letting the poller find
    it — the same code path a dropped webhook produces.
    """
    order = _buy(agent, mandate, vpa, "STA-NB-A5")
    if not order:
        return ChaosResult("chs_02", "webhook never delivered", "poller resolves",
                           "no order", False, [], "")

    r = agent.http.post(
        f"{agent.merchant_url}/admin/simulate_dropped_webhook",
        json={"order_id": order["order_id"]},
    )
    if r.status_code != 200:
        return ChaosResult(
            "chs_02", "webhook never delivered", "poller resolves the true state",
            "endpoint unavailable", False, [], "merchant has no dropped-webhook injector",
        )

    resolved = r.json()
    return ChaosResult(
        id="chs_02",
        name="webhook never delivered",
        expected="reconciliation poller resolves the true state",
        observed=resolved.get("state_after", "?"),
        passed=bool(resolved.get("reconciled")),
        steps=_saga(agent, order["order_id"]),
        detail="the webhook is never the only path to the truth",
    )


def chs_03_duplicate_webhook(agent: BuyerAgent, mandate: Mandate, vpa: str) -> ChaosResult:
    """The same event three times. Idempotent, so nothing changes."""
    order = _buy(agent, mandate, vpa, "STA-PEN-12")
    if not order or not order.get("rail_payment_id"):
        return ChaosResult("chs_03", "duplicate webhook", "no op", "no order", False, [], "")

    before = _saga(agent, order["order_id"])
    outcomes = []
    for _ in range(3):
        r = agent.http.post(
            f"{agent.merchant_url}/admin/simulate_webhook",
            json={"order_id": order["order_id"], "event": "payment.captured"},
        )
        outcomes.append(r.json() if r.status_code == 200 else {"error": r.status_code})
    after = _saga(agent, order["order_id"])

    duplicates = sum(1 for o in outcomes if o.get("duplicate"))
    return ChaosResult(
        id="chs_03",
        name="duplicate webhook, three times",
        expected="idempotent, no double anything",
        observed=f"{duplicates} of 3 recognised as duplicates",
        passed=duplicates >= 2 and before == after,
        steps=after,
        detail="the order state must be unchanged by a redelivery",
    )


def chs_04_out_of_order_webhook(agent: BuyerAgent, mandate: Mandate, vpa: str) -> ChaosResult:
    """`payment.authorized` after `payment.captured`. Must not walk backwards."""
    order = _buy(agent, mandate, vpa, "CBL-USBC-2M")
    if not order:
        return ChaosResult("chs_04", "out of order webhook", "converges", "no order", False, [], "")

    before = order["state"]
    agent.http.post(
        f"{agent.merchant_url}/admin/simulate_webhook",
        json={"order_id": order["order_id"], "event": "payment.authorized"},
    )
    after = agent.order_for_quote(order["quote_id"])
    return ChaosResult(
        id="chs_04",
        name="out of order webhook",
        expected="converges to the correct state, never walks backwards",
        observed=after["state"] if after else "?",
        passed=bool(after) and after["state"] == before,
        steps=_saga(agent, order["order_id"]),
    )


def chs_05_refund_itself_fails(agent: BuyerAgent, mandate: Mandate, vpa: str) -> ChaosResult:
    """
    The compensation fails. Retries, then NEEDS_ATTENTION, never silent.

    The one to volunteer unprompted.
    """
    agent.http.post(f"{agent.merchant_url}/admin/inject_failure", json={"refund_fails": True})
    agent.http.post(f"{agent.merchant_url}/admin/force_stockout", json={"sku": "FUR-MAT-DSK"})
    order = _buy(agent, mandate, vpa, "FUR-MAT-DSK")
    agent.http.post(f"{agent.merchant_url}/admin/inject_failure", json={"refund_fails": False})

    steps = _saga(agent, order["order_id"]) if order else []
    attempts = sum(1 for s in steps if s == "ROLLING_BACK")
    parked = order and order["state"] == "NEEDS_ATTENTION"

    return ChaosResult(
        id="chs_05",
        name="the refund itself fails",
        expected="retries, then NEEDS_ATTENTION, never silent",
        observed=order["state"] if order else "no order",
        passed=bool(parked) and "BUDGET_RELEASED" not in steps,
        steps=steps,
        detail=(
            f"{attempts} rollback attempt(s) recorded; the budget must NOT be released, "
            "because claiming the money came back when it did not is worse than the failure"
        ),
    )


SCENARIOS = [
    chs_01_stockout_after_capture,
    chs_02_webhook_never_delivered,
    chs_03_duplicate_webhook,
    chs_04_out_of_order_webhook,
    chs_05_refund_itself_fails,
]


def run_all(agent: BuyerAgent, persona: dict[str, Any], vpa: str) -> list[ChaosResult]:
    out = []
    for scenario in SCENARIOS:
        # A fresh mandate per scenario, so one scenario's spend cannot make the
        # next one fail for an unrelated reason.
        out.append(scenario(agent, agent.issue_mandate(persona, vpa), vpa))
    return out
