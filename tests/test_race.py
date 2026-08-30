"""
The race test. This is the number to show on the slide.

Fire twenty concurrent payments at a mandate with room for five and assert the
total never exceeds the cap. Then do the same against `naive_reserve` — the
read-then-write implementation a reasonable team writes — and show that it
overspends.

Run against a **file-backed** database, deliberately. The in-memory database
shares one connection across threads and needs a process lock to work at all,
which would mean this test was exercising a Python `threading.Lock` rather than
`BEGIN IMMEDIATE`. On a file, each thread gets its own connection and SQLite's
own write lock is what serialises them — which is the mechanism that will be
running in the demo, so it is the one worth testing.

`test_the_naive_ledger_overspends` asserts the *failure*. If the naive version
ever stops overspending, the harness has stopped being concurrent and the
passing case above is no longer evidence of anything.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from contracts.crypto import generate_keypair, sign
from contracts.ids import new_id
from contracts.schemas import Mandate, QuoteItemRequest
from core.db import Database
from core.ledger.reservations import Ledger
from core.mandate.store import MandateStore
from merchant.catalog import MERCHANT_VPA
from tests.conftest import iso

CONCURRENCY = 20
PER_PAYMENT_PAISE = 100_000


@pytest.fixture
def file_db(tmp_path) -> Database:
    """A real file, so SQLite's own locking is what is under test."""
    return Database(f"sqlite:///{tmp_path}/race.db")


def _mandate_on(db: Database, **constraint_overrides) -> Mandate:
    priv, pub = generate_keypair()
    now = datetime.now(timezone.utc)
    constraints = {
        "max_per_txn_paise": PER_PAYMENT_PAISE,
        "max_total_paise": 5 * PER_PAYMENT_PAISE,
        "max_count": 50,  # not the binding constraint unless a test says so
        "merchant_allowlist": [MERCHANT_VPA],
        "category_allowlist": ["stationery"],
        "valid_from": iso(now - timedelta(minutes=1)),
        "valid_until": iso(now + timedelta(days=1)),
    }
    constraints.update(constraint_overrides)
    m = Mandate(
        mandate_id=new_id("mnd"),
        delegator={"vpa": "swetank@okaxis", "pubkey": pub},
        delegate={"agent_id": "buyer_agent_v1", "pubkey": pub},
        intent="restock office supplies",
        constraints=constraints,
        issued_at=iso(now),
    )
    m.signature = sign(m.model_dump(), priv)
    MandateStore(db).register(m)
    return m


def _fire(fn, mandate, n: int) -> list:
    """Release every thread at once, so they genuinely overlap."""
    barrier = threading.Barrier(n)

    def one(_i: int):
        barrier.wait()
        return fn(
            mandate_id=mandate.mandate_id,
            decision_id=new_id("dec"),
            amount_paise=PER_PAYMENT_PAISE,
            constraints=mandate.constraints,
        )

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(n)))


def test_atomic_reservation_never_exceeds_the_total_cap(file_db):
    mandate = _mandate_on(file_db)
    ledger = Ledger(file_db)

    outcomes = _fire(ledger.reserve, mandate, CONCURRENCY)
    approved = [o for o in outcomes if o.ok]
    spent, _ = ledger.spent_and_count(mandate.mandate_id)

    assert len(approved) == 5, f"expected exactly 5 approvals, got {len(approved)}"
    assert spent == 5 * PER_PAYMENT_PAISE
    assert spent <= mandate.constraints.max_total_paise


def test_the_count_ceiling_holds_under_concurrency_too(file_db):
    mandate = _mandate_on(
        file_db, max_total_paise=100 * PER_PAYMENT_PAISE, max_count=3
    )
    ledger = Ledger(file_db)

    outcomes = _fire(ledger.reserve, mandate, CONCURRENCY)
    assert len([o for o in outcomes if o.ok]) == 3
    assert ledger.spent_and_count(mandate.mandate_id)[1] == 3


def test_the_naive_ledger_overspends(file_db, capsys):
    mandate = _mandate_on(file_db)
    ledger = Ledger(file_db)

    outcomes = _fire(ledger.naive_reserve, mandate, CONCURRENCY)
    approved = len([o for o in outcomes if o.ok])
    spent, _ = ledger.spent_and_count(mandate.mandate_id)
    cap = mandate.constraints.max_total_paise

    assert approved > 5, (
        f"the naive ledger approved {approved}, not more than the cap allows. "
        "That means this test is not running concurrently and proves nothing."
    )
    assert spent > cap

    with capsys.disabled():
        print(
            f"\n    atomic: 5 approved, {5 * PER_PAYMENT_PAISE} paise, cap {cap}"
            f"\n    naive:  {approved} approved, {spent} paise, cap {cap} "
            f"-> overspent by {spent - cap}\n"
        )


def test_a_blocked_request_gives_its_budget_back(quotes, authorize, make_mandate, gate):
    """
    The ceiling takes budget before quote binding runs. If quote binding then
    refuses, the money must come back in the same call — otherwise forty demo
    runs slowly starve the mandate and nobody sees why.
    """
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)

    before = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)
    d = authorize(mandate, q, amount_paise=q.total_paise + 50_000)
    after = gate.ledger.headroom_paise(mandate.mandate_id, mandate.constraints)

    assert d.verdict == "BLOCK"
    assert after == before, "a blocked request must not hold budget"


def test_a_step_up_keeps_its_reservation(db, make_mandate, gate):
    """
    STEP_UP is the one refusal that keeps the money held. The human is being
    asked right now, and releasing would let a concurrent request spend it out
    from underneath them. The sweeper reclaims it if they never answer.
    """
    mandate = make_mandate()
    ledger = Ledger(db)
    decision_id = new_id("dec")
    outcome = ledger.reserve(
        mandate_id=mandate.mandate_id,
        decision_id=decision_id,
        amount_paise=50_000,
        constraints=mandate.constraints,
    )
    assert outcome.ok
    assert ledger.spent_and_count(mandate.mandate_id)[0] == 50_000


def test_the_sweeper_releases_expired_reservations(db, make_mandate):
    mandate = make_mandate()
    ledger = Ledger(db)
    assert ledger.reserve(
        mandate_id=mandate.mandate_id,
        decision_id=new_id("dec"),
        amount_paise=50_000,
        constraints=mandate.constraints,
    ).ok

    with db.immediate_tx() as conn:
        conn.execute("UPDATE reservations SET expires_at = '2000-01-01T00:00:00Z'")

    assert ledger.sweep() == 1
    assert ledger.spent_and_count(mandate.mandate_id)[0] == 0


def test_the_sweeper_never_expires_committed_money(db, make_mandate):
    """
    A COMMITTED reservation is money that actually moved. Expiring it would hand
    the buyer back budget they have already spent.
    """
    mandate = make_mandate()
    ledger = Ledger(db)
    decision_id = new_id("dec")
    ledger.reserve(
        mandate_id=mandate.mandate_id,
        decision_id=decision_id,
        amount_paise=50_000,
        constraints=mandate.constraints,
    )
    ledger.commit(decision_id)

    with db.immediate_tx() as conn:
        conn.execute("UPDATE reservations SET expires_at = '2000-01-01T00:00:00Z'")

    assert ledger.sweep() == 0
    assert ledger.spent_and_count(mandate.mandate_id)[0] == 50_000
