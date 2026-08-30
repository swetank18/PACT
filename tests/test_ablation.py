"""
Ablation and reset.

Ablation is how Lane B produces the matrix: disable one check, rerun the attack
set, record what leaks. The important property is that an ablated check reports
**SKIPPED with a reason**, never a silent PASS — otherwise the matrix would
credit a disabled layer with a block it did not make.

Reset gets pressed forty times on stage. It has to be fast and it has to be
complete; a stale row on the second run in front of judges is the failure nobody
sees coming.
"""

from __future__ import annotations

import time

import pytest

from contracts.reason_codes import CHECK_ORDER, ReasonCode, Verdict
from contracts.schemas import QuoteItemRequest
from core.gate.engine import GateConfig
from merchant.catalog import MERCHANT_VPA


def _ablate(gate, *names: str) -> None:
    gate.config = GateConfig(merchant_vpa=MERCHANT_VPA, disabled_checks=frozenset(names))


@pytest.fixture
def quote(quotes, make_mandate):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    return mandate, q


def test_an_ablated_check_is_skipped_not_silently_passed(gate, quote, authorize):
    mandate, q = quote
    _ablate(gate, "replay")

    d = authorize(mandate, q)
    replay = next(c for c in d.checks if c.name == "replay")

    assert replay.status == "SKIPPED"
    assert replay.detail == "ablated", (
        "an ablated check must say so, or the matrix credits it with a block "
        "it never made"
    )


def test_the_chain_is_still_reported_in_full_when_ablated(gate, quote, authorize):
    mandate, q = quote
    _ablate(gate, "scope", "ceiling")
    d = authorize(mandate, q)
    assert [c.name for c in d.checks] == list(CHECK_ORDER)


def test_disabling_replay_lets_a_replay_through(gate, quote, authorize):
    """One row of the matrix, asserted. Removing the layer must actually leak."""
    mandate, q = quote
    nonce = "the-same-nonce-twice"

    assert authorize(mandate, q, nonce=nonce).verdict is Verdict.ALLOW
    assert authorize(mandate, q, nonce=nonce).reason_code is ReasonCode.NONCE_REPLAY

    _ablate(gate, "replay")
    leaked = authorize(mandate, q, nonce=nonce)
    assert leaked.verdict is Verdict.ALLOW, "with replay ablated, the replay must leak"


def test_disabling_scope_lets_a_lookalike_vpa_through(gate, quote, authorize):
    mandate, q = quote
    assert authorize(mandate, q, payee_vpa="deskkit@razorpayy").reason_code is (
        ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED
    )

    _ablate(gate, "scope")
    assert authorize(mandate, q, payee_vpa="deskkit@razorpayy").verdict is Verdict.ALLOW


def test_disabling_quote_binding_lets_an_invented_price_through(gate, quote, authorize):
    mandate, q = quote
    assert authorize(mandate, q, amount_paise=q.total_paise + 1).reason_code is (
        ReasonCode.QUOTE_AMOUNT_MISMATCH
    )

    _ablate(gate, "quote_binding")
    d = authorize(mandate, q, amount_paise=q.total_paise + 1)
    assert d.verdict is Verdict.ALLOW, "with quote binding ablated, the hallucinated price leaks"


def test_ablating_one_check_does_not_disable_another(gate, quote, authorize):
    """
    The diagonal in the matrix only means something if the layers are actually
    independent. Removing replay must leave scope working.
    """
    mandate, q = quote
    _ablate(gate, "replay")
    assert authorize(mandate, q, payee_vpa="deskkit@razorpayy").reason_code is (
        ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED
    )


# ----------------------------------------------------------------- reset ---


def test_reset_clears_every_table(db, gate, quotes, make_mandate, authorize, merchant):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    decision = authorize(mandate, q)
    merchant.saga.run(quote=q, mandate_id=mandate.mandate_id, decision_id=decision.decision_id)

    with db.read_tx() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] > 0
        assert conn.execute("SELECT COUNT(*) c FROM saga_steps").fetchone()["c"] > 0

    db.reset()

    from core.db import RESETTABLE_TABLES

    with db.read_tx() as conn:
        for table in RESETTABLE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]  # noqa: S608
            assert n == 0, f"{table} still has {n} rows after reset"


def test_reset_is_fast_enough_to_press_forty_times(db, capsys):
    """
    Under a second, every time. It gets pressed forty times, and the run that
    matters is the one in front of judges.
    """
    timings = []
    for _ in range(40):
        start = time.perf_counter()
        db.reset()
        timings.append((time.perf_counter() - start) * 1000)

    timings.sort()
    assert timings[-1] < 1000, f"slowest reset was {timings[-1]:.1f} ms"
    with capsys.disabled():
        print(
            f"\n    40 resets: min {timings[0]:.2f} ms, median {timings[20]:.2f} ms, "
            f"max {timings[-1]:.2f} ms\n"
        )


def test_a_reset_leaves_no_stale_decision_behind(db, gate, quotes, make_mandate, authorize):
    """The stale-row failure: a second demo run showing the first run's data."""
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    authorize(mandate, q)
    assert gate.audit.list_decisions() != []

    db.reset()
    assert gate.audit.list_decisions() == []
    assert gate.mandates.get(mandate.mandate_id) is None
