"""
The growth claim, machine checked.

"Every offer the merchant makes is provably approvable." That is the central
revenue argument, so it is not asserted by inspection — every suggested addon is
put through the **real gate**, and a single BLOCK fails the suite.

`test_the_naive_upsell_offers_things_the_gate_rejects` is the other half. It
asserts the baseline *fails*, because the pitch is a comparison and a comparison
with only one side measured is not a comparison.
"""

from __future__ import annotations

import pytest

from contracts.reason_codes import Verdict
from contracts.schemas import QuoteItemRequest


def _quote_and_headroom(quotes, gate, mandate, skus):
    headroom = gate.headroom_service.for_mandate(mandate.mandate_id)
    q = quotes.build(
        [QuoteItemRequest(sku=s) for s in skus],
        mandate_id=mandate.mandate_id,
        headroom=headroom,
    )
    return q, gate.headroom_service.for_mandate(mandate.mandate_id)


def test_every_suggested_addon_passes_the_real_gate(
    quotes, gate, make_mandate, merchant, authorize
):
    """The one to point at. A machine-checked proof of the revenue claim."""
    mandate = make_mandate()
    q, headroom = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])

    offers, _ = merchant.upsell.suggest(q, headroom)
    assert offers, "the fixture should produce at least one offer to be meaningful"

    for addon in offers:
        # A fresh mandate per offer: these are alternatives, not a shopping run.
        fresh = make_mandate()
        combined = quotes.build(
            [QuoteItemRequest(sku=line.sku, qty=line.qty) for line in q.items]
            + [QuoteItemRequest(sku=addon.sku, qty=1)],
            mandate_id=fresh.mandate_id,
        )
        decision = authorize(fresh, combined)
        assert decision.verdict is Verdict.ALLOW, (
            f"offered {addon.sku} but the gate answered "
            f"{decision.verdict}/{decision.reason_code} — the growth claim is false"
        )


@pytest.mark.parametrize(
    "constraints,label",
    [
        ({"max_total_paise": 100_000}, "almost no budget left"),
        ({"category_allowlist": ["cables"]}, "narrow category scope"),
        ({"max_per_txn_paise": 90_000}, "a tight per transaction cap"),
        ({"max_count": 1}, "no purchases remaining after this one"),
    ],
)
def test_the_guarantee_holds_for_the_awkward_mandates_too(
    constraints, label, quotes, gate, make_mandate, merchant, authorize
):
    """
    These are the personas that make the naive baseline look bad, so the
    guarantee has to hold for exactly these and not only for a roomy mandate.

    Each offer is checked against a **fresh mandate**. The offers are
    alternatives the buyer picks between, not a sequence they buy in turn, so
    authorizing them one after another against the same mandate would spend its
    budget and its count and prove nothing about the offer itself.
    """
    mandate = make_mandate(constraints=constraints)
    q, headroom = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])
    offers, _ = merchant.upsell.suggest(q, headroom)

    for addon in offers:
        fresh = make_mandate(constraints=constraints)
        combined = quotes.build(
            [QuoteItemRequest(sku=line.sku, qty=line.qty) for line in q.items]
            + [QuoteItemRequest(sku=addon.sku, qty=1)],
            mandate_id=fresh.mandate_id,
        )
        decision = authorize(fresh, combined)
        assert decision.verdict is Verdict.ALLOW, (
            f"with {label}, offered {addon.sku} and the gate answered "
            f"{decision.reason_code}"
        )


def test_nothing_is_offered_outside_the_category_allowlist(
    quotes, gate, make_mandate, merchant
):
    mandate = make_mandate(constraints={"category_allowlist": ["stationery"]})
    q, headroom = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])

    offers, filtered = merchant.upsell.suggest(q, headroom)
    assert all(a.category == "stationery" for a in offers)
    assert filtered > 0, "the furniture complement should have been withheld"


def test_a_mandate_with_no_room_gets_no_offers(quotes, gate, make_mandate, merchant):
    mandate = make_mandate(
        constraints={"max_total_paise": 120_000, "max_per_txn_paise": 120_000}
    )
    q, headroom = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])

    offers, filtered = merchant.upsell.suggest(q, headroom)
    assert offers == []
    assert filtered > 0


def test_the_naive_upsell_offers_things_the_gate_rejects(
    quotes, gate, make_mandate, merchant, authorize
):
    """
    Arm C, measured.

    A blind upsell against a narrow mandate offers furniture the gate then
    refuses. This asserts that it *does* happen — if it ever stopped, the
    contrast the pitch is built on would have quietly disappeared.
    """
    mandate = make_mandate(constraints={"category_allowlist": ["stationery"]})
    q, _ = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])

    offers, _ = merchant.upsell.suggest_naive(q)
    assert offers, "the naive path should offer something"

    verdicts = []
    for addon in offers:
        combined = quotes.build(
            [QuoteItemRequest(sku=line.sku, qty=line.qty) for line in q.items]
            + [QuoteItemRequest(sku=addon.sku, qty=1)],
            mandate_id=mandate.mandate_id,
        )
        verdicts.append(authorize(mandate, combined).verdict)

    assert Verdict.BLOCK in verdicts, (
        "the naive upsell no longer offers anything the gate rejects, so the "
        "arm C contrast is not being demonstrated"
    )


def test_the_counters_lane_b_needs_are_incremented(quotes, gate, make_mandate, merchant):
    mandate = make_mandate(constraints={"category_allowlist": ["stationery"]})
    q, headroom = _quote_and_headroom(quotes, gate, mandate, ["STA-NB-A5"])

    offers, filtered = merchant.upsell.suggest(q, headroom)
    merchant.upsell.record_acceptance()

    counters = merchant.upsell.counters
    assert counters.offers_made == len(offers)
    assert counters.offers_filtered_by_headroom == filtered
    assert counters.offers_accepted == 1
