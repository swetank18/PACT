"""
The headroom envelope, verified rather than trusted.

The claim the design rests on is that a merchant can trust a signed envelope it
was handed **without asking anyone**. The gate has always signed them and always
published its key at `/v1/gate/pubkey`, whose docstring reads "So the merchant
can verify the headroom envelopes it is handed" — and for the life of the
project nothing called it. `HttpGateClient.headroom` parsed the JSON and
believed it.

Nothing could have moved money through that: every ceiling is re-derived server
side at authorize. What it could do is make the central claim false, which is
worse for a project whose whole argument is that the envelope is trustworthy.

These drive the real client through a stub transport, so the code under test is
the one the merchant actually runs, signatures and all.
"""

from __future__ import annotations

import json

import httpx
import pytest

from contracts.crypto import b64u_encode, generate_keypair, sign
from contracts.schemas import Headroom, utcnow
from merchant.gate_client import HttpGateClient


def _envelope(key, *, headroom_paise: int = 500_000) -> Headroom:
    envelope = Headroom(
        mandate_id="mnd_TEST",
        headroom_paise=headroom_paise,
        max_per_txn_paise=500_000,
        payments_remaining=3,
        categories_allowed=["stationery"],
        valid_until="2099-01-01T00:00:00Z",
        merchant_in_scope=True,
        as_of=utcnow(),
    )
    envelope.signature = sign(envelope.model_dump(), key)
    return envelope


class Gate:
    """A stub gate. `pubkey` is what it publishes; `body` is what it serves."""

    def __init__(self, pubkey: str, body: dict) -> None:
        self.pubkey = pubkey
        self.body = body
        self.pubkey_fetches = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gate/pubkey"):
            self.pubkey_fetches += 1
            return httpx.Response(
                200, json={"algorithm": "Ed25519", "public_key_b64u": self.pubkey}
            )
        return httpx.Response(200, json=self.body)


def _client(gate: Gate) -> HttpGateClient:
    client = HttpGateClient(base_url="http://gate.test")
    client._client = httpx.Client(  # noqa: SLF001 - swapping the transport is the point
        base_url="http://gate.test", transport=httpx.MockTransport(gate.handler)
    )
    return client


@pytest.fixture
def keypair():
    return generate_keypair()


def test_a_correctly_signed_envelope_is_accepted(keypair):
    priv, pub = keypair
    gate = Gate(pub, json.loads(_envelope(priv).model_dump_json()))

    envelope = _client(gate).headroom("mnd_TEST")

    assert envelope is not None
    assert envelope.headroom_paise == 500_000


def test_an_envelope_edited_in_transit_is_refused(keypair):
    """
    The one that matters. Raise the headroom on the wire and the merchant would
    have offered against a number the gate never said.
    """
    priv, pub = keypair
    body = json.loads(_envelope(priv).model_dump_json())
    body["headroom_paise"] = 99_999_900

    assert _client(Gate(pub, body)).headroom("mnd_TEST") is None


def test_an_envelope_signed_by_someone_else_is_refused(keypair):
    priv, pub = keypair
    other_priv, _ = generate_keypair()
    body = json.loads(_envelope(other_priv).model_dump_json())

    assert _client(Gate(pub, body)).headroom("mnd_TEST") is None


def test_an_unsigned_envelope_is_refused(keypair):
    priv, pub = keypair
    body = json.loads(_envelope(priv).model_dump_json())
    body["signature"] = None

    assert _client(Gate(pub, body)).headroom("mnd_TEST") is None


def test_it_fails_closed_when_the_key_cannot_be_fetched(keypair):
    """No key, no offers — never "trust it anyway"."""
    priv, pub = keypair
    body = json.loads(_envelope(priv).model_dump_json())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gate/pubkey"):
            return httpx.Response(503)
        return httpx.Response(200, json=body)

    client = HttpGateClient(base_url="http://gate.test")
    client._client = httpx.Client(  # noqa: SLF001
        base_url="http://gate.test", transport=httpx.MockTransport(handler)
    )
    assert client.headroom("mnd_TEST") is None


def test_the_key_is_cached_and_not_refetched_per_envelope(keypair):
    priv, pub = keypair
    gate = Gate(pub, json.loads(_envelope(priv).model_dump_json()))
    client = _client(gate)

    for _ in range(5):
        assert client.headroom("mnd_TEST") is not None
    assert gate.pubkey_fetches == 1


def test_a_rotated_gate_key_is_refetched_rather_than_rejected_forever(keypair):
    """
    The failure mode this project has already had once, in another form.

    The gate generates a new signing key when it boots without its volume, which
    is every restart on a plan with no disk — the plan the deployed instance is
    actually on. A cached key would then reject every envelope for the life of
    the merchant process: the upsell silently off, health checks green, and
    nothing on screen saying why. So a mismatch refetches the key once before it
    is called a forgery.
    """
    priv, pub = keypair
    gate = Gate(pub, json.loads(_envelope(priv).model_dump_json()))
    client = _client(gate)
    assert client.headroom("mnd_TEST") is not None
    assert gate.pubkey_fetches == 1

    new_priv, new_pub = generate_keypair()
    gate.pubkey = new_pub
    gate.body = json.loads(_envelope(new_priv).model_dump_json())

    assert client.headroom("mnd_TEST") is not None, (
        "a key rotation must not disable the merchant's upsell for good"
    )
    assert gate.pubkey_fetches == 2
