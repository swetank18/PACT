"""
The step up endpoint, at the HTTP boundary.

A step up is the one refusal that asks a human rather than answering for them,
so the endpoint that carries their answer is the one place in the gate where a
person's authority is asserted directly. Three things have to hold, and the
first two already did:

  the approval is signed by the delegator's device key, and an unsigned or
  wrongly signed one is refused rather than trusted;

  the signature is over *this* decision, so an approval cannot be lifted from
  one purchase and replayed onto another;

  the mandate is still live when the answer arrives. A step up sits on screen
  for as long as the human takes, and the account holder can revoke in the
  middle of that.

Driven through the app rather than the engine, because the signature check and
the revocation check both live in the endpoint.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Same dance as tests/test_contention.py: the app builds its service at import
# time, so it has to be pointed somewhere disposable before the import.
_TMP = tempfile.mkdtemp(prefix="pact-stepup-")
os.environ.setdefault("PACT_DB_URL", f"sqlite:///{_TMP}/gate.db")
os.environ.setdefault("PACT_GATE_KEY_PATH", f"{_TMP}/gate_signing_key.hex")
os.environ.setdefault("PACT_PROFILE", "razorpay-track01")

from fastapi.testclient import TestClient  # noqa: E402

from contracts.ids import new_id  # noqa: E402
from contracts.reason_codes import ReasonCode, Verdict  # noqa: E402
from contracts.schemas import Decision, utcnow  # noqa: E402
from contracts.crypto import generate_keypair, sign  # noqa: E402
from core import app as gate_app  # noqa: E402
from tests.conftest import iso  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402


@pytest.fixture
def client():
    return TestClient(gate_app.app)


@pytest.fixture
def pending(client):
    """A registered mandate and a decision parked in STEP_UP against it."""
    priv, pub = generate_keypair()
    now = datetime.now(timezone.utc)
    body = {
        "v": 1,
        "mandate_id": new_id("mnd"),
        "delegator": {"vpa": "buyer@okaxis", "pubkey": pub},
        "delegate": {"agent_id": "buyer_agent_v1", "pubkey": pub},
        "intent": "restock office supplies",
        "issued_at": iso(now),
        "constraints": {
            "max_per_txn_paise": 500_000,
            "max_total_paise": 1_500_000,
            "max_count": 5,
            "merchant_allowlist": ["deskkit@razorpay"],
            "category_allowlist": ["stationery"],
            "valid_from": iso(now - timedelta(minutes=1)),
            "valid_until": iso(now + timedelta(hours=24)),
        },
    }
    body["signature"] = sign(body, priv)
    assert client.post("/v1/mandates", json=body).status_code == 200

    decision = Decision(
        decision_id=new_id("dec"),
        mandate_id=body["mandate_id"],
        verdict=Verdict.STEP_UP,
        reason_code=ReasonCode.INTENT_MISMATCH,
        payee_vpa="deskkit@razorpay",
        amount_paise=98_282,
        quote_id=new_id("qte"),
        elapsed_ms=1.0,
        checks=[],
        at=utcnow(),
    )
    gate_app.service.audit.record_decision(decision)
    return body, priv, decision


def _approve(decision_id: str, priv: str) -> dict:
    approval = {"decision_id": decision_id, "approve": True, "at": utcnow()}
    return {"approve": True, "approval": approval, "signature": sign(approval, priv)}


def test_a_signed_approval_from_the_device_allows_and_issues_a_token(client, pending):
    body, priv, decision = pending

    r = client.post(
        f"/v1/decisions/{decision.decision_id}/step_up", json=_approve(decision.decision_id, priv)
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "ALLOW"
    assert r.json()["settlement_token"]


def test_an_unsigned_approval_is_refused(client, pending):
    """Otherwise anyone who can reach the gate can approve anything."""
    body, priv, decision = pending

    r = client.post(
        f"/v1/decisions/{decision.decision_id}/step_up",
        json={"approve": True, "approval": {"decision_id": decision.decision_id}},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["reason_code"] == str(ReasonCode.REQUEST_SIG_INVALID)


def test_an_approval_signed_for_a_different_decision_is_refused(client, pending):
    """An approval must not be liftable from one purchase onto another."""
    body, priv, decision = pending

    r = client.post(
        f"/v1/decisions/{decision.decision_id}/step_up", json=_approve(new_id("dec"), priv)
    )
    assert r.status_code == 403


def test_a_step_up_answered_after_the_mandate_is_revoked_is_refused(client, pending):
    """
    The kill switch has to win against a step up that is already on screen.

    The settlement token this would have issued is refused at redemption now, so
    no money moves either way — but a decision recorded as ALLOW against a
    revoked mandate is a false row in the audit trail, and the audit trail is
    the product.
    """
    body, priv, decision = pending

    assert client.post(f"/v1/mandates/{body['mandate_id']}/revoke").status_code == 200

    r = client.post(
        f"/v1/decisions/{decision.decision_id}/step_up", json=_approve(decision.decision_id, priv)
    )
    assert r.status_code == 403
    assert r.json()["detail"]["reason_code"] == str(ReasonCode.MANDATE_REVOKED)

    after = gate_app.service.audit.get_decision(decision.decision_id)
    assert after["verdict"] == str(Verdict.STEP_UP), (
        "the decision was mutated despite the refusal"
    )
