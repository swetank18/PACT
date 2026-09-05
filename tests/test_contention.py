"""
What the gate says when it cannot get the write lock.

The ledger's correctness rests on SQLite's write lock — the balance is a SUM
computed inside the same `BEGIN IMMEDIATE` that inserts the reservation — so
under enough concurrency some authorize waits the full busy timeout and gives
up. That is the design working. What matters is what the caller is told, and it
used to be a 500: `scripts/load.py` at twenty racing buyers on a shared CI
runner produced exactly one, and a 500 there reads as a defect in the gate
rather than as a busy database.

The same mistake, twice removed: a gate timeout once surfaced as TOKEN_INVALID,
which reads as forgery. Refusing is right in all of these. Saying the wrong
thing about why is what sends someone hunting a bug, or an attacker, that is
not there.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

# The app builds its service at import time, so point it somewhere disposable
# before importing. Otherwise this test writes pact.db into the repository.
_TMP = tempfile.mkdtemp(prefix="pact-contention-")
os.environ.setdefault("PACT_DB_URL", f"sqlite:///{_TMP}/gate.db")
os.environ.setdefault("PACT_GATE_KEY_PATH", f"{_TMP}/gate_signing_key.hex")
# The gate refuses to start without a merchant VPA — deliberately, because an
# empty one silently turns the whole growth feature off with health checks
# green. Give it the same profile the deployment runs.
os.environ.setdefault("PACT_PROFILE", "razorpay-track01")

from fastapi.testclient import TestClient  # noqa: E402

from contracts.reason_codes import ReasonCode  # noqa: E402
from core import app as gate_app  # noqa: E402


@pytest.fixture
def client():
    # No lifespan: the sweeper and the event-bus loop are not what this is
    # about, and starting them would make the test depend on timing.
    return TestClient(gate_app.app)


def test_a_contended_ledger_is_a_503_and_says_which_code(client, monkeypatch):
    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(gate_app.service.gate, "authorize", locked)

    response = client.post("/v1/authorize", json=_authorize_body())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason_code"] == str(ReasonCode.GATE_UNAVAILABLE)
    # The merchant maps any HTTPError from the gate onto GATE_UNAVAILABLE and
    # refuses the order, so this whole path fails closed. The code is about what
    # the audit trail says, not about whether the money moves.


def test_a_real_database_error_is_not_dressed_up_as_busy(client, monkeypatch):
    """
    The handler must not swallow everything sqlite3 raises.

    A missing table reported as "busy, try again" is worse than the 500 it
    deserves: it turns a schema bug into an infrastructure shrug, and the caller
    retries forever against something that will never work.
    """
    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: reservations")

    monkeypatch.setattr(gate_app.service.gate, "authorize", broken)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        client.post("/v1/authorize", json=_authorize_body())


def _authorize_body() -> dict:
    """Shape only. Every one of these is refused long before the ledger is
    touched; the handler under test replaces the call that would do the work."""
    return {
        "mandate_id": "mnd_test",
        "quote_id": "qte_test",
        "amount_paise": 1000,
        "payee_vpa": "merchant@bank",
        "nonce": "nonce_test",
        "issued_at": "2026-09-05T00:00:00Z",
        "signature": "not-a-real-signature",
    }
