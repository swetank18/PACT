"""
Connecting the PayPal wallet a mandate pays from, and disconnecting it.

What is being shown: only the principal can do either, the gate checks that
without giving the merchant their key, the wallet's token never appears in
anything the console or the agent can read, and a disconnected wallet cannot be
paid from — not even by falling back to the demo wallet.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contracts.crypto import generate_keypair, sign
from contracts.reason_codes import ReasonCode
from contracts.schemas import QuoteItemRequest
from merchant.wallets import CONNECT, DISCONNECT, VaultService, WalletError, WalletStore, vault_id_resolver
from rails.paypal.adapter import PayPalAdapter
from rails.paypal.client import Credentials, PayPalClient
from tests.fake_paypal import CLIENT_ID, CLIENT_SECRET, VAULT_ID, WEBHOOK_ID, FakePayPal

REPO = Path(__file__).resolve().parents[1]
BASE = "https://pact.example/api/merchant"


def _now(offset_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("rails.paypal.client.time.sleep", lambda _s: None)


@pytest.fixture
def api() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def client(api, db) -> PayPalClient:
    creds = Credentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                        webhook_id=WEBHOOK_ID, vault_id=VAULT_ID)
    return PayPalClient(db, creds, transport=api.transport)


@pytest.fixture
def wallets(db) -> WalletStore:
    return WalletStore(db)


@pytest.fixture
def rail(client, wallets):
    """The merchant's saga pays from the wallet each mandate connected, with
    the demo wallet as the default for mandates that never connected one."""
    return PayPalAdapter(client, vault_id_for=vault_id_resolver(wallets, VAULT_ID))


@pytest.fixture
def vault(client, wallets, merchant) -> VaultService:
    return VaultService(client, wallets, merchant.gate_client)


@pytest.fixture
def principal(keys, make_mandate):
    priv, _ = keys
    mandate = make_mandate()

    def signed(action: str, *, at: str | None = None, key=None, mandate_id=None):
        payload = {"action": action, "mandate_id": mandate_id or mandate.mandate_id,
                   "issued_at": at or _now()}
        return payload, sign(payload, key or priv)

    return mandate, signed


def _connect(vault, api, mandate, signed):
    payload, sig = signed(CONNECT)
    started = vault.connect(mandate.mandate_id, payload, sig, base_url=BASE)
    (setup_id,) = api.setup_tokens
    return started, setup_id


def _buy(quotes, authorize, merchant, mandate):
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    assert decision.verdict == "ALLOW"
    return merchant.saga.run(quote=q, mandate_id=mandate.mandate_id,
                             decision_id=decision.decision_id)


# ------------------------------------------------- the gate's yes or no ---


def test_the_gate_confirms_the_principal_without_handing_out_their_key(gate, principal):
    mandate, signed = principal
    payload, sig = signed(CONNECT)
    assert gate.mandates.verify_principal(mandate.mandate_id, action=CONNECT,
                                          payload=payload, signature=sig) == (True, ReasonCode.OK)


@pytest.mark.parametrize(("case", "code"), [
    ("other action", ReasonCode.REQUEST_SIG_INVALID),
    ("other key", ReasonCode.REQUEST_SIG_INVALID),
    ("stale", ReasonCode.REQUEST_STALE),
    ("other mandate", ReasonCode.REQUEST_SIG_INVALID),
])
def test_anything_but_a_fresh_request_signed_for_this_is_refused(gate, principal, case, code):
    mandate, signed = principal
    stranger, _ = generate_keypair()
    payload, sig = {
        "other action": lambda: signed(DISCONNECT),
        "other key": lambda: signed(CONNECT, key=stranger),
        "stale": lambda: signed(CONNECT, at=_now(-600)),
        "other mandate": lambda: signed(CONNECT, mandate_id="mnd_SOMEONE_ELSE"),
    }[case]()
    assert gate.mandates.verify_principal(mandate.mandate_id, action=CONNECT,
                                          payload=payload, signature=sig) == (False, code)


def test_an_unknown_mandate_is_not_found(gate, principal):
    _, signed = principal
    payload, sig = signed(CONNECT, mandate_id="mnd_NOPE")
    assert gate.mandates.verify_principal("mnd_NOPE", action=CONNECT, payload=payload,
                                          signature=sig)[1] == ReasonCode.MANDATE_NOT_FOUND


# --------------------------------------------------------------- connect ---


def test_connecting_needs_the_principal(vault, principal, api):
    mandate, signed = principal
    stranger, _ = generate_keypair()
    with pytest.raises(WalletError) as unsigned:
        vault.connect(mandate.mandate_id, None, None, base_url=BASE)
    payload, sig = signed(CONNECT, key=stranger)
    with pytest.raises(WalletError) as forged:
        vault.connect(mandate.mandate_id, payload, sig, base_url=BASE)
    assert unsigned.value.status == forged.value.status == 403
    assert api.setup_tokens == {}, "PayPal was never asked"


def test_a_connected_wallet_pays_for_that_mandates_purchases(
    vault, api, wallets, principal, quotes, authorize, merchant
):
    mandate, signed = principal
    started, setup_id = _connect(vault, api, mandate, signed)
    assert started["approve_url"].startswith("https://www.sandbox.paypal.com/")
    assert vault.refresh(mandate.mandate_id)["status"] == "PENDING", "nothing bound before approval"

    api.approve(setup_id)
    connected = vault.refresh(mandate.mandate_id)
    assert connected["status"] == "VAULTED"
    assert connected["payer"] == "j***@personal.example.com"

    result = _buy(quotes, authorize, merchant, mandate)
    assert result.final_state == "FULFILLED"
    charged = [s for s in api.seen if s[1] == "/v2/checkout/orders"][-1][2]
    assert charged["payment_source"]["paypal"]["vault_id"] == wallets.get(mandate.mandate_id).vault_id
    assert charged["payment_source"]["paypal"]["vault_id"] != VAULT_ID


def test_the_return_page_finishes_the_connection(vault, api, wallets, principal):
    mandate, signed = principal
    _, setup_id = _connect(vault, api, mandate, signed)
    api.approve(setup_id)
    ref = wallets.get(mandate.mandate_id).ref
    assert vault.returned(ref, cancelled=False)["status"] == "VAULTED"


def test_cancelling_at_paypal_connects_nothing(vault, api, wallets, principal, quotes,
                                              authorize, merchant):
    mandate, signed = principal
    _connect(vault, api, mandate, signed)
    ref = wallets.get(mandate.mandate_id).ref
    assert vault.returned(ref, cancelled=True)["status"] == "FAILED"
    # And a mandate whose connection failed does not quietly pay from the demo wallet.
    result = _buy(quotes, authorize, merchant, mandate)
    assert result.final_state == "ROLLED_BACK"


def test_a_mandate_in_another_currency_cannot_connect(vault, make_mandate, keys):
    priv, _ = keys
    mandate = make_mandate(constraints={"currency": "USD"})
    payload = {"action": CONNECT, "mandate_id": mandate.mandate_id, "issued_at": _now()}
    with pytest.raises(WalletError) as exc:
        vault.connect(mandate.mandate_id, payload, sign(payload, priv), base_url=BASE)
    assert exc.value.status == 409


# ------------------------------------------------------------ disconnect ---


def test_disconnecting_deletes_at_paypal_and_the_mandate_pays_from_nothing(
    vault, api, wallets, principal, quotes, authorize, merchant
):
    """
    The demo wallet is configured as a default here on purpose. A disconnected
    mandate must not reach it: "disconnect" would otherwise mean "pay from
    someone else's".
    """
    mandate, signed = principal
    _, setup_id = _connect(vault, api, mandate, signed)
    api.approve(setup_id)
    vault.refresh(mandate.mandate_id)
    vault_id = wallets.get(mandate.mandate_id).vault_id

    payload, sig = signed(DISCONNECT)
    assert vault.disconnect(mandate.mandate_id, payload, sig)["status"] == "DISCONNECTED"
    assert vault_id not in api.vaulted, "deleted at PayPal, not only here"

    result = _buy(quotes, authorize, merchant, mandate)
    assert result.final_state == "ROLLED_BACK"
    last = merchant.audit.list_steps(result.order_id)[-1]
    assert last["reason_code"] == ReasonCode.RAIL_AUTHORIZATION_FAILED.value


def test_disconnecting_needs_the_principal(vault, api, principal):
    mandate, signed = principal
    _, setup_id = _connect(vault, api, mandate, signed)
    api.approve(setup_id)
    vault.refresh(mandate.mandate_id)
    payload, sig = signed(CONNECT)  # signed, but for the other action
    with pytest.raises(WalletError) as exc:
        vault.disconnect(mandate.mandate_id, payload, sig)
    assert exc.value.status == 403


def test_a_mandate_that_never_connected_uses_the_demo_wallet(
    api, principal, quotes, authorize, merchant
):
    mandate, _ = principal
    assert _buy(quotes, authorize, merchant, mandate).final_state == "FULFILLED"
    charged = [s for s in api.seen if s[1] == "/v2/checkout/orders"][-1][2]
    assert charged["payment_source"]["paypal"]["vault_id"] == VAULT_ID


def test_the_vault_id_appears_in_nothing_the_console_can_read(vault, api, wallets, principal):
    mandate, signed = principal
    seen = [_connect(vault, api, mandate, signed)[0]]
    api.approve(next(iter(api.setup_tokens)))
    seen.append(vault.refresh(mandate.mandate_id))
    vault_id = wallets.get(mandate.mandate_id).vault_id
    seen.append(vault.returned(wallets.get(mandate.mandate_id).ref, cancelled=False))
    payload, sig = signed(DISCONNECT)
    seen.append(vault.disconnect(mandate.mandate_id, payload, sig))

    assert vault_id and vault_id not in json.dumps(seen)


# ------------------------------------------------------------------ http ---


def test_a_merchant_on_another_rail_has_no_wallet_to_connect():
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PACT_RAIL")}
    env.update(PACT_PROFILE="razorpay-track01", PACT_DB_URL="sqlite:///:memory:")
    script = (
        "from fastapi.testclient import TestClient\n"
        "import merchant.app as m\n"
        "r = TestClient(m.app).post('/v1/paypal/vault', json={'mandate_id': 'mnd_x'})\n"
        "print(r.status_code)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], env=env, cwd=REPO,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-1500:]
    assert done.stdout.strip().splitlines()[-1] == "409"
