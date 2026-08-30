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

from contracts.crypto import verify
from contracts.reason_codes import ReasonCode
from contracts.schemas import Mandate, utcnow
from core.db import Database


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
            mandate=Mandate.model_validate(json.loads(row["body_json"])),
            signature_ok=bool(row["signature_ok"]),
            revoked=bool(row["revoked"]),
        )

    def revoke(self, mandate_id: str) -> bool:
        with self.db.immediate_tx() as conn:
            cur = conn.execute(
                "UPDATE mandates SET revoked = 1 WHERE mandate_id = ?", (mandate_id,)
            )
        return cur.rowcount > 0
