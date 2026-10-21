"""
A profile names a currency, and everything priced follows it.

The PayPal rail cannot settle rupees, so the PayPal profile sells in dollars,
and the danger in that is quiet: a catalog where some prices are paise and
some are cents quotes a total that looks plausible and is wrong. So the price
list is all or nothing, the units are integers, and a profile with no money
rules for its currency does not boot.

The end-to-end tests run the merchant in a subprocess, because the catalog is
fixed when the module is imported, which is the point: an instance sells in one
currency for its whole life.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import Profile
from core.config import load as load_profile
from merchant.catalog import BASE_CATALOG, BASE_PRICING, CatalogError, build

REPO = Path(__file__).resolve().parents[1]
USD_PRICES = load_profile("paypal-agentic").pricing["prices"]


def profile(**overrides) -> Profile:
    base = dict(name="test", rail="mock_upi", merchant_payee="m@example", merchant_name="DeskKit")
    base.update(overrides)
    return Profile(**base)


# ------------------------------------------------------------------ build ---


def test_the_rupee_catalog_is_the_one_it_always_was():
    catalog, pricing = build(profile())
    assert catalog == BASE_CATALOG
    assert (pricing.currency, pricing.tax_bps) == ("INR", BASE_PRICING["tax_bps"])


def test_the_dollar_profile_prices_every_sku_in_cents():
    catalog, pricing = build(load_profile("paypal-agentic"))
    assert pricing.currency == "USD"
    assert {p.sku: p.price_minor for p in catalog} == USD_PRICES
    assert "illustrative" in pricing.tax_label


def test_a_dollar_catalog_with_one_rupee_price_left_in_it_does_not_build():
    prices = dict(USD_PRICES)
    prices.pop("FUR-CHR-ERG")
    with pytest.raises(CatalogError, match="FUR-CHR-ERG"):
        build(profile(currency="USD", pricing={**_usd_rules(), "prices": prices}))


def test_a_price_for_a_sku_that_does_not_exist_is_refused():
    with pytest.raises(CatalogError, match="NOPE"):
        build(profile(currency="USD", pricing={**_usd_rules(), "prices": {**USD_PRICES, "NOPE": 1}}))


@pytest.mark.parametrize("bad", [12.99, "1299", -1, True])
def test_a_price_that_is_not_whole_minor_units_is_refused(bad):
    """12.99 in a YAML file is a typo for 1299, not a price to round."""
    with pytest.raises(CatalogError, match="minor units"):
        build(profile(currency="USD",
                      pricing={**_usd_rules(), "prices": {**USD_PRICES, "STA-NB-A5": bad}}))


def test_a_profile_missing_a_pricing_rule_says_which():
    rules = _usd_rules()
    rules.pop("tax_bps")
    with pytest.raises(CatalogError, match="tax_bps"):
        build(profile(currency="USD", pricing={**rules, "prices": USD_PRICES}))


def test_a_currency_with_no_money_rules_is_refused():
    with pytest.raises(CatalogError, match="XYZ"):
        build(profile(currency="XYZ"))


def _usd_rules() -> dict:
    rules = dict(load_profile("paypal-agentic").pricing)
    rules.pop("prices")
    return rules


# ------------------------------------------------------- the merchant, run ---


def _merchant(profile_name: str, script: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PACT_PROFILE=profile_name, PACT_DB_URL="sqlite:///:memory:",
               PAYPAL_CLIENT_ID="sb-test", PAYPAL_CLIENT_SECRET="sb-test")
    env.pop("PACT_RAIL", None)
    env.pop("PACT_MERCHANT_PAYEE", None)
    done = subprocess.run([sys.executable, "-c", script], env=env, cwd=REPO,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


QUOTE_AND_MANIFEST = """
import asyncio, json
import merchant.app as m
from contracts.schemas import QuoteItemRequest
q = m.service.quotes.build([QuoteItemRequest(sku="STA-NB-A5", qty=2)], mandate_id=None)
man = asyncio.run(m.manifest())
print(json.dumps({"rail": m.service.rail.name, "quote": q.model_dump(), "manifest": man,
                  "stats": m.service.stats.compute().model_dump()}))
"""


def test_the_paypal_profile_boots_and_quotes_in_dollars():
    """
    Two notebooks at $12.99: $25.98, 8% illustrative tax is $2.08 (207.84
    rounded half up), and under the $50 free-shipping line so $5.99 shipping.
    """
    out = _merchant("paypal-agentic", QUOTE_AND_MANIFEST)
    assert out["rail"] == "paypal"
    q = out["quote"]
    assert q["currency"] == "USD"
    assert (q["subtotal_minor"], q["tax_minor"], q["shipping_minor"]) == (2598, 208, 599)
    assert q["total_minor"] == 2598 + 208 + 599
    assert out["manifest"]["currency"] == "USD"
    assert "illustrative" in out["manifest"]["tax_label"]
    assert out["stats"]["currency"] == "USD"


def test_the_manifest_advertises_the_payee_the_gate_checks():
    """
    The manifest used to advertise a hardcoded `deskkit@razorpay` while the gate
    checked the profile's payee. Under `upi-agentic`, whose payee is
    `deskkit@okaxis`, a buyer copying the manifest's payee into its mandate was
    out of scope on every purchase. Nothing ran that profile, so nothing saw it.
    """
    out = _merchant("upi-agentic", QUOTE_AND_MANIFEST)
    assert out["manifest"]["merchant_payee"] == load_profile("upi-agentic").merchant_payee
    assert out["quote"]["currency"] == "INR"


def test_the_paypal_profile_with_no_keys_runs_simulated_in_dollars():
    """
    The judge who clones the repo and runs it with no PayPal app of their own.
    The profile's rail is a default, not an instruction: no credentials, so
    the simulated rail, in the profile's currency.
    """
    env_without_keys = """
import os
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
    os.environ.pop(k, None)
""" + QUOTE_AND_MANIFEST
    out = _merchant("paypal-agentic", env_without_keys)
    assert out["rail"] == "mock_upi"
    assert out["quote"]["currency"] == "USD"


# --------------------------------------------- the mandate's own currency ---


def test_a_mandate_in_another_currency_is_out_of_scope(make_mandate, quotes, authorize, gate):
    """
    50000 is $500 to a dollar mandate and ₹500 to a rupee merchant. The gate
    refuses to read one as the other, and refuses before the ceiling check, so
    no budget is reserved for a payment it is about to turn down.
    """
    from contracts.reason_codes import ReasonCode
    from contracts.schemas import QuoteItemRequest

    mandate = make_mandate(constraints={"currency": "USD"})
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)

    decision = authorize(mandate, q)

    assert decision.verdict == "BLOCK"
    assert decision.reason_code == ReasonCode.SCOPE_CURRENCY_MISMATCH
    assert gate.ledger.spent_and_count(mandate.mandate_id) == (0, 0)


def test_the_envelope_says_why_and_the_merchant_offers_nothing(make_mandate, quotes, gate, merchant):
    from contracts.schemas import QuoteItemRequest

    mandate = make_mandate(constraints={"currency": "USD"})
    envelope = gate.headroom_service.for_mandate(mandate.mandate_id)
    assert envelope.currency == "USD"
    assert envelope.merchant_in_scope is False

    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id,
                     headroom=envelope)
    assert q.headroom_fit is not None and q.headroom_fit.fits is False
    offered, _ = merchant.upsell.suggest(q, envelope)
    assert offered == []


@pytest.mark.parametrize("bad", ["usd", "US", "DOLLARS", ""])
def test_a_currency_that_is_not_an_iso_code_is_refused(bad):
    from pydantic import ValidationError

    from contracts.schemas import Constraints

    with pytest.raises(ValidationError):
        Constraints(currency=bad, max_per_txn_minor=1, max_total_minor=1, max_count=1,
                    merchant_allowlist=["m"], category_allowlist=["c"],
                    valid_from="2026-01-01T00:00:00Z", valid_until="2027-01-01T00:00:00Z")
