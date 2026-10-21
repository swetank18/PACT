"""
The principal check over HTTP, which is how a deployed merchant asks it.

`tests/test_wallets.py` drives the in-process gate client. The merchant in a
container talks to the gate over HTTP, so the endpoint and `HttpGateClient`
are exercised here too — including the case that matters most: a gate that
does not answer is a no, never a yes.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

# The gate app builds its service at import time; see tests/test_contention.py.
_TMP = tempfile.mkdtemp(prefix="pact-principal-")
os.environ.setdefault("PACT_DB_URL", f"sqlite:///{_TMP}/gate.db")
os.environ.setdefault("PACT_GATE_KEY_PATH", f"{_TMP}/gate_signing_key.hex")
os.environ.setdefault("PACT_PROFILE", "razorpay-track01")

from fastapi.testclient import TestClient  # noqa: E402

from contracts.crypto import generate_keypair, sign  # noqa: E402
from contracts.ids import new_id  # noqa: E402
from core import app as gate_app  # noqa: E402
from merchant.gate_client import HttpGateClient  # noqa: E402
from merchant.wallets import CONNECT  # noqa: E402
from tests.conftest import iso  # noqa: E402


@pytest.fixture
def http_gate():
    client = HttpGateClient(base_url="http://gate.test")
    client._client = TestClient(gate_app.app, base_url="http://gate.test")
    return client


@pytest.fixture
def registered(http_gate):
    priv, pub = generate_keypair()
    now = datetime.now(timezone.utc)
    body = {
        "v": 1, "mandate_id": new_id("mnd"),
        "delegator": {"account": "buyer@okaxis", "pubkey": pub},
        "delegate": {"agent_id": "buyer_agent_v1", "pubkey": pub},
        "intent": "restock office supplies", "issued_at": iso(now),
        "constraints": {
            "currency": gate_app.CURRENCY, "max_per_txn_minor": 500_000,
            "max_total_minor": 1_500_000, "max_count": 5,
            "merchant_allowlist": [gate_app.MERCHANT_PAYEE], "category_allowlist": ["stationery"],
            "valid_from": iso(now - timedelta(minutes=1)), "valid_until": iso(now + timedelta(days=1)),
        },
    }
    body["signature"] = sign(body, priv)
    assert http_gate._client.post("/v1/mandates", json=body).json()["accepted"] is True
    return body["mandate_id"], priv


def _signed(mandate_id: str, key) -> tuple[dict, str]:
    payload = {"action": CONNECT, "mandate_id": mandate_id, "issued_at": iso(datetime.now(timezone.utc))}
    return payload, sign(payload, key)


def test_the_gate_says_yes_over_http(http_gate, registered):
    mandate_id, priv = registered
    payload, sig = _signed(mandate_id, priv)
    assert http_gate.verify_principal(mandate_id, action=CONNECT, payload=payload,
                                      signature=sig) == (True, "OK")


def test_and_no_to_someone_else(http_gate, registered):
    mandate_id, _ = registered
    stranger, _ = generate_keypair()
    payload, sig = _signed(mandate_id, stranger)
    assert http_gate.verify_principal(mandate_id, action=CONNECT, payload=payload,
                                      signature=sig) == (False, "REQUEST_SIG_INVALID")


def test_a_gate_that_does_not_answer_is_a_no():
    unreachable = HttpGateClient(base_url="http://127.0.0.1:9", timeout=0.5)
    priv, _ = generate_keypair()
    payload, sig = _signed("mnd_x", priv)
    assert unreachable.verify_principal("mnd_x", action=CONNECT, payload=payload,
                                        signature=sig) == (False, "GATE_UNAVAILABLE")
