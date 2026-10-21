"""
Mandate issuance, storage, revocation and signature verification.

The signature is verified **once, at registration**, and the result is stored.
The gate re-asserts the stored flag on every authorize rather than re-verifying,
because an Ed25519 verify per request would put a fixed cost on the cheapest
path for no added safety: the mandate is immutable once stored, so a signature
that verified at registration verifies forever.

What is *not* cached is the request signature. That is verified on every call,
because it is different every call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from contracts.crypto import verify
from contracts.legacy import upgrade_keys
from contracts.reason_codes import ReasonCode
from contracts.schemas import Mandate, parse_rfc3339, utcnow
from core.db import Database


#: How old a principal's signed request may be. Long enough for a slow round
#: trip through a phone; short enough that a captured one is soon useless.
PRINCIPAL_MAX_AGE_SECONDS = 120


def _with_currency(body: dict) -> dict:
    """
    Every mandate stored before mandates carried a currency was signed in
    rupees: no profile priced in anything else until 2026-10-21. Its signature
    was verified at registration over the body as it was, so this adds what was
    true of it rather than changing what was signed.
    """
    body.get("constraints", {}).setdefault("currency", "INR")
    return body


@dataclass(frozen=True, slots=True)
class StoredMandate:
    mandate: Mandate
    signature_ok: bool
    revoked: bool


class MandateStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register(self, mandate: Mandate) -> tuple[bool, ReasonCode]:
        """
        Verify and store. Returns whether the mandate was accepted.

        A mandate whose signature does not verify is still stored, with
        `signature_ok = 0`. Storing it means the audit trail can show the
        rejected attempt and the gate can answer MANDATE_SIG_INVALID rather
        than MANDATE_NOT_FOUND, which is a materially more useful refusal for
        an agent trying to repair its own request.
        """
        if not mandate.signature:
            signature_ok = False
        else:
            signature_ok = verify(
                mandate.model_dump(exclude_none=False),
                mandate.signature,
                mandate.delegator.pubkey,
            )

        body = mandate.model_dump()
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO mandates
                    (mandate_id, body_json, delegator_pub, delegate_pub,
                     signature, signature_ok, revoked, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(mandate_id) DO UPDATE SET
                    body_json     = excluded.body_json,
                    delegator_pub = excluded.delegator_pub,
                    delegate_pub  = excluded.delegate_pub,
                    signature     = excluded.signature,
                    signature_ok  = excluded.signature_ok
                """,
                (
                    mandate.mandate_id,
                    json.dumps(body, separators=(",", ":")),
                    mandate.delegator.pubkey,
                    mandate.delegate.pubkey,
                    mandate.signature or "",
                    int(signature_ok),
                    utcnow(),
                ),
            )

        return signature_ok, (
            ReasonCode.OK if signature_ok else ReasonCode.MANDATE_SIG_INVALID
        )

    def get(self, mandate_id: str) -> StoredMandate | None:
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT body_json, signature_ok, revoked FROM mandates WHERE mandate_id = ?",
                (mandate_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredMandate(
            mandate=Mandate.model_validate(_with_currency(upgrade_keys(json.loads(row["body_json"])))),
            signature_ok=bool(row["signature_ok"]),
            revoked=bool(row["revoked"]),
        )

    def verify_principal(
        self,
        mandate_id: str,
        *,
        action: str,
        payload: dict[str, Any],
        signature: str,
        max_age_seconds: int = PRINCIPAL_MAX_AGE_SECONDS,
    ) -> tuple[bool, ReasonCode]:
        """
        Whether a request really comes from the person who signed this mandate.

        For the things only the principal may do outside a payment — connect a
        wallet to the mandate, disconnect it — the merchant forwards the signed
        request here and gets a yes or no. It never learns the delegator's key:
        the headroom envelope is built so the merchant cannot see who the buyer
        is, and handing out the key would undo that.

        The payload names the action and the mandate, so a signature for one
        cannot be replayed as the other, and carries `issued_at`, so an old one
        cannot be replayed at all past a short window. Inside the window a replay
        repeats an idempotent act, which is why there is no nonce.
        """
        stored = self.get(mandate_id)
        if stored is None:
            return False, ReasonCode.MANDATE_NOT_FOUND
        if not stored.signature_ok:
            return False, ReasonCode.MANDATE_SIG_INVALID
        if payload.get("mandate_id") != mandate_id or payload.get("action") != action:
            return False, ReasonCode.REQUEST_SIG_INVALID
        try:
            issued = parse_rfc3339(str(payload.get("issued_at", "")))
        except ValueError:
            return False, ReasonCode.REQUEST_STALE
        age = (datetime.now(timezone.utc) - issued).total_seconds()
        if age > max_age_seconds or age < -max_age_seconds:
            return False, ReasonCode.REQUEST_STALE
        if not verify(payload, signature, stored.mandate.delegator.pubkey):
            return False, ReasonCode.REQUEST_SIG_INVALID
        return True, ReasonCode.OK

    def revoke(self, mandate_id: str) -> bool:
        with self.db.immediate_tx() as conn:
            cur = conn.execute(
                "UPDATE mandates SET revoked = 1 WHERE mandate_id = ?", (mandate_id,)
            )
        return cur.rowcount > 0
