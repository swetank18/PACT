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

from contracts.crypto import verify
from contracts.money import Paise
from contracts.reason_codes import ReasonCode
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
        #: The gate's Ed25519 public key, fetched once and cached. See `_pubkey`.
        self._gate_pubkey: str | None = None

    # ------------------------------------------------------------ headroom ---

    def _pubkey(self, *, refresh: bool = False) -> str | None:
        if refresh:
            self._gate_pubkey = None
        if self._gate_pubkey is None:
            try:
                r = self._client.get("/v1/gate/pubkey")
                r.raise_for_status()
                self._gate_pubkey = str(r.json()["public_key_b64u"])
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("gate public key unavailable: %s", exc)
                return None
        return self._gate_pubkey

    def headroom(self, mandate_id: str) -> Headroom | None:
        """
        Fetch the envelope and **verify it**, rather than trusting the transport.

        This is the claim the whole design rests on: a merchant can trust a
        signed envelope it was handed without asking anyone. The gate has always
        signed them and published its key at `/v1/gate/pubkey` — whose docstring
        reads "So the merchant can verify the headroom envelopes it is handed" —
        and nothing here ever called it. Every ceiling is re-derived server side
        at authorize, so an unverified envelope could never move money; what it
        could do is make the central claim untrue, which is the thing being sold.

        Fails closed. An envelope that does not verify is not an envelope, and
        the upsell gets nothing rather than falling back to offering everything.
        """
        try:
            r = self._client.get(f"/v1/mandates/{mandate_id}/headroom")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            envelope = Headroom.model_validate(r.json())
        except httpx.HTTPError as exc:
            # Fail closed for the upsell: no headroom means no offers, rather
            # than falling back to offering everything. An outage must not
            # silently downgrade us to the naive baseline.
            log.warning("headroom unavailable for %s: %s", mandate_id, exc)
            return None

        return envelope if self._verified(envelope, mandate_id) else None

    def _verified(self, envelope: Headroom, mandate_id: str) -> bool:
        if not envelope.signature:
            log.warning("headroom envelope for %s carries no signature", mandate_id)
            return False

        payload = envelope.model_dump()
        for refresh in (False, True):
            pubkey = self._pubkey(refresh=refresh)
            if pubkey is None:
                return False
            if verify(payload, envelope.signature, pubkey):
                return True
            # One retry against a freshly fetched key before calling it a
            # forgery. The gate generates a new key when it boots without its
            # volume — which is every restart on a plan with no disk — and a
            # cached key would then reject every envelope for the life of the
            # process, turning the growth feature off with health checks green.
            # That exact failure has happened here once already, from an empty
            # merchant VPA, and it is the reason this retries rather than
            # refusing on the first mismatch.
        log.error(
            "headroom envelope for %s failed signature verification against the "
            "gate's published key — refusing to offer against it",
            mandate_id,
        )
        return False

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
            # Fail closed — no order without a redeemed token — but say why
            # truthfully. This used to return TOKEN_INVALID, which means "that
            # token is not valid" and reads as a forgery. Under load the gate
            # simply does not answer within the timeout, and a burst of
            # TOKEN_INVALID in the audit trail sends someone hunting an attacker
            # who does not exist. Found by scripts/load.py at 32 concurrent.
            #
            # Deliberately not retried. Redemption is single use and not
            # idempotent: a retry after a timeout where the gate *did* redeem is
            # exactly where a double spend gets introduced. Refusing and letting
            # the buyer retry the whole purchase is the safe direction.
            log.error("token redemption failed: %s", exc)
            return False, str(ReasonCode.GATE_UNAVAILABLE), None

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
