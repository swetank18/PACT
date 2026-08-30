"""
The buyer agent. Holds a mandate, discovers the merchant, buys.

**One agent, four flags, no forked scripts.** `--gate` and `--upsell` define the
experiment arms. Forking the agent per arm is how you end up measuring two
different programs and calling it a comparison.

    --gate=off      no authority check at all. Arm B.
    --gate=naive    a hard client-side spend cap. Arm C.
    --gate=pact     the full nine checks. Arm D.

    --upsell=off       never take an addon
    --upsell=naive     take whatever is offered, blind. Arm C.
    --upsell=headroom  the merchant only offers what fits. Arm D.

Steps 8, 9 and 10 of the flow are where the marks are, and where most
implementations stop: on STEP_UP surface to the human, on BLOCK read the reason
code and **repair the order**, on rollback evaluate the alternative. An agent
that reads a structured refusal and fixes its own request is materially better
than one that crashes, and the repair loop is what turns arm D's step-ups into
recovered revenue rather than lost sales.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx

from contracts.crypto import generate_keypair, sign
from contracts.ids import new_id
from contracts.money import Paise

log = logging.getLogger("pact.buyer")

GateMode = Literal["off", "naive", "pact"]
UpsellMode = Literal["off", "naive", "headroom"]


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reasoning(quote: dict[str, Any], intent: str) -> str:
    """What the agent believes it is doing. The auditor reads this, so it has to
    be the real reason rather than a placeholder."""
    basket = ", ".join(f"{i['qty']}x {i['name']}" for i in quote["items"])
    return f"Buying {basket} toward: {intent}"


@dataclass
class SessionResult:
    """
    Everything the harness needs, computed from what actually happened.

    Deliberately verbose: Lane B's metrics come from the engine's decision log
    and the merchant's saga table, and this record is what lets the harness
    cross-check itself against them. If the two disagree, the engine is right.
    """

    sim_id: str
    persona: str
    arm: str
    seed: int
    completed: bool = False
    gmv_paise: Paise = 0
    #: Money the merchant took and then lost: fraud, hallucinated prices, or a
    #: purchase outside what the human actually authorised. Arm B's number.
    loss_paise: Paise = 0
    refunded_paise: Paise = 0
    order_ids: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    upsell_offered: int = 0
    upsell_accepted: int = 0
    upsell_rejected_by_gate: int = 0
    step_ups: int = 0
    step_ups_recovered: int = 0
    #: A legitimate purchase the arm refused outright. Arm C's number.
    false_blocks: int = 0
    saga_recoveries: int = 0
    repairs: int = 0
    #: True when this session included an adversarial attempt. Tracked so the
    #: hostile share of the population is reportable rather than assumed.
    hostile: bool = False
    transcript: list[str] = field(default_factory=list)
    error: str | None = None

    def say(self, line: str) -> None:
        self.transcript.append(line)


@dataclass
class Mandate:
    """The signed delegation, and the key that stays on the buyer's device."""

    body: dict[str, Any]
    private_key: Any

    @property
    def id(self) -> str:
        return self.body["mandate_id"]

    @property
    def constraints(self) -> dict[str, Any]:
        return self.body["constraints"]


class BuyerAgent:
    def __init__(
        self,
        *,
        gate_url: str = "http://localhost:8000",
        merchant_url: str = "http://localhost:8100",
        gate_mode: GateMode = "pact",
        upsell_mode: UpsellMode = "headroom",
        seed: int = 0,
        timeout: float = 15.0,
    ) -> None:
        self.gate_url = gate_url
        self.merchant_url = merchant_url
        self.gate_mode = gate_mode
        self.upsell_mode = upsell_mode
        # Temperature 0 equivalent: every stochastic choice comes from this
        # generator, so a seed reproduces a session exactly. Without it, a
        # variance run tells you nothing.
        self.rng = random.Random(seed)
        self.seed = seed
        self.http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------ mandate ---

    def issue_mandate(self, persona: dict[str, Any], merchant_vpa: str) -> Mandate:
        """
        The human signs on device. The agent gets the mandate, never the key.

        Arm B has no gate, but it still carries a mandate — otherwise the arms
        would differ in two ways at once and the comparison would be worthless.
        """
        priv, pub = generate_keypair()
        now = datetime.now(timezone.utc)
        m = persona["mandate"]
        body = {
            "v": 1,
            "mandate_id": new_id("mnd"),
            "delegator": {"vpa": persona.get("vpa", "buyer@okaxis"), "pubkey": pub},
            "delegate": {"agent_id": "buyer_agent_v1", "pubkey": pub},
            "intent": persona["goal"],
            "issued_at": iso(now),
            "constraints": {
                "max_per_txn_paise": m["max_per_txn_paise"],
                "max_total_paise": m["max_total_paise"],
                "max_count": m["max_count"],
                "merchant_allowlist": [merchant_vpa],
                "category_allowlist": list(m["categories"]),
                "valid_from": iso(now - timedelta(minutes=1)),
                "valid_until": iso(now + timedelta(hours=m.get("valid_hours", 24))),
            },
        }
        body["signature"] = sign(body, priv)
        self.http.post(f"{self.gate_url}/v1/mandates", json=body)
        return Mandate(body=body, private_key=priv)

    # ---------------------------------------------------------- discovery ---

    def discover(self) -> dict[str, Any]:
        """Cold, from the manifest. No partnership, no hardcoded endpoints."""
        r = self.http.get(f"{self.merchant_url}/.well-known/agent-commerce.json")
        r.raise_for_status()
        return r.json()

    def search(self, query: str, category: str | None = None) -> list[dict[str, Any]]:
        params = {"q": query}
        if category:
            params["category"] = category
        r = self.http.get(f"{self.merchant_url}/v1/catalog", params=params)
        r.raise_for_status()
        return r.json()["products"]

    def quote(self, items: list[dict[str, Any]], mandate_id: str | None) -> dict[str, Any]:
        r = self.http.post(
            f"{self.merchant_url}/v1/quote",
            json={"items": items, "mandate_id": mandate_id},
        )
        r.raise_for_status()
        return r.json()

    def suggest_addons(self, quote_id: str, mandate_id: str) -> dict[str, Any]:
        r = self.http.post(
            f"{self.merchant_url}/v1/suggest_addons",
            json={"quote_id": quote_id, "mandate_id": mandate_id},
        )
        if r.status_code != 200:
            return {"addons": [], "filtered_out": 0}
        return r.json()

    def headroom(self, mandate_id: str) -> dict[str, Any] | None:
        r = self.http.get(f"{self.gate_url}/v1/mandates/{mandate_id}/headroom")
        return r.json() if r.status_code == 200 else None

    # ---------------------------------------------------------- authorize ---

    def authorize(
        self,
        mandate: Mandate,
        quote: dict[str, Any],
        merchant_vpa: str,
        *,
        amount_override: Paise | None = None,
        payee_override: str | None = None,
        nonce_override: str | None = None,
        excerpt: str | None = None,
        sign_with: Any = None,
    ) -> dict[str, Any]:
        """
        Build a signed authorize request and ask the gate.

        The overrides exist for the attack suite: an invented amount, a
        lookalike payee, a reused nonce, hostile page text. They are not used on
        the honest path, and the honest path is the default.
        """
        req = {
            "mandate_id": mandate.id,
            "quote_id": quote["quote_id"],
            "amount_paise": amount_override if amount_override is not None else quote["total_paise"],
            "payee_vpa": payee_override or merchant_vpa,
            "nonce": nonce_override or new_id("dec"),
            "issued_at": iso(datetime.now(timezone.utc)),
            # Populated honestly. Stub these and the intent auditor has nothing
            # to work with, so the injection attack would pass for the wrong
            # reason and the ablation matrix would be a lie.
            "context": {
                "page_excerpt": excerpt
                if excerpt is not None
                else " | ".join(i["name"] for i in quote["items"]),
                "agent_reasoning": _reasoning(quote, mandate.body["intent"]),
            },
        }
        req["signature"] = sign(req, sign_with or mandate.private_key)
        r = self.http.post(f"{self.gate_url}/v1/authorize", json=req)
        r.raise_for_status()
        return r.json()

    def create_order(
        self, quote_id: str, decision: dict[str, Any], recovered_from: str | None = None
    ) -> dict[str, Any]:
        body = {
            "quote_id": quote_id,
            "decision_id": decision["decision_id"],
            "settlement_token": decision.get("settlement_token") or "",
        }
        if recovered_from:
            body["recovered_from"] = recovered_from
        r = self.http.post(f"{self.merchant_url}/v1/orders", json=body)
        r.raise_for_status()
        return r.json()

    def order_for_quote(self, quote_id: str) -> dict[str, Any] | None:
        r = self.http.get(f"{self.merchant_url}/v1/orders", params={"limit": 50})
        if r.status_code != 200:
            return None
        for o in r.json()["orders"]:
            if o["quote_id"] == quote_id:
                return o
        return None

    def resolve_step_up(self, decision: dict[str, Any], mandate: Mandate, approve: bool) -> dict:
        """
        Surface to the human and wait. In simulation the "human" is the
        persona's own tolerance, decided by the seeded generator.

        Approving signs the specific decision on the device, so the gate can
        prove the approval did not come from the agent.
        """
        payload: dict[str, Any] = {"approve": approve}
        if approve:
            approval = {
                "decision_id": decision["decision_id"],
                "mandate_id": decision["mandate_id"],
                "amount_paise": decision["amount_paise"],
                "payee_vpa": decision["payee_vpa"],
                "approved_at": iso(datetime.now(timezone.utc)),
            }
            payload["approval"] = approval
            payload["signature"] = sign(approval, mandate.private_key)
        r = self.http.post(
            f"{self.gate_url}/v1/decisions/{decision['decision_id']}/step_up", json=payload
        )
        return r.json() if r.status_code == 200 else {"verdict": "BLOCK"}

    def accept_alternative(self, order_id: str) -> dict[str, Any] | None:
        r = self.http.post(f"{self.merchant_url}/v1/orders/{order_id}/accept_alternative", json={})
        return r.json() if r.status_code == 200 else None
