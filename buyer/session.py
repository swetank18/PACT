"""
One shopping session, from discovery to settlement.

The arms differ only in the two flags. Everything else — the catalog, the quote
engine, the merchant, the rail — is identical, which is the only way the numbers
compare.

The part worth reading is `_handle_refusal`. On BLOCK, the agent reads the reason
code and repairs its own order: over the per-transaction cap, drop the most
expensive line and requote; category refused, drop the offending line; out of
budget, shrink. Most implementations crash or give up here, and the difference
between giving up and repairing is a large part of the gap between arm C and
arm D.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.ids import new_id
from contracts.money import Paise
from buyer.agent import BuyerAgent, Mandate, SessionResult
from sim import hostile

log = logging.getLogger("pact.session")

MAX_REPAIRS = 3
#: How long to wait for the saga to reach a terminal state before giving up on
#: observing it. The saga is asynchronous by design so the console can watch it.
SAGA_POLL_ATTEMPTS = 40
SAGA_POLL_SLEEP_S = 0.15

TERMINAL = {"FULFILLED", "RECOVERED", "ROLLED_BACK", "NEEDS_ATTENTION"}

#: Codes that mean "this offer should never have been made". Signature and
#: replay failures are agent bugs, not bad recommendations, so they are not here.
UPSELL_REJECTION_CODES = {
    "CEILING_PER_TXN",
    "CEILING_TOTAL",
    "CEILING_COUNT",
    "SCOPE_CATEGORY_MISMATCH",
}


def run_session(
    agent: BuyerAgent,
    persona: dict[str, Any],
    *,
    arm: str,
    manifest: dict[str, Any],
    hostile_rate: float = 0.0,
    force_skus: list[str] | None = None,
    allow_repair: bool = True,
) -> SessionResult:
    import time

    result = SessionResult(
        sim_id=new_id("sim"), persona=persona["id"], arm=arm, seed=agent.seed
    )
    merchant_vpa = manifest["merchant_vpa"]

    try:
        mandate = agent.issue_mandate(persona, merchant_vpa)
        result.say(f"mandate {mandate.id} signed on device")

        # 1. Browse toward the goal.
        if force_skus:
            # A stage demo cannot depend on a random basket. The simulation
            # samples the catalog; the beats name their items.
            items = [{"sku": sku, "qty": 1} for sku in force_skus]
            result.say(f"browsing for {', '.join(force_skus)}")
        else:
            products = agent.search(persona["goal"])
            wanted = _pick_basket(agent, persona, products)
            if not wanted:
                result.say("nothing in the catalog matched")
                return result
            items = [{"sku": p["sku"], "qty": 1} for p in wanted]
        quote = agent.quote(items, mandate.id)
        result.say(f"quote {quote['quote_id']} for {quote['total_paise']} paise")

        # 2. The upsell. This is where the arms separate on revenue.
        quote, addon_taken = _consider_addons(agent, mandate, quote, persona, result)

        # 3. Authorise, repairing on a structured refusal.
        decision = _authorize_with_repair(
            agent,
            mandate,
            quote,
            merchant_vpa,
            persona,
            result,
            carrying_addon=addon_taken,
            allow_repair=allow_repair,
        )
        if decision is None:
            return result
        quote = decision["_quote"]

        if decision["verdict"] != "ALLOW":
            return result

        # 4. Settle, and watch the saga.
        agent.create_order(quote["quote_id"], decision)
        order = _await_order(agent, quote["quote_id"], result)
        if order is None:
            result.say("the order never appeared")
            return result

        result.order_ids.append(order["order_id"])
        final = _await_terminal(agent, order["order_id"], result)

        if final["state"] == "FULFILLED":
            result.completed = True
            result.gmv_paise += final["amount_paise"]
            result.loss_paise += _unauthorised(final["amount_paise"], mandate)
            if addon_taken:
                result.upsell_accepted += 1
            result.say(f"fulfilled, {final['amount_paise']} paise")

        elif final["state"] in ("ROLLED_BACK", "ALTERNATIVE_OFFERED"):
            result.refunded_paise += final["amount_paise"]
            # 5. The rollback recovery. Evaluate the offered alternative rather
            #    than treating a refund as the end of the session.
            recovered = _try_recovery(agent, mandate, final, merchant_vpa, result)
            if recovered:
                result.completed = True
                result.saga_recoveries += 1
                result.gmv_paise += recovered
                result.say(f"recovered, {recovered} paise")

        elif final["state"] == "NEEDS_ATTENTION":
            # Not a loss: the money is visible and parked for a human. Counting
            # it as revenue or as loss would both be wrong.
            result.say("parked in NEEDS_ATTENTION")

        # A share of sessions are adversarial, in every arm. A revenue table
        # where every session is honest measures nothing about why the gate
        # exists, and the attack suite on its own does not show what the
        # attacks cost.
        if hostile_rate and hostile.should_be_hostile(agent.rng, hostile_rate):
            result.hostile = True
            hostile.attempt(agent, mandate, merchant_vpa, result)

    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("session %s failed", result.sim_id)

    return result


def _unauthorised(amount: Paise, mandate: Mandate) -> Paise:
    """
    Money that settled outside what the human actually authorised.

    This is the "fraud and error loss" column, and it is defined the same way
    for every arm: compare what settled against the mandate the human signed.
    An arm with no ceiling check will let a purchase through that exceeds the
    per-transaction cap, and the excess is a real loss to the merchant — it is
    the transaction the buyer disputes.

    Defined here rather than per-arm on purpose. A loss metric that only exists
    in the arm you want to look bad is not a measurement.
    """
    cap = mandate.constraints["max_per_txn_paise"]
    return max(0, amount - cap)


# ------------------------------------------------------------------ basket ---


def _pick_basket(agent: BuyerAgent, persona: dict[str, Any], products: list[dict]) -> list[dict]:
    """
    What the persona came for. Seeded, so a rerun buys the same things.

    Filters to what the mandate allows *before* the gate sees it — a buyer agent
    that knowingly shops outside its own scope is not a realistic baseline, it
    is a broken one, and it would inflate every arm's block rate equally.
    """
    allowed = set(persona["mandate"]["categories"])
    pool = [p for p in products if p["category"] in allowed and p["in_stock"] > 0]
    if not pool:
        pool = [p for p in products if p["in_stock"] > 0]
    if not pool:
        return []

    size = min(len(pool), persona.get("basket_size", 2))
    return agent.rng.sample(pool, size)


def _consider_addons(
    agent: BuyerAgent,
    mandate: Mandate,
    quote: dict[str, Any],
    persona: dict[str, Any],
    result: SessionResult,
) -> tuple[dict[str, Any], bool]:
    """
    Returns the quote to buy and whether an addon rode along.

    `off` never asks. `naive` and `headroom` both ask the same endpoint — the
    difference is on the merchant's side, which is the point: the merchant with
    a readable authority envelope filters before offering, and the one without
    it cannot.
    """
    if agent.upsell_mode == "off":
        return quote, False

    offer = agent.suggest_addons(quote["quote_id"], mandate.id)
    addons = offer.get("addons", [])
    if not addons:
        return quote, False

    result.upsell_offered += len(addons)
    receptivity = persona.get("addon_receptivity", 0.5)
    if agent.rng.random() > receptivity:
        result.say(f"{len(addons)} addon(s) offered, declined")
        return quote, False

    chosen = addons[0]
    combined = [{"sku": i["sku"], "qty": i["qty"]} for i in quote["items"]]
    combined.append({"sku": chosen["sku"], "qty": 1})
    result.say(f"took the addon {chosen['sku']}")
    return agent.quote(combined, mandate.id), True


# --------------------------------------------------------------- authorize ---


def _authorize_with_repair(
    agent: BuyerAgent,
    mandate: Mandate,
    quote: dict[str, Any],
    merchant_vpa: str,
    persona: dict[str, Any],
    result: SessionResult,
    *,
    carrying_addon: bool = False,
    #: An agent that does not read reason codes. Off for the contrast beat,
    #: where the point is what happens when a refusal is not actionable.
    allow_repair: bool = True,
) -> dict[str, Any] | None:
    """
    Ask, and on a structured refusal, fix the order and ask again.

    Arm B skips the gate entirely. Arm C applies a hard client-side cap and
    simply stops when it trips — which is exactly the behaviour that loses
    legitimate sales, and exactly why arm C leaves money on the table.
    """
    if agent.gate_mode == "naive":
        cap = mandate.constraints["max_per_txn_paise"]
        if quote["total_paise"] > cap:
            # A hard cap with no way to ask. The sale is simply lost, and if the
            # purchase was legitimate that is a false block.
            result.false_blocks += 1
            result.say(f"naive cap refused {quote['total_paise']} over {cap}; session ends")
            return {"verdict": "BLOCK", "reason_code": "CEILING_PER_TXN", "_quote": quote}

    for attempt in range(MAX_REPAIRS + 1):
        decision = agent.authorize(mandate, quote, merchant_vpa)
        decision["_quote"] = quote
        result.decisions.append(decision)

        if decision["verdict"] == "ALLOW":
            return decision

        if decision["verdict"] == "STEP_UP":
            result.step_ups += 1
            # The human is asked. In simulation the persona's tolerance decides,
            # seeded so the answer is reproducible.
            approves = agent.rng.random() < persona.get("step_up_approval", 0.8)
            answer = agent.resolve_step_up(decision, mandate, approves)
            if answer.get("verdict") == "ALLOW":
                result.step_ups_recovered += 1
                decision["verdict"] = "ALLOW"
                decision["settlement_token"] = answer.get("settlement_token")
                result.say("step up approved by the human")
                return decision
            result.say("step up refused by the human")
            return decision

        # An upsell rejection is a BLOCK on a quote that actually carried an
        # offered addon — not any BLOCK on a ceiling or scope code. A basket
        # that was too big before any addon was suggested is the agent's own
        # doing, and counting it here would attribute the agent's mistake to the
        # merchant's recommender. That distinction is the difference between a
        # measured number and a flattering one, and this is the number the whole
        # comparison turns on.
        if carrying_addon and decision["reason_code"] in UPSELL_REJECTION_CODES:
            result.upsell_rejected_by_gate += 1
            carrying_addon = False  # count it once per session, not once per repair

        # BLOCK. Read the code and repair, rather than crashing.
        if not allow_repair:
            result.say(f"no repair attempted: {decision['reason_code']}; session ends")
            return decision
        if attempt == MAX_REPAIRS:
            result.say(f"gave up after {MAX_REPAIRS} repairs: {decision['reason_code']}")
            return decision

        repaired = _repair(agent, mandate, quote, decision, result)
        if repaired is None:
            result.say(f"unrepairable: {decision['reason_code']}")
            return decision
        quote = repaired
        result.repairs += 1

    return None


def _repair(
    agent: BuyerAgent,
    mandate: Mandate,
    quote: dict[str, Any],
    decision: dict[str, Any],
    result: SessionResult,
) -> dict[str, Any] | None:
    """
    Turn a reason code into a smaller order.

    This is why the reason codes are a frozen enum rather than prose: an agent
    can branch on them. `CEILING_*` means shrink, `SCOPE_CATEGORY_MISMATCH`
    means drop the offending line. Everything else is not a repair — a replay or
    a bad signature is a bug in the agent, not an order that is too big.
    """
    code = decision["reason_code"]
    items = [{"sku": i["sku"], "qty": i["qty"], "_line": i} for i in quote["items"]]

    if code in ("CEILING_PER_TXN", "CEILING_TOTAL"):
        if len(items) <= 1:
            return None
        # Drop the most expensive line. Crude, and it is the right kind of crude:
        # the claim is that a structured refusal is actionable, not that we have
        # a clever basket optimiser.
        items.sort(key=lambda i: i["_line"]["line_total_paise"], reverse=True)
        dropped = items.pop(0)
        result.say(f"repairing after {code}: dropped {dropped['sku']}")

    elif code == "SCOPE_CATEGORY_MISMATCH":
        allowed = set(mandate.constraints["category_allowlist"])
        keep = [i for i in items if i["_line"]["category"] in allowed]
        if not keep or len(keep) == len(items):
            return None
        result.say(f"repairing after {code}: dropped out-of-scope lines")
        items = keep

    elif code == "QUOTE_EXPIRED":
        result.say("requoting after expiry")

    else:
        return None

    return agent.quote([{"sku": i["sku"], "qty": i["qty"]} for i in items], mandate.id)


# -------------------------------------------------------------------- saga ---


def _await_order(agent: BuyerAgent, quote_id: str, result: SessionResult) -> dict | None:
    import time

    for _ in range(SAGA_POLL_ATTEMPTS):
        order = agent.order_for_quote(quote_id)
        if order:
            return order
        time.sleep(SAGA_POLL_SLEEP_S)
    return None


def _await_terminal(agent: BuyerAgent, order_id: str, result: SessionResult) -> dict:
    """
    Wait for the saga to settle.

    ALTERNATIVE_OFFERED counts as terminal here because it is terminal *for the
    merchant* — the saga has stopped and is waiting on the buyer. Treating it as
    in-flight would mean timing out on exactly the case the recovery flow exists
    to handle.
    """
    import time

    last: dict = {"state": "UNKNOWN", "amount_paise": 0, "order_id": order_id}
    for _ in range(SAGA_POLL_ATTEMPTS):
        r = agent.http.get(f"{agent.merchant_url}/v1/orders", params={"limit": 50})
        for o in r.json().get("orders", []):
            if o["order_id"] == order_id:
                last = o
                if o["state"] in TERMINAL or o["state"] == "ALTERNATIVE_OFFERED":
                    return o
        time.sleep(SAGA_POLL_SLEEP_S)
    return last


def _try_recovery(
    agent: BuyerAgent,
    mandate: Mandate,
    rolled_back: dict[str, Any],
    merchant_vpa: str,
    result: SessionResult,
) -> Paise:
    """
    The merchant offered something else. Decide, and if yes, buy it properly.

    The replacement goes through the gate like any other purchase — the merchant
    cannot spend for us, so a recovery that skipped the gate would not be a
    recovery, it would be a hole.
    """
    offer = agent.accept_alternative(rolled_back["order_id"])
    if not offer or "quote" not in offer:
        return 0

    replacement = offer["quote"]
    decision = agent.authorize(mandate, replacement, merchant_vpa)
    if decision["verdict"] != "ALLOW":
        result.say(f"the alternative was refused: {decision['reason_code']}")
        return 0

    agent.create_order(replacement["quote_id"], decision, recovered_from=offer["recovered_from"])
    order = _await_order(agent, replacement["quote_id"], result)
    if order is None:
        return 0
    final = _await_terminal(agent, order["order_id"], result)
    result.order_ids.append(order["order_id"])
    return final["amount_paise"] if final["state"] in ("FULFILLED", "RECOVERED") else 0
