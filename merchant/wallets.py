"""
Connecting the wallet a mandate pays from.

The one moment in the PayPal flow with a person in it. The principal signs a
mandate that bounds what the agent may spend; here they connect the PayPal
wallet it spends *from* — they approve once, at PayPal, and from then on the
agent's purchases are charged to that wallet with nobody at an approve button.

Three rules, each a thing this module refuses to do:

**Only the principal.** Connecting and disconnecting are signed with the key
that signed the mandate, and the gate checks the signature. The merchant never
holds that key and never learns it.

**The wallet's token never leaves the server.** No response carries the vault
id, no log line prints it, and the agent cannot ask for it. What the console
shows is the payer's email, masked.

**Disconnect means disconnected.** The token is deleted at PayPal, and a mandate
whose wallet was disconnected cannot fall back to a default wallet: that would
turn "disconnect" into "pay from someone else's".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from contracts.ids import new_id
from contracts.schemas import utcnow
from core.db import Database
from merchant.gate_client import GateClient

log = logging.getLogger("pact.merchant.wallets")

CONNECT = "paypal.vault.connect"
DISCONNECT = "paypal.vault.disconnect"


class WalletError(RuntimeError):
    """A request about a wallet that cannot be honoured, with an HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Wallet:
    mandate_id: str
    rail: str
    ref: str
    setup_token_id: str | None
    vault_id: str | None
    status: str
    payer_masked: str | None
    detail: str | None

    def public(self) -> dict[str, Any]:
        """Everything the console may see. The vault id is not in it."""
        return {"mandate_id": self.mandate_id, "rail": self.rail, "status": self.status,
                "payer": self.payer_masked, "detail": self.detail}


def mask_email(email: str | None) -> str | None:
    """`judge-buyer@personal.example.com` -> `j***@personal.example.com`."""
    if not email or "@" not in email:
        return None
    name, domain = email.split("@", 1)
    return f"{name[:1]}***@{domain}"


class WalletStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _row(self, row) -> Wallet | None:  # noqa: ANN001
        if row is None:
            return None
        return Wallet(row["mandate_id"], row["rail"], row["ref"], row["setup_token_id"],
                      row["vault_id"], row["status"], row["payer_masked"], row["detail"])

    def get(self, mandate_id: str) -> Wallet | None:
        with self.db.read_tx() as conn:
            return self._row(conn.execute(
                "SELECT * FROM wallets WHERE mandate_id = ?", (mandate_id,)).fetchone())

    def by_ref(self, ref: str) -> Wallet | None:
        with self.db.read_tx() as conn:
            return self._row(conn.execute("SELECT * FROM wallets WHERE ref = ?", (ref,)).fetchone())

    def start(self, mandate_id: str, rail: str, ref: str, setup_token_id: str) -> None:
        now = utcnow()
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO wallets (mandate_id, rail, ref, setup_token_id, vault_id, status,
                                     payer_masked, detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, 'PENDING', NULL, NULL, ?, ?)
                ON CONFLICT(mandate_id) DO UPDATE SET
                    ref = excluded.ref, setup_token_id = excluded.setup_token_id,
                    vault_id = NULL, status = 'PENDING', payer_masked = NULL,
                    detail = NULL, updated_at = excluded.updated_at
                """,
                (mandate_id, rail, ref, setup_token_id, now, now),
            )

    def update(self, mandate_id: str, **fields: Any) -> None:
        allowed = {"vault_id", "status", "payer_masked", "detail"}
        assert set(fields) <= allowed, set(fields) - allowed
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.db.immediate_tx() as conn:
            conn.execute(f"UPDATE wallets SET {sets}, updated_at = ? WHERE mandate_id = ?",
                         (*fields.values(), utcnow(), mandate_id))

    def for_order(self, order_id: str) -> Wallet | None:
        """The wallet the mandate behind this order pays from, if it has one."""
        with self.db.read_tx() as conn:
            return self._row(conn.execute(
                "SELECT w.* FROM orders o JOIN wallets w ON w.mandate_id = o.mandate_id "
                "WHERE o.order_id = ?", (order_id,)).fetchone())


def vault_id_resolver(wallets: WalletStore, default_vault_id: str):  # noqa: ANN201
    """
    Which vaulted wallet pays for an order: the one its mandate connected.

    A mandate that never connected one falls back to `default_vault_id` — the
    single demo payer from `PAYPAL_VAULT_ID`, so the agent can run before anyone
    has vaulted. A mandate whose wallet is pending, failed or disconnected gets
    nothing, never the default: the principal said no, and paying from a
    different wallet would not be honouring it.
    """
    def resolve(order_id: str) -> str:
        wallet = wallets.for_order(order_id)
        if wallet is None:
            return default_vault_id
        return (wallet.vault_id or "") if wallet.status == "VAULTED" else ""
    return resolve


class VaultService:
    def __init__(self, client, wallets: WalletStore, gate: GateClient) -> None:  # noqa: ANN001
        #: A `rails.paypal.client.PayPalClient`. Typed loosely: this module is
        #: what the merchant knows about wallets, not about PayPal's wire format.
        self.client = client
        self.wallets = wallets
        self.gate = gate

    def _principal(self, mandate_id: str, action: str, payload: Any, signature: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(signature, str) or not signature:
            raise WalletError(403, "this needs the principal's signed request")
        ok, code = self.gate.verify_principal(mandate_id, action=action, payload=payload,
                                              signature=signature)
        if not ok:
            raise WalletError(403, f"the gate did not confirm the principal: {code}")

    def connect(self, mandate_id: str, payload: Any, signature: Any, *, base_url: str) -> dict:
        """
        Start connecting. Returns the PayPal page the principal approves at.
        Nothing is bound until they do.
        """
        self._principal(mandate_id, CONNECT, payload, signature)
        headroom = self.gate.headroom(mandate_id)
        if headroom is None:
            raise WalletError(404, "no such mandate")
        if not headroom.merchant_in_scope:
            raise WalletError(409, "this mandate is not one this merchant can sell against")

        ref = new_id("wlt")
        back = f"{base_url.rstrip('/')}/v1/paypal/vault/return?ref={ref}"
        setup = self.client.create_setup_token(back, f"{back}&cancelled=1", idem_key=ref)
        approve = next((link["href"] for link in setup.get("links", [])
                        if link.get("rel") == "approve"), None)
        if not approve:
            raise WalletError(502, "PayPal returned no page to approve at")
        self.wallets.start(mandate_id, "paypal", ref, str(setup["id"]))
        return {"mandate_id": mandate_id, "status": "PENDING", "approve_url": approve}

    def refresh(self, mandate_id: str) -> dict:
        """
        Where the connection stands. A pending one is checked at PayPal, and an
        approved setup token is turned into the vaulted wallet here — so the
        console's poll completes it even if the browser never came back.
        """
        wallet = self.wallets.get(mandate_id)
        if wallet is None:
            raise WalletError(404, "no wallet has been connected to this mandate")
        if wallet.status == "PENDING" and wallet.setup_token_id:
            setup = self.client.get_setup_token(wallet.setup_token_id)
            if setup.get("status") == "APPROVED":
                token = self.client.create_payment_token(wallet.setup_token_id, idem_key=wallet.ref)
                email = ((token.get("payment_source") or {}).get("paypal") or {}).get("email_address")
                self.wallets.update(mandate_id, vault_id=str(token["id"]), status="VAULTED",
                                    payer_masked=mask_email(email))
                log.info("wallet connected for mandate %s", mandate_id)
        return self.wallets.get(mandate_id).public()  # type: ignore[union-attr]

    def returned(self, ref: str, *, cancelled: bool) -> dict:
        """PayPal sent the browser back. Cancelled, or go and finish it."""
        wallet = self.wallets.by_ref(ref)
        if wallet is None:
            raise WalletError(404, "this link does not belong to a connection in progress")
        if cancelled and wallet.status == "PENDING":
            self.wallets.update(wallet.mandate_id, status="FAILED",
                                detail="cancelled at PayPal")
            return self.wallets.get(wallet.mandate_id).public()  # type: ignore[union-attr]
        return self.refresh(wallet.mandate_id)

    def disconnect(self, mandate_id: str, payload: Any, signature: Any) -> dict:
        """
        Delete the wallet's token at PayPal and mark the mandate disconnected.
        After this the mandate cannot pay from anything until it is connected
        again.
        """
        self._principal(mandate_id, DISCONNECT, payload, signature)
        wallet = self.wallets.get(mandate_id)
        if wallet is None:
            raise WalletError(404, "no wallet has been connected to this mandate")
        if wallet.vault_id:
            self.client.delete_payment_token(wallet.vault_id)
        self.wallets.update(mandate_id, vault_id=None, status="DISCONNECTED",
                            detail="disconnected by the principal; deleted at PayPal")
        log.info("wallet disconnected for mandate %s", mandate_id)
        return self.wallets.get(mandate_id).public()  # type: ignore[union-attr]
