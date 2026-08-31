"""
The Razorpay path, exercised.

Before this file, `rails/razorpay/` was the largest unverified surface in the
system: written from `API_NOTES.md`, never run. Everything above the adapter was
tested against `mock_upi`, which is a different implementation and therefore
proves nothing about this one.

These tests drive the real client through an `httpx.MockTransport` that enforces
the documented rules — see `tests/fake_razorpay.py`. What is being checked is
mostly the request, not the response: that capture sends `currency` as well as
`amount`, that `receipt` fits in 40 characters, that a second capture takes the
documented 400 path rather than raising, that a `pending` refund is not treated
as money returned.

The boundary this cannot cross: it verifies the client against `API_NOTES.md`,
so a field the real API requires that the notes failed to record is still
invisible. Closing that needs a test key and nothing else will do it. It is a
much smaller gap than "never run".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from core.db import Database
from core.rail import RailAdapter
from rails.razorpay.adapter import RazorpayAdapter
from rails.razorpay.client import (
    Credentials,
    RazorpayClient,
    RazorpayError,
    idempotency_key,
    receipt_for,
)
from tests.fake_razorpay import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, FakeRazorpay

AMOUNT = 249900


@pytest.fixture
def api() -> FakeRazorpay:
    return FakeRazorpay()


@pytest.fixture
def client(api, db) -> RazorpayClient:
    return RazorpayClient(
        db,
        Credentials(key_id=KEY_ID, key_secret=KEY_SECRET, webhook_secret=WEBHOOK_SECRET),
        transport=api.transport,
    )


@pytest.fixture
def adapter(client) -> RazorpayAdapter:
    return RazorpayAdapter(client)


@pytest.fixture
def captured_payment(api, client):
    """An order, an authorised payment against it, and a capture. The state the
    refund tests start from."""
    order = client.create_order(AMOUNT, receipt="ord_ref", notes={}, idem_key="k_order")
    pid = api.authorized_payment(order["id"], AMOUNT)
    client.capture(pid, AMOUNT, "k_capture")
    return order, pid


# ------------------------------------------------------------------ setup ---


def test_the_adapter_satisfies_the_rail_protocol(adapter):
    """If this fails, adding a rail was never as cheap as claimed."""
    assert isinstance(adapter, RailAdapter)


def test_a_live_key_is_refused_at_construction(db):
    """
    A live key in a hackathon repo is a real incident, not a hypothetical. It
    has to fail at start, not at the first payment.
    """
    with pytest.raises(RuntimeError, match="not a test key"):
        RazorpayClient(db, Credentials("rzp_live_something", "secret", ""))


def test_without_credentials_it_refuses_rather_than_pretending(db):
    """
    The failure mode to avoid is a client that quietly no-ops and lets the saga
    record a settlement that never happened.
    """
    client = RazorpayClient(db, Credentials("", "", ""))
    with pytest.raises(RazorpayError) as exc:
        client.create_order(AMOUNT, "r", {}, "k")
    assert exc.value.code == "NO_CREDENTIALS"


def test_requests_carry_basic_auth_with_the_key_pair(api, client):
    client.create_order(AMOUNT, receipt="r", notes={}, idem_key="k")
    _, _, _, headers = api.seen[-1]
    decoded = base64.b64decode(headers["authorization"].removeprefix("Basic ")).decode()
    assert decoded == f"{KEY_ID}:{KEY_SECRET}"


# ------------------------------------------------------------------ order ---


def test_create_order_sends_paise_and_a_currency(api, client):
    body = client.create_order(AMOUNT, receipt="ord_1", notes={"pact_ref": "ord_1"},
                               idem_key="k1")
    method, path, sent, _ = api.seen[-1]

    assert (method, path) == ("POST", "/orders")
    # Paise, not rupees. Sending 2499 here charges a hundredth of the order.
    assert sent["amount"] == AMOUNT
    assert sent["currency"] == "INR"
    assert body["status"] == "created"
    assert body["amount_due"] == AMOUNT


def test_the_receipt_is_truncated_to_what_the_api_accepts(api, client):
    """
    40 ASCII characters, and a sha256 hex is 64. The full key stays in our
    table; only a prefix goes over the wire.
    """
    key = idempotency_key("ord_00000000", AMOUNT, 1)
    assert len(key) == 64

    client.create_order(AMOUNT, receipt=key, notes={}, idem_key=key)
    _, _, sent, _ = api.seen[-1]

    assert len(sent["receipt"]) == 40
    assert sent["receipt"] == receipt_for(key)
    assert sent["receipt"].isascii()


# ---------------------------------------------------------------- capture ---


def test_capture_sends_both_amount_and_currency(api, client):
    """
    The field people get wrong from memory. Capture is not a bare POST, and the
    fake 400s exactly as the documented API does if either is missing — so this
    assertion is enforced from both ends.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)

    result = client.capture(pid, AMOUNT, "k_c")
    method, path, sent, _ = api.seen[-1]

    assert (method, path) == ("POST", f"/payments/{pid}/capture")
    assert sent == {"amount": AMOUNT, "currency": "INR"}
    assert result["status"] == "captured"
    assert result["captured"] is True


def test_capturing_the_wrong_amount_is_refused(api, client):
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)

    with pytest.raises(RazorpayError) as exc:
        client.capture(pid, AMOUNT + 100, "k_c")
    assert exc.value.status == 400


def test_a_repeat_capture_on_a_new_key_converges_instead_of_raising(api, client):
    """
    The documented behaviour, and the one worth spelling out.

    Razorpay 400s a second capture rather than returning the original payment.
    A retry after a lost response is therefore indistinguishable from a genuine
    failure unless the client goes and looks. It does: on a 400 it fetches the
    payment, and if it is in fact captured that is a success on our side.

    Keyed deliberately with a *different* idempotency key, so the local table
    cannot short circuit it and the real path is what runs.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)
    client.capture(pid, AMOUNT, "k_first")

    again = client.capture(pid, AMOUNT, "k_second_and_different")

    assert again["status"] == "captured"
    assert again["_replayed"] is True
    assert [p for m, p, _, _ in api.seen if m == "GET"], "it never went and looked"


def test_a_400_that_is_not_an_already_captured_payment_still_raises(api, client):
    """
    The converge-on-400 path must not swallow real failures. A payment that is
    not captured has to come back as an error, or a failed capture would look
    like a successful one.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)
    api.payments[pid]["status"] = "failed"

    with pytest.raises(RazorpayError):
        client.capture(pid, AMOUNT, "k_c")


# ------------------------------------------------------------ idempotency ---


def test_the_same_key_never_reaches_the_api_twice(api, client):
    """
    The whole reason for the local table: Razorpay has no idempotency header,
    and `receipt` rejects a duplicate rather than replaying it. So the
    protection has to be ours, before the call.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)

    first = client.capture(pid, AMOUNT, "same_key")
    calls_after_first = len(api.seen)
    second = client.capture(pid, AMOUNT, "same_key")

    assert len(api.seen) == calls_after_first, "the second capture reached the API"
    assert second["id"] == first["id"]
    assert second["_replayed"] is True
    assert first.get("_replayed") is not True


def test_a_duplicate_receipt_is_an_error_not_a_replay(api, client):
    """
    Documents *why* the local table exists rather than leaning on `receipt`.
    Two different idempotency keys that happen to send the same receipt get a
    400 from the API, not the original order back.
    """
    client.create_order(AMOUNT, receipt="same_receipt", notes={}, idem_key="k1")

    with pytest.raises(RazorpayError) as exc:
        client.create_order(AMOUNT, receipt="same_receipt", notes={}, idem_key="k2")
    assert "Duplicate" in (exc.value.description or "")


# ----------------------------------------------------------------- refund ---


def test_a_refund_sends_the_amount_because_partials_need_it(api, captured_payment, client):
    _, pid = captured_payment
    client.refund(pid, AMOUNT, "k_refund")
    _, path, sent, _ = api.seen[-1]

    assert path == f"/payments/{pid}/refund"
    assert sent["amount"] == AMOUNT
    assert len(sent["receipt"]) <= 40


def test_a_pending_refund_is_reported_as_pending(api, captured_payment, adapter):
    """
    `pending` is a real state. A 200 from the refund endpoint does not mean the
    money is back, and a saga that closes the compensation here would tell a
    customer they had been refunded when they had not.
    """
    _, pid = captured_payment
    api.refund_status = "pending"

    result = adapter.refund(pid, AMOUNT, "k_refund")

    assert result.status == "pending"
    assert result.ref.startswith("rfnd_")
    # ok, because the request was accepted — but the status is what the saga
    # must read before it calls the compensation closed.
    assert result.ok is True


def test_refunding_more_than_was_captured_is_refused(api, captured_payment, adapter):
    _, pid = captured_payment
    result = adapter.refund(pid, AMOUNT * 2, "k_refund")

    assert result.ok is False
    assert result.error_detail and "refundable" in result.error_detail


# ---------------------------------------------------------------- adapter ---


def test_the_adapter_maps_an_api_error_into_a_result_not_an_exception(api, client, adapter):
    """
    The saga's rollback depends on a failed capture arriving as `ok=False`. An
    exception escaping the adapter would abort the saga mid-flight, leaving the
    reservation held and the stock consumed.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)
    api.payments[pid]["status"] = "failed"

    result = adapter.capture(pid, AMOUNT, "k_c")

    assert result.ok is False
    assert result.ref is None
    assert result.error_code


def test_status_prefers_the_captured_payment(api, captured_payment, adapter):
    """
    What the reconciliation poller reads. An order can have several payment
    attempts against it, and the captured one is the answer even when it is not
    the most recent row.
    """
    order, pid = captured_payment
    api.authorized_payment(order["id"], AMOUNT)  # a later, uncaptured attempt

    status = adapter.status(order["id"])

    assert status.status == "captured"
    assert status.payment_id == pid


def test_a_replayed_capture_survives_the_dataclass(api, client, adapter):
    """
    Regression. `RailResult` is a slots dataclass with no `__dict__`, and the
    replay path once crashed building one — on exactly the call that must not
    double charge.
    """
    order = client.create_order(AMOUNT, "r", {}, "k_o")
    pid = api.authorized_payment(order["id"], AMOUNT)

    adapter.capture(pid, AMOUNT, "k_same")
    replayed = adapter.capture(pid, AMOUNT, "k_same")

    assert replayed.ok is True
    assert replayed.replayed is True


# ------------------------------------------------------------- transport ---


def test_a_429_is_retried_rather_than_raised(api, client):
    """
    The rate limit is UNVERIFIED in API_NOTES, so an undocumented limit has to
    degrade into slowness rather than a crash.
    """
    api.fail_with = [429]
    body = client.create_order(AMOUNT, "r", {}, "k")

    assert body["status"] == "created"
    assert len(api.seen) == 2


def test_a_server_error_is_retried_and_then_gives_up(api, client):
    api.fail_with = [500, 502, 503]

    with pytest.raises(RazorpayError) as exc:
        client.create_order(AMOUNT, "r", {}, "k")

    assert exc.value.code == "TRANSPORT"
    assert len(api.seen) == 3, "bounded retry, not an infinite one"


def test_a_failed_call_is_not_remembered_as_a_success(api, client, db: Database):
    """
    A failure must leave the idempotency table alone. Cache a 500 and the retry
    after it returns the failure forever, which is worse than the outage.
    """
    api.fail_with = [500, 500, 500]
    with pytest.raises(RazorpayError):
        client.create_order(AMOUNT, "r", {}, "k_failed")

    with db.read_tx() as conn:
        rows = conn.execute(
            "SELECT 1 FROM idempotency WHERE idem_key = ?", ("k_failed",)
        ).fetchall()
    assert not rows

    api.fail_with = []
    assert client.create_order(AMOUNT, "r", {}, "k_failed")["status"] == "created"


# -------------------------------------------------------------- webhooks ---


def test_the_signature_is_hmac_over_the_raw_body(client):
    raw = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_1"}}}}'
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()

    assert client.verify_webhook(raw, signature) is True


def test_reserialising_the_body_breaks_the_signature(client):
    """
    The standard way this check silently stops working: parse the JSON, hand the
    handler a dict, re-serialise it to verify. Python's separators differ from
    the sender's and the HMAC no longer matches. The handler must verify the
    bytes it received.
    """
    raw = b'{"event": "payment.captured", "amount": 100}'
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    reserialised = json.dumps(json.loads(raw), separators=(",", ":")).encode()

    assert reserialised != raw
    assert client.verify_webhook(raw, signature) is True
    assert client.verify_webhook(reserialised, signature) is False


def test_a_tampered_body_is_rejected(client):
    raw = b'{"amount":100}'
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()

    assert client.verify_webhook(b'{"amount":900000}', signature) is False


def test_an_absent_signature_is_rejected(client):
    """
    Found by mutation: an earlier version of this file did not cover it, and a
    `verify` that returned True on an empty signature passed the whole suite.

    A delivery with no `X-Razorpay-Signature` at all is the cheapest possible
    forgery — anyone who can reach the endpoint can post a captured payment for
    an order they do not own. It must be rejected in the same breath as a wrong
    one.
    """
    raw = b'{"event":"payment.captured"}'
    assert client.verify_webhook(raw, "") is False
    assert client.verify_webhook(raw, None) is False  # type: ignore[arg-type]


def test_a_signature_from_a_different_secret_is_rejected(client):
    forged = hmac.new(b"not_the_webhook_secret", b"{}", hashlib.sha256).hexdigest()
    assert client.verify_webhook(b"{}", forged) is False


def test_no_webhook_secret_rejects_everything(db, api):
    """
    Fail closed. With no secret configured, every delivery is unverifiable, and
    an unverified webhook is an unauthenticated instruction to change an order's
    state.
    """
    client = RazorpayClient(
        db, Credentials(KEY_ID, KEY_SECRET, ""), transport=api.transport
    )
    raw = b"{}"
    assert client.verify_webhook(raw, "anything") is False
    assert client.verify_webhook(raw, "") is False
