#!/usr/bin/env python3
"""
Generates fixtures/keys/test_vector.json — the cross language signature vector.

Per 00-SHARED-CONTRACTS.md section 6 this file is Lane A's to publish. Lane C
generated this one so that browser parity could be proven at hour 0 instead of
hour 3. When Lane A publishes theirs it overwrites this file and the console's
parity test picks it up with no code change.

Signing procedure, identical in Python and the browser:
  strip `signature` -> RFC 8785 JCS -> Ed25519 -> base64url unpadded.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jcs import canonicalize  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "keys" / "test_vector.json"

# Fixed seeds so the vector is byte reproducible. Test keys, never real ones.
DELEGATOR_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
DELEGATE_SEED = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"
)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sign(payload: dict, seed: bytes) -> str:
    unsigned = {k: v for k, v in payload.items() if k != "signature"}
    return b64u(Ed25519PrivateKey.from_private_bytes(seed).sign(canonicalize(unsigned)))


def main() -> int:
    delegator = Ed25519PrivateKey.from_private_bytes(DELEGATOR_SEED)
    delegate = Ed25519PrivateKey.from_private_bytes(DELEGATE_SEED)

    delegator_pub = b64u(delegator.public_key().public_bytes_raw())
    delegate_pub = b64u(delegate.public_key().public_bytes_raw())

    mandate = {
        "v": 1,
        "mandate_id": "mnd_01J8XQ4K7T",
        "delegator": {"vpa": "swetank@okaxis", "pubkey": delegator_pub},
        "delegate": {"agent_id": "buyer_agent_v1", "pubkey": delegate_pub},
        "intent": "restock office supplies for the month",
        "constraints": {
            "max_per_txn_paise": 500000,
            "max_total_paise": 1500000,
            "max_count": 5,
            "merchant_allowlist": ["deskkit@razorpay"],
            "category_allowlist": ["stationery", "office_furniture", "cables"],
            "valid_from": "2026-08-30T10:00:00Z",
            "valid_until": "2026-08-31T10:00:00Z",
        },
        "issued_at": "2026-08-30T09:58:12Z",
    }

    # A second case chosen to break naive canonicalisers: non-ASCII keys and
    # values, a key ordering that differs from insertion order, an empty
    # object, an empty array, and a string needing control-char escapes.
    unicode_case = {
        "z_last": None,
        "éclair": "café — ₹ 1,299",
        "A": [],
        "🚀_rocket": {"nested": {"deep": True}},
        "a": {},
        "tab\there": "line\nbreak",
        "num": 1234567890,
        "neg": -42,
    }

    vector = {
        "_note": (
            "Signing procedure: strip `signature`, canonicalise with RFC 8785 JCS, "
            "sign with Ed25519, encode base64url unpadded. Test keys only."
        ),
        "_generated_by": "scripts/gen_test_vector.py (Lane C placeholder, Lane A may overwrite)",
        "algorithm": "Ed25519",
        "canonicalization": "RFC8785",
        "encoding": "base64url-unpadded",
        "keys": {
            "delegator": {
                "seed_hex": DELEGATOR_SEED.hex(),
                "private_key_b64u": b64u(DELEGATOR_SEED),
                "public_key_b64u": delegator_pub,
            },
            "delegate": {
                "seed_hex": DELEGATE_SEED.hex(),
                "private_key_b64u": b64u(DELEGATE_SEED),
                "public_key_b64u": delegate_pub,
            },
        },
        "cases": [
            {
                "name": "mandate",
                "signer": "delegator",
                "payload": mandate,
                "canonical": canonicalize(mandate).decode("utf-8"),
                "signature": sign(mandate, DELEGATOR_SEED),
            },
            {
                "name": "unicode_and_ordering",
                "signer": "delegate",
                "payload": unicode_case,
                "canonical": canonicalize(unicode_case).decode("utf-8"),
                "signature": sign(unicode_case, DELEGATE_SEED),
            },
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vector, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote %s" % OUT)
    for c in vector["cases"]:
        print("  %-22s %s" % (c["name"], c["signature"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
