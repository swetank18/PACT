"""
How the merchant talks to the gate.

Two implementations behind one shape. The merchant is a separate service on
8100 and the gate owns the ledger on 8000, so HTTP is the real path. In-process
exists because the test suite should not need two servers to assert that a
rollback releases budget.

Both are exercised: `tests/` uses the in-process one, the running system uses
HTTP.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

from contracts.money import Paise
from contracts.schemas import Headroom

log = logging.getLogger("pact.merchant.gate")

GATE_URL = os.environ.get("PACT_GATE_URL", "http://localhost:8000")


class GateClient(Protocol):
    def headroom(self, mandate_id: str) -> Headroom | None: ...
    def redeem(self, token: str, amount_paise: Paise) -> tuple[bool, str, str | None]: ...
    def commit(self, decision_id: str) -> bool: ...
    def release(self, decision_id: str) -> Paise: ...


class HttpGateClient(GateClient):
    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self._client = httpx.Client(base_url=base_url or GATE_URL, timeout=timeout)

    def headroom(self, mandate_id: str) -> Headroom | None:
        try:
            r = self._client.get(f"/v1/mandates/{mandate_id}/headroom")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return Headroom.model_validate(r.json())
        except httpx.HTTPError as exc:
            # Fail closed for the upsell: no headroom means no offers, rather
            # than falling back to offering everything. An outage must not
            # silently downgrade us to the naive baseline.
            log.warning("headroom unavailable for %s: %s", mandate_id, exc)
            return None

    def redeem(self, token: str, amount_paise: Paise) -> tuple[bool, str, str | None]:
        try:
            r = self._client.post(
                "/v1/settlement/redeem",
                json={"settlement_token": token, "amount_paise": amount_paise},
            )
            r.raise_for_status()
            body = r.json()
            return bool(body.get("ok")), str(body.get("reason_code")), body.get("decision_id")
        except httpx.HTTPError as exc:
            log.error("token redemption failed: %s", exc)
            return False, "TOKEN_INVALID", None

    def commit(self, decision_id: str) -> bool:
        try:
            r = self._client.post("/v1/settlement/commit", json={"decision_id": decision_id})
            r.raise_for_status()
            return bool(r.json().get("ok"))
        except httpx.HTTPError as exc:
            log.error("commit failed for %s: %s", decision_id, exc)
            return False

    def release(self, decision_id: str) -> Paise:
        try:
            r = self._client.post("/v1/settlement/release", json={"decision_id": decision_id})
            r.raise_for_status()
            return int(r.json().get("released_paise", 0))
        except httpx.HTTPError as exc:
            # A release that does not happen is budget the buyer never gets
            # back. Loud, and the saga treats it as a failed compensation.
            log.error("release failed for %s: %s", decision_id, exc)
            raise


class InProcessGateClient(GateClient):
    """Used by the tests, and by the single-process dev runner."""

    def __init__(self, gate, headroom_service) -> None:  # noqa: ANN001
        self.gate = gate
        self.headroom_service = headroom_service

    def headroom(self, mandate_id: str) -> Headroom | None:
        return self.headroom_service.for_mandate(mandate_id)

    def redeem(self, token: str, amount_paise: Paise) -> tuple[bool, str, str | None]:
        ok, code, decision_id = self.gate.redeem_token(token, amount_paise=amount_paise)
        return ok, str(code), decision_id

    def commit(self, decision_id: str) -> bool:
        return self.gate.ledger.commit(decision_id)

    def release(self, decision_id: str) -> Paise:
        return self.gate.ledger.release(decision_id)
