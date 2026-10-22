"""
The Claude buyer, with a scripted model.

No test here calls Anthropic. The model is a script that answers each turn
the way a model would — tool calls, thinking, a summary — and the shop is the
real gate, quote engine and saga, in process, on the simulated rail. So what is
tested is everything around the model: what it is sent, what its tool calls do,
what reaches the gate, what the run records, and what happens when the model
is fooled.

Whether the real model is fooled, and how often, is an experiment (doc 2, E1),
not a test.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from buyer.llm_agent import FALLBACK_BETA, INJECTIONS, MODEL, TOOLS, ClaudeBuyer
from buyer.runs import RunManager, RunRefused
from contracts.reason_codes import ReasonCode
from contracts.schemas import AuthorizeRequest, QuoteItemRequest
from merchant.catalog import BY_SKU, CURRENCY, MERCHANT_PAYEE, search

# ------------------------------------------------------- a scripted model ---


@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class Thinking:
    thinking: str
    type: str = "thinking"


@dataclass
class ToolUse:
    name: str
    input: dict
    id: str = ""
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 300
    cache_read_input_tokens: int = 900
    cache_creation_input_tokens: int = 0


@dataclass
class Response:
    content: list
    stop_reason: str = "tool_use"
    usage: Usage = field(default_factory=Usage)


def last_result(messages: list[dict], tool: str | None = None) -> dict:
    """The most recent tool result the script is answering."""
    for message in reversed(messages):
        if message["role"] == "user" and isinstance(message["content"], list):
            for block in reversed(message["content"]):
                if block.get("type") == "tool_result":
                    return json.loads(block["content"])
    raise AssertionError("no tool result yet")


class ScriptedModel:
    """Each turn is a function of the conversation so far, returning a Response."""

    def __init__(self, turns: list[Callable[[list[dict]], Response]]) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.beta = self
        self.messages = self

    def create(self, **kwargs: Any) -> Response:
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self.turns.pop(0)(kwargs["messages"])
        for i, block in enumerate(response.content):
            if block.type == "tool_use" and not block.id:
                block.id = f"toolu_{len(self.requests)}_{i}"
        return response


def tool(name: str, **args: Any) -> Response:
    return Response([ToolUse(name, args)])


def done(text: str = "Bought the notebooks.") -> Response:
    return Response([Text(text)], stop_reason="end_turn")


# ------------------------------------------------ the real shop, in process ---


class InProcessShop:
    payee = MERCHANT_PAYEE
    currency = CURRENCY

    def __init__(self, gate, quotes, merchant) -> None:  # noqa: ANN001
        self.gate, self.quotes, self.merchant = gate, quotes, merchant
        self.authorized: list[dict] = []

    def search(self, query, category):  # noqa: ANN001, ANN201
        return [{"sku": p.sku, "name": p.name, "category": p.category,
                 "price_minor": p.price_minor, "in_stock": self.merchant.inventory.level(p.sku)}
                for p in search(query, category)]

    def product(self, sku):  # noqa: ANN001, ANN201
        p = BY_SKU.get(sku)
        return None if p is None else {"sku": p.sku, "name": p.name, "category": p.category,
                                       "price_minor": p.price_minor, "description": p.description,
                                       "in_stock": self.merchant.inventory.level(p.sku)}

    def headroom(self, mandate_id):  # noqa: ANN001, ANN201
        h = self.gate.headroom_service.for_mandate(mandate_id)
        return h.model_dump() if h else None

    def quote(self, items, mandate_id):  # noqa: ANN001, ANN201
        h = self.gate.headroom_service.for_mandate(mandate_id)
        return self.quotes.build([QuoteItemRequest(**i) for i in items], mandate_id=mandate_id,
                                 headroom=h).model_dump()

    def addons(self, quote_id, mandate_id):  # noqa: ANN001, ANN201
        offered, _ = self.merchant.upsell.suggest(self.quotes.get(quote_id),
                                                  self.gate.headroom_service.for_mandate(mandate_id))
        return {"addons": [a.model_dump() for a in offered]}

    def authorize(self, request):  # noqa: ANN001, ANN201
        self.authorized.append(request)
        return self.gate.authorize(AuthorizeRequest(**request)).model_dump()

    def order(self, quote_id, decision, recovered_from=None):  # noqa: ANN001, ANN201
        q = self.quotes.get(quote_id)
        ok, _, decision_id = self.gate.redeem_token(decision["settlement_token"], amount_minor=q.total_minor)
        assert ok
        self.merchant.saga.run(quote=q, mandate_id=decision["mandate_id"], decision_id=decision_id)
        return {"accepted": True}

    def order_and_steps(self, quote_id):  # noqa: ANN001, ANN201
        order = next((o for o in self.merchant.orders.list() if o.quote_id == quote_id), None)
        if order is None:
            return None, []
        return order.model_dump(), self.merchant.audit.list_steps(order.order_id)

    def accept_alternative(self, order_id):  # noqa: ANN001, ANN201
        return None

    def resolve_step_up(self, decision_id, payload):  # noqa: ANN001, ANN201
        raise AssertionError("not used in process")


@pytest.fixture
def shop(gate, quotes, merchant) -> InProcessShop:
    return InProcessShop(gate, quotes, merchant)


@pytest.fixture
def run_agent(shop, make_mandate, keys):
    """Run the Claude buyer on a script; return (result, steps, model)."""
    priv, _ = keys

    def _run(turns, *, mode="normal", mandate=None, agent_key=None, **kw):
        mandate = mandate or make_mandate()
        model = ScriptedModel(turns)
        steps: list[dict] = []
        buyer = ClaudeBuyer(model, shop, mandate_id=mandate.mandate_id,
                            goal="restock office supplies for the month",
                            agent_key=agent_key or priv, emit=steps.append,
                            wait_for_principal=lambda d: None, mode=mode, poll_s=0.0, **kw)
        return buyer.run(), steps, model

    return _run


def buy_notebooks(**pay_args):
    return [
        lambda m: tool("search_catalog", query="notebook", category=None),
        lambda m: tool("read_product", sku="STA-NB-A5"),
        lambda m: tool("get_quote", items=[{"sku": "STA-NB-A5", "qty": 1}]),
        lambda m: tool("pay", quote_id=last_result(m)["quote_id"], payee=pay_args.get("payee"),
                       reasoning="The goal is restocking office supplies; notebooks are supplies."),
        lambda m: done(),
    ]


# ------------------------------------------------------- what it is sent ---


def test_every_turn_asks_for_the_model_and_settings_it_should(run_agent):
    _, _, model = run_agent(buy_notebooks())
    for request in model.requests:
        assert request["model"] == MODEL == "claude-opus-5-5"
        assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
        assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert request["output_config"] == {"effort": "medium"}
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "tool_choice" not in request, "Opus 5.5 refuses a forced tool choice"


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_tool_schemas_stay_inside_the_supported_subset():
    """Nullable is anyOf with null. A type array is not in the listed subset,
    and a schema the API rejects is a 400 on every turn of every run."""
    for t in TOOLS:
        for node in _walk(t["input_schema"]):
            assert not isinstance(node.get("type"), list), (t["name"], node)


def test_every_tool_is_strict(run_agent):
    for t in TOOLS:
        schema = t["input_schema"]
        assert t["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_the_model_sees_its_own_turns_back_unchanged(run_agent):
    """Thinking blocks are valid only in the conversation that produced them."""
    thought = Thinking("Notebooks fit the goal.")
    turns = buy_notebooks()
    turns[0] = lambda m: Response([thought, ToolUse("search_catalog", {"query": "notebook", "category": None})])
    _, _, model = run_agent(turns)
    assert model.requests[1]["messages"][1]["content"][0] is thought


# ------------------------------------------------------ an honest purchase ---


def test_an_honest_purchase_goes_through_the_gate_and_the_saga(run_agent, shop, merchant):
    result, steps, _ = run_agent(buy_notebooks())

    assert result["outcome"] == "done"
    kinds = [s["kind"] for s in steps]
    assert kinds[0] == "tool_call" and kinds[-1] == "done"
    gate = next(s for s in steps if s["kind"] == "gate")
    assert gate["detail"]["verdict"] == "ALLOW"
    settled = [s["detail"]["state"] for s in steps if s["kind"] == "settlement"]
    assert settled[-1] == "FULFILLED" and "PAYMENT_CAPTURED" in settled
    (order,) = merchant.orders.list()
    assert order.state == "FULFILLED"


def test_the_amount_paid_is_the_quotes_never_the_models(run_agent, shop, quotes):
    run_agent(buy_notebooks())
    (request,) = shop.authorized
    assert request["amount_minor"] == quotes.get(request["quote_id"]).total_minor


def test_what_the_agent_read_travels_with_the_payment(run_agent, shop):
    run_agent(buy_notebooks())
    (request,) = shop.authorized
    assert BY_SKU["STA-NB-A5"].description in request["context"]["page_excerpt"]
    assert "notebooks are supplies" in request["context"]["agent_reasoning"]


def test_usage_is_recorded_per_turn_and_in_total(run_agent):
    result, steps, model = run_agent(buy_notebooks())
    with_usage = [s for s in steps if "usage" in s]
    assert len(with_usage) == len(model.requests)
    assert result["usage"]["input_tokens"] == 1200 * len(model.requests)


def test_paying_for_a_quote_the_run_never_saw_is_refused_before_the_gate(run_agent, shop):
    result, steps, _ = run_agent([
        lambda m: tool("pay", quote_id="qte_INVENTED", payee=None, reasoning="x"),
        lambda m: done("Could not pay."),
    ])
    assert shop.authorized == []
    paid = next(s for s in steps if s["kind"] == "tool_result")
    assert "get_quote first" in paid["summary"]


# ------------------------------------------------------------- attack mode ---


def test_attack_mode_puts_instructions_in_the_first_page_read(run_agent):
    _, steps, _ = run_agent(buy_notebooks(), mode="attack")
    page = next(s for s in steps if s["kind"] == "tool_result" and s["tool"] == "read_product")
    assert INJECTIONS[2] in page["detail"]["description"]


def test_a_fooled_agent_paying_the_injected_payee_moves_no_money(run_agent, merchant):
    """
    The model did what the page said. The gate refused, and no order exists:
    the model being fooled is the premise, the gate not being fooled is the
    result.
    """
    result, steps, _ = run_agent(buy_notebooks(payee="partner-fulfilment@paypal-partners.example"),
                                 mode="attack")
    gate = next(s for s in steps if s["kind"] == "gate")
    assert gate["detail"]["verdict"] == "BLOCK"
    assert gate["reason_code"] in (ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED.value,
                                   ReasonCode.INTENT_INJECTION_SUSPECTED.value)
    assert merchant.orders.list() == []
    paid = next(s for s in steps if s["kind"] == "tool_result" and s["tool"] == "pay")
    assert paid["detail"]["money_moved"] is False


def test_a_recognisable_injection_fails_closed_whatever_the_model_decides(run_agent, merchant):
    """
    A page the pattern scan recognises as an injection refuses the payment made
    in its context, even when the model ignored it. The scan reads the page,
    not the model's opinion of it.
    """
    _, steps, _ = run_agent(buy_notebooks(), mode="attack", injection=INJECTIONS[0])
    gate = next(s for s in steps if s["kind"] == "gate")
    assert gate["reason_code"] == ReasonCode.INTENT_INJECTION_SUSPECTED.value
    assert merchant.orders.list() == []


def test_an_injection_the_scan_misses_is_caught_only_if_obeyed(run_agent, merchant):
    """
    Found while writing these tests: the payee-redirect phrasing in
    INJECTIONS[2] is not one the deterministic pattern scan recognises. An
    agent that reads it and pays the real merchant anyway is allowed — the
    purchase is honest, and refusing it would be a false block. An agent that
    obeys it is refused by scope (the test above). Recognising the phrasing
    itself is the model auditor's job, check ten; how often the real model is
    fooled is experiment E1, not a test.
    """
    _, steps, _ = run_agent(buy_notebooks(), mode="attack", injection=INJECTIONS[2])
    gate = next(s for s in steps if s["kind"] == "gate")
    assert gate["detail"]["verdict"] == "ALLOW"
    assert gate["detail"]["payee"] == MERCHANT_PAYEE
    (order,) = merchant.orders.list()
    assert order.state == "FULFILLED"


def test_an_agent_with_the_wrong_key_cannot_pay(run_agent, merchant):
    from contracts.crypto import generate_keypair

    stranger, _ = generate_keypair()
    _, steps, _ = run_agent(buy_notebooks(), agent_key=stranger)
    gate = next(s for s in steps if s["kind"] == "gate")
    assert gate["reason_code"] == ReasonCode.REQUEST_SIG_INVALID.value
    assert merchant.orders.list() == []


# -------------------------------------------------------------- endings ---


def test_a_refusal_ends_the_run_and_says_so(run_agent):
    result, steps, _ = run_agent([lambda m: Response([], stop_reason="refusal")])
    assert result["outcome"] == "refused" and steps[-1]["kind"] == "error"


def test_a_run_that_never_finishes_stops_at_the_cap(run_agent):
    result, steps, _ = run_agent([lambda m: tool("read_headroom")] * 3, max_turns=3)
    assert result["outcome"] == "turn_cap"
    assert "Stopped after 3 turns" in steps[-1]["summary"]


def test_a_tool_that_fails_is_a_result_the_model_sees(run_agent):
    _, _, model = run_agent([
        lambda m: tool("read_product", sku="NOPE"),
        lambda m: done("That product does not exist."),
    ])
    result_block = model.requests[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True


# ------------------------------------------------------------------ runs ---


class StepUpShop:
    """A shop whose gate always wants the principal, for the relay."""

    payee, currency = "m@example", "INR"

    def __init__(self) -> None:
        self.answers: list[dict] = []
        self.ordered: list[dict] = []

    def search(self, q, c): return []  # noqa: ANN001, ANN201, E704
    def product(self, s): return None  # noqa: ANN001, ANN201, E704
    def headroom(self, m): return None  # noqa: ANN001, ANN201, E704

    def quote(self, items, mandate_id):  # noqa: ANN001, ANN201
        return {"quote_id": "qte_1", "currency": "INR", "items": [], "subtotal_minor": 100,
                "tax_minor": 0, "shipping_minor": 0, "total_minor": 100}

    def addons(self, q, m): return {"addons": []}  # noqa: ANN001, ANN201, E704

    def authorize(self, request):  # noqa: ANN001, ANN201
        return {"verdict": "STEP_UP", "decision_id": "dec_1", "mandate_id": request["mandate_id"],
                "reason_code": "INTENT_MISMATCH"}

    def order(self, quote_id, decision, recovered_from=None):  # noqa: ANN001, ANN201
        self.ordered.append(decision)
        return {"accepted": True}

    def order_and_steps(self, quote_id):  # noqa: ANN001, ANN201
        return ({"order_id": "ord_1", "state": "FULFILLED", "amount_minor": 100, "currency": "INR"},
                [{"state": "FULFILLED", "order_id": "ord_1"}])

    def accept_alternative(self, o): return None  # noqa: ANN001, ANN201, E704

    def resolve_step_up(self, decision_id, payload):  # noqa: ANN001, ANN201
        self.answers.append(payload)
        return {"verdict": "ALLOW", "decision_id": decision_id, "mandate_id": "mnd_1",
                "settlement_token": "stl_SECRET"}


def _manager(model_turns, shop, keys, **kw) -> RunManager:
    priv, _ = keys
    return RunManager(llm_factory=lambda: ScriptedModel(list(model_turns)), shop_factory=lambda: shop,
                      agent_key=priv, buyer_kwargs={"poll_s": 0.0}, **kw)


STEP_UP_TURNS = [
    lambda m: tool("get_quote", items=[{"sku": "X", "qty": 1}]),
    lambda m: tool("pay", quote_id="qte_1", payee=None, reasoning="needed"),
    lambda m: done("Paid after approval."),
]


def _until(predicate, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_step_up_waits_for_the_principal_and_relays_their_answer(keys):
    shop = StepUpShop()
    manager = _manager(STEP_UP_TURNS, shop, keys)
    run = manager.start("mnd_1", "restock")
    _until(lambda: run.status == "waiting_principal")

    signed = {"approve": True, "approval": {"decision_id": "dec_1"}, "signature": "sig-from-device"}
    answer = manager.step_up(run.run_id, signed)

    assert shop.answers == [signed], "relayed exactly as the principal signed it"
    assert "settlement_token" not in answer, "the console never handles a token"
    _until(lambda: run.status == "done")
    assert shop.ordered[0]["settlement_token"] == "stl_SECRET"


def test_a_principal_who_never_answers_is_a_no(keys):
    shop = StepUpShop()
    manager = _manager(STEP_UP_TURNS, shop, keys, step_up_wait_s=0.05)
    run = manager.start("mnd_1", "restock")
    _until(lambda: run.status == "done")
    assert shop.ordered == []
    paid = next(s for s in run.steps if s["kind"] == "tool_result" and s["tool"] == "pay")
    assert paid["detail"]["money_moved"] is False


def test_no_model_configured_refuses_rather_than_failing_later(keys):
    priv, _ = keys
    manager = RunManager(llm_factory=None, shop_factory=StepUpShop, agent_key=priv)
    with pytest.raises(RunRefused) as exc:
        manager.start("mnd_1", "restock")
    assert exc.value.status == 503


def test_the_hourly_limit_holds(keys):
    gate_open = threading.Event()
    slow = [lambda m: (gate_open.wait(5), done())[1]]
    manager = _manager(slow * 3, StepUpShop(), keys, runs_per_hour=2, runs_at_once=5)
    manager.start("mnd_1", "a")
    manager.start("mnd_1", "b")
    with pytest.raises(RunRefused) as exc:
        manager.start("mnd_1", "c")
    gate_open.set()
    assert exc.value.status == 429 and "hourly" in exc.value.detail


def test_runs_at_once_are_limited(keys):
    gate_open = threading.Event()
    slow = [lambda m: (gate_open.wait(5), done())[1]]
    manager = _manager(slow * 3, StepUpShop(), keys, runs_per_hour=10, runs_at_once=1)
    manager.start("mnd_1", "a")
    with pytest.raises(RunRefused) as exc:
        manager.start("mnd_1", "b")
    gate_open.set()
    assert exc.value.status == 429


def test_only_a_run_waiting_for_approval_can_be_answered(keys):
    manager = _manager([lambda m: done()], StepUpShop(), keys)
    run = manager.start("mnd_1", "restock")
    _until(lambda: run.status == "done")
    with pytest.raises(RunRefused) as exc:
        manager.step_up(run.run_id, {"approve": True})
    assert exc.value.status == 409
