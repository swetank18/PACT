"""
The headroom envelope: what it says, and what it must never say.

The privacy claim is "the schema physically cannot carry it", so the test is on
the **field set**, not on a particular instance. A test that only checked one
envelope's values would pass right up until someone adds a convenient field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contracts.crypto import verify
from contracts.schemas import Headroom, QuoteItemRequest

#: Everything the merchant is allowed to learn. Adding to this set is a contract
#: change and should require deleting a line from this test, deliberately.
ALLOWED_FIELDS = {
    "mandate_id",
    "headroom_paise",
    "max_per_txn_paise",
    "payments_remaining",
    "categories_allowed",
    "valid_until",
    "merchant_in_scope",
    "as_of",
    "signature",
}

#: Things a merchant must not learn about a buyer.
FORBIDDEN_SUBSTRINGS = (
    "delegator",
    "vpa",
    "intent",
    "max_total",
    "total_budget",
    "spent",
    "history",
    "pubkey",
)


def test_the_envelope_carries_only_the_allowed_fields():
    assert set(Headroom.model_fields) == ALLOWED_FIELDS


def test_no_field_name_hints_at_buyer_identity_or_history():
    for field in Headroom.model_fields:
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden not in field, f"{field} looks like it leaks {forbidden}"


def test_the_serialised_envelope_contains_no_buyer_identity(
    gate, make_mandate, quotes, authorize
):
    mandate = make_mandate()
    # Spend something first. At zero spend `headroom_paise` legitimately equals
    # the budget, which would make the "total budget is absent" assertion pass
    # for the wrong reason.
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    assert authorize(mandate, q).verdict == "ALLOW"

    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)
    blob = envelope.model_dump_json().lower()

    assert mandate.delegator.vpa.lower() not in blob
    assert mandate.intent.lower() not in blob
    assert mandate.delegator.pubkey.lower() not in blob
    # The total budget is the number that would tell a merchant how rich the
    # buyer is. Only what is *left* is publishable.
    assert str(mandate.constraints.max_total_paise) not in blob


def test_headroom_is_what_is_left_not_what_the_budget_was(gate, make_mandate, quotes, authorize):
    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)

    before = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert before.headroom_paise == mandate.constraints.max_total_paise

    d = authorize(mandate, q)
    assert d.verdict == "ALLOW"

    after = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert after.headroom_paise == before.headroom_paise - q.total_paise
    assert after.payments_remaining == before.payments_remaining - 1


def test_the_envelope_is_signed_by_the_gate(gate, make_mandate):
    """The merchant can prove what it was told and when. That is the dispute
    story, and it costs ten minutes."""
    mandate = make_mandate()
    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)

    assert envelope.signature
    assert verify(
        envelope.model_dump(), envelope.signature, gate.headroom_service.public_key_b64u
    )


def test_a_tampered_envelope_does_not_verify(gate, make_mandate):
    mandate = make_mandate()
    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)

    forged = envelope.model_dump()
    forged["headroom_paise"] = 99_999_999
    assert not verify(
        forged, envelope.signature, gate.headroom_service.public_key_b64u
    )


def test_a_revoked_mandate_reports_zero_rather_than_disappearing(gate, make_mandate):
    """
    The merchant's question is "what may I offer". The truthful answer for a
    dead mandate is "nothing", which keeps the upsell filter correct without
    teaching it about mandate lifecycle.
    """
    mandate = make_mandate()
    gate.mandates.revoke(mandate.mandate_id)

    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert envelope is not None
    assert envelope.headroom_paise == 0
    assert envelope.payments_remaining == 0
    assert envelope.categories_allowed == []
    assert envelope.merchant_in_scope is False


def test_an_out_of_scope_merchant_is_told_so(gate, make_mandate):
    mandate = make_mandate(constraints={"merchant_allowlist": ["someoneelse@okhdfc"]})
    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert envelope.merchant_in_scope is False


def test_unknown_mandate_returns_nothing(gate):
    assert gate.headroom_service.for_mandate("mnd_NOPE") is None


def test_the_gate_refuses_a_signing_key_on_the_denylist(tmp_path, monkeypatch):
    """
    The refusal itself, exercised on every clone.

    This used to check the real file at the default path and skip when it was
    absent — which is every fresh clone, so the refusal path was never actually
    run. Worse, the moment anyone started the gate the check went the other way:
    the gate generates its own key *at that same path* on first boot, so a run
    of ./scripts/dev.sh followed by ./scripts/test.sh failed with DID NOT RAISE
    and pointed at a compromised key that was not there. A check that fires on
    the normal case and stays silent on the case it exists for is worse than no
    check.

    So: generate a key, put its fingerprint on the denylist, and assert the
    refusal. The private half of the key that actually leaked is not here and
    must never be, and a fingerprint is all the mechanism needs.
    """
    import hashlib

    from core.ledger import headroom as headroom_module

    path = tmp_path / "gate_signing_key.hex"
    key = headroom_module.load_or_create_gate_key(str(path))
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()

    monkeypatch.setattr(
        headroom_module, "COMPROMISED_PUBLIC_KEYS", frozenset({fingerprint})
    )
    with pytest.raises(RuntimeError, match="public repository"):
        headroom_module.load_or_create_gate_key(str(path))


def test_the_denylist_still_carries_the_key_that_leaked():
    """The mechanism above is worth nothing if the list it reads is empty."""
    from core.ledger.headroom import COMPROMISED_PUBLIC_KEYS

    assert "1b12f696b6de17d0e9a50f8cda09e02038b513df2d37465dcec8b3a6a3487d90" in (
        COMPROMISED_PUBLIC_KEYS
    ), "the fingerprint of the key that was published must stay on the denylist"


def test_a_clone_that_still_holds_the_leaked_key_refuses_to_boot():
    """
    The original case, gated on the file actually being that key.

    A clone predating the purge still has the published key on disk with
    DEFAULT_KEY_PATH pointing at it, and would otherwise boot with it silently.
    A clone that has merely run the gate has a locally generated key at the same
    path, which is the normal case and not a finding.
    """
    import hashlib

    from contracts.crypto import private_key_from_seed
    from core.ledger.headroom import (
        COMPROMISED_PUBLIC_KEYS,
        DEFAULT_KEY_PATH,
        load_or_create_gate_key,
    )

    on_disk = Path(DEFAULT_KEY_PATH)
    if not on_disk.exists():
        pytest.skip("no key at the default path, so there is nothing to check")

    key = private_key_from_seed(bytes.fromhex(on_disk.read_text().strip()))
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    if fingerprint not in COMPROMISED_PUBLIC_KEYS:
        pytest.skip("the key at the default path was generated locally, which is fine")

    with pytest.raises(RuntimeError, match="public repository"):
        load_or_create_gate_key(str(on_disk))


def test_a_freshly_generated_key_is_accepted(tmp_path):
    """The check must recognise one key, not distrust every key."""
    from core.ledger.headroom import load_or_create_gate_key

    path = tmp_path / "gate_signing_key.hex"
    first = load_or_create_gate_key(str(path))
    assert path.exists()
    # And it is stable across restarts, or every envelope already issued breaks.
    second = load_or_create_gate_key(str(path))
    assert first.public_key().public_bytes_raw() == second.public_key().public_bytes_raw()
