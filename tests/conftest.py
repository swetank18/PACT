"""Shared fixtures. Every test gets a fresh in-memory database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.crypto import generate_keypair, sign
from contracts.ids import new_id
from contracts.schemas import AuthorizeRequest, Mandate
from core.audit.store import AuditStore
from core.db import Database
from core.gate.auditor import Auditor
from core.gate.engine import Gate, GateConfig
from core.ledger.headroom import HeadroomService
from core.ledger.reservations import Ledger
from core.mandate.store import MandateStore
from merchant.catalog import MERCHANT_VPA, Inventory
from merchant.gate_client import InProcessGateClient
from merchant.quote import QuoteEngine
from merchant.saga import OrderStore, SagaRunner
from merchant.stats import StatsService
from merchant.upsell import UpsellEngine
from rails.mock_upi.adapter import MockUpiAdapter


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
def db() -> Database:
    return Database("sqlite:///:memory:")


@pytest.fixture
def keys():
    priv, pub = generate_keypair()
    return priv, pub


@pytest.fixture
def gate(db, tmp_path):
    # An ephemeral gate signing key per test, so the suite never touches the
    # persisted one and tests cannot depend on each other's key.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    audit = AuditStore(db)
    mandates = MandateStore(db)
    ledger = Ledger(db)
    g = Gate(
        db,
        config=GateConfig(merchant_vpa=MERCHANT_VPA),
        auditor=Auditor(api_key=None),  # deterministic mode; no network in tests
        mandates=mandates,
        ledger=ledger,
        audit=audit,
    )
    g.headroom_service = HeadroomService(
        mandates, ledger, merchant_vpa=MERCHANT_VPA, signing_key=Ed25519PrivateKey.generate()
    )
    return g


@pytest.fixture
def make_mandate(gate, keys):
    priv, pub = keys

    def _make(**overrides):
        now = datetime.now(timezone.utc)
        constraints = {
            "max_per_txn_paise": 500_000,
            "max_total_paise": 1_500_000,
            "max_count": 5,
            "merchant_allowlist": [MERCHANT_VPA],
            "category_allowlist": ["stationery", "cables", "office_furniture"],
            "valid_from": iso(now - timedelta(minutes=1)),
            "valid_until": iso(now + timedelta(days=1)),
        }
        constraints.update(overrides.pop("constraints", {}))
        m = Mandate(
            mandate_id=overrides.pop("mandate_id", new_id("mnd")),
            delegator={"vpa": "swetank@okaxis", "pubkey": pub},
            delegate={"agent_id": "buyer_agent_v1", "pubkey": pub},
            intent=overrides.pop("intent", "restock office supplies for the month"),
            constraints=constraints,
            issued_at=iso(now),
            **overrides,
        )
        m.signature = sign(m.model_dump(), priv)
        gate.mandates.register(m)
        return m

    return _make


@pytest.fixture
def quotes(db) -> QuoteEngine:
    return QuoteEngine(db)


@pytest.fixture
def authorize(gate, keys):
    """Builds and signs an authorize request the way the buyer agent would."""
    priv, _ = keys

    def _authorize(mandate, quote, **overrides):
        payload = {
            "mandate_id": mandate.mandate_id,
            "quote_id": quote.quote_id,
            "amount_paise": quote.total_paise,
            "payee_vpa": MERCHANT_VPA,
            "nonce": new_id("dec"),
            "issued_at": iso(datetime.now(timezone.utc)),
            "context": {
                "page_excerpt": ", ".join(i.name for i in quote.items),
                "agent_reasoning": "buying what the goal asks for",
            },
        }
        unsigned_only = overrides.pop("_unsigned", False)
        payload.update(overrides)
        req = AuthorizeRequest(**payload)
        if not unsigned_only:
            req.signature = sign(req.model_dump(), priv)
        return gate.authorize(req)

    return _authorize


@pytest.fixture
def rail() -> MockUpiAdapter:
    return MockUpiAdapter()


@pytest.fixture
def merchant(db, gate, rail, quotes):
    """The merchant plane wired in-process against the same gate."""

    class _Merchant:
        def __init__(self):
            self.db = db
            self.inventory = Inventory()
            self.quotes = quotes
            self.upsell = UpsellEngine(self.inventory)
            self.orders = OrderStore(db)
            self.audit = AuditStore(db)
            self.gate_client = InProcessGateClient(gate, gate.headroom_service)
            self.rail = rail
            self.saga = SagaRunner(
                db,
                audit=self.audit,
                rail=rail,
                inventory=self.inventory,
                quotes=quotes,
                gate=self.gate_client,
                orders=self.orders,
                step_delay_s=0.0,  # no theatre in tests
            )
            self.stats = StatsService(db, self.upsell)

    return _Merchant()
