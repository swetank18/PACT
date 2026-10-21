#!/usr/bin/env python3
"""
Run the PayPal rail against the real sandbox, once, and say where it disagrees
with `rails/paypal/API_NOTES.md`.

    export PAYPAL_CLIENT_ID=… PAYPAL_CLIENT_SECRET=…
    python3 scripts/paypal_sandbox.py                 # vaults a buyer first if needed
    PAYPAL_VAULT_ID=… python3 scripts/paypal_sandbox.py --cents 100

Without `PAYPAL_VAULT_ID` it starts the one step that needs a person: it prints
PayPal's approve link, you log in there as a **sandbox personal account** (from
the developer dashboard, not your real one), and it waits until PayPal reports
the setup token approved. The vault id it prints is the one to export.

Then one purchase through the adapter the saga uses — authorise against the
vault, capture, read back, refund, read back — checking each answer against
what the notes say it will be. Every mismatch is printed with PayPal's
`debug_id` and the run exits non-zero. Sandbox only: the client refuses any
other host.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import Database  # noqa: E402
from rails.paypal.adapter import PayPalAdapter  # noqa: E402
from rails.paypal.client import PayPalClient, PayPalError, credentials_from_env  # noqa: E402

APPROVE_WAIT_S = 600


def vault(client: PayPalClient) -> str:
    key = uuid.uuid4().hex
    setup = client.create_setup_token(
        "https://example.com/pact/vault/approved", "https://example.com/pact/vault/cancelled", key
    )
    link = next((l["href"] for l in setup.get("links", []) if l.get("rel") == "approve"), None)
    if not link:
        sys.exit(f"setup token has no approve link: {json.dumps(setup)[:400]}")
    print(f"\n  Approve as a sandbox buyer:\n\n    {link}\n")
    print("  (PayPal then sends you to example.com, which will not load. That is fine.)")

    deadline = time.monotonic() + APPROVE_WAIT_S
    status = setup.get("status")
    while status != "APPROVED":
        if time.monotonic() > deadline:
            sys.exit(f"not approved after {APPROVE_WAIT_S}s; last status {status}")
        time.sleep(3)
        status = client.get_setup_token(setup["id"]).get("status")
    token = client.create_payment_token(setup["id"], key)
    print(f"\n  vaulted. export PAYPAL_VAULT_ID={token['id']}\n")
    return str(token["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cents", type=int, default=100, help="amount in USD cents")
    args = parser.parse_args()

    creds = credentials_from_env()
    if not creds.configured:
        sys.exit("set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox app credentials)")

    client = PayPalClient(Database("sqlite:///:memory:"), creds, currency="USD")
    vault_id = creds.vault_id or vault(client)
    adapter = PayPalAdapter(client, vault_id_for=lambda _ref: vault_id)

    ref = f"pact-sbx-{uuid.uuid4().hex[:12]}"
    wrong: list[str] = []

    def check(what: str, got: object, expected: object) -> None:
        mark = "ok " if got == expected else "XX "
        print(f"  {mark} {what}: {got!r}" + ("" if got == expected else f"  (notes say {expected!r})"))
        if got != expected:
            wrong.append(what)

    try:
        intent = adapter.create_intent(args.cents, ref=ref, idem_key=f"{ref}-1")
        print(f"\n  order {intent.intent_id}, authorization {intent.raw['payment_id']}")
        check("order status after create", intent.raw.get("order_status"), "COMPLETED")
        check("authorised amount", intent.amount_minor, args.cents)

        capture = adapter.capture(intent.raw["payment_id"], args.cents, f"{ref}-1")
        check("capture ok", capture.ok, True)
        check("capture status", capture.status, "captured")

        again = adapter.capture(intent.raw["payment_id"], args.cents, f"{ref}-fresh-key")
        check("second capture on a new key converges", (again.ok, again.ref), (True, capture.ref))

        status = adapter.status(intent.intent_id)
        check("reconciler reads it as", status.status, "captured")
        check("reconciler's payment id", status.payment_id, capture.ref)

        refund = adapter.refund(capture.ref or "", args.cents, f"{ref}-99")
        check("refund ok", refund.ok, True)
        print(f"  ·   refund status {refund.status} (completed or pending are both documented)")

        after = adapter.status(intent.intent_id)
        check("refunded amount read back", after.amount_refunded_minor,
              args.cents if refund.status == "completed" else 0)
    except PayPalError as exc:
        print(f"\n  PayPal refused: {exc.code} {exc.description} (debug_id {exc.debug_id})")
        print(f"  {json.dumps(exc.body)[:800]}")
        return 1

    print("\n  all as the notes say" if not wrong else f"\n  {len(wrong)} disagreement(s): {wrong}")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
