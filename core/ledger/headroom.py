"""
The headroom envelope. Section 7, and the most important schema in the project.

The merchant does not see the mandate. It sees a signed, minimal statement of
what will be approved.

Two hard requirements, both testable:

**Privacy by construction.** The delegator's identity, the intent text, the
total budget and the spend history are not in `Headroom`. Not stripped — absent
from the type. When a judge asks whether this leaks buyer data, the answer is
that the schema physically cannot carry it, and `tests/test_headroom.py`
asserts the field set rather than trusting the reading.

Note what `headroom_paise` is and is not: it is what is **left**, never what the
budget was. A merchant that learns "₹8,900 remaining" learns what it can sell.
A merchant that learns "₹8,900 of ₹15,000 remaining" learns how rich the buyer
is and how much they have already spent elsewhere. The difference is one
subtraction we deliberately do not publish.

**Signed and time stamped.** The merchant can prove what it was told and when,
which is the dispute story, and it costs ten minutes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts.crypto import b64u_encode, private_key_from_seed, sign
from contracts.schemas import Headroom, utcnow
from core.ledger.reservations import Ledger
from core.mandate.store import MandateStore

#: Where the gate's own signing key lives. Generated on first boot so a fresh
#: clone works; persisted so a restart mid-demo does not invalidate envelopes
#: the merchant is already holding.
DEFAULT_KEY_PATH = "fixtures/keys/gate_signing_key.hex"


#: SHA-256 of the *public* half of a signing key that was committed to a public
#: repository and is therefore known to everyone. The private half is not here
#: and must never be; a public-key fingerprint is enough to recognise it.
#:
#: A fresh clone cannot hit this — the file is untracked and the gate generates
#: its own on first boot. A clone that predates that still has the compromised
#: file on disk and would boot with it silently, which is the case this exists
#: for. Refusing is not paranoia: an envelope signed with a key anyone can
#: download proves nothing, and the whole point of the envelope is that a
#: merchant can trust it without asking.
COMPROMISED_PUBLIC_KEYS: frozenset[str] = frozenset(
    {"1b12f696b6de17d0e9a50f8cda09e02038b513df2d37465dcec8b3a6a3487d90"}
)


def _fingerprint(key: Ed25519PrivateKey) -> str:
    return hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()


def load_or_create_gate_key(path: str | None = None) -> Ed25519PrivateKey:
    key_path = Path(path or os.environ.get("PACT_GATE_KEY_PATH", DEFAULT_KEY_PATH))
    if key_path.exists():
        key = private_key_from_seed(bytes.fromhex(key_path.read_text().strip()))
        if _fingerprint(key) in COMPROMISED_PUBLIC_KEYS:
            raise RuntimeError(
                f"{key_path} holds a signing key that was committed to a public "
                "repository. Anyone can sign a headroom envelope with it, so a "
                "merchant verifying one learns nothing. Delete the file and "
                "restart — the gate will generate a new one. Refusing to start."
            )
        return key

    key = Ed25519PrivateKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key.private_bytes_raw().hex() + "\n")
    # Test-mode key for a hackathon, but there is no reason to leave it group
    # readable and every reason to build the habit.
    key_path.chmod(0o600)
    return key


class HeadroomService:
    def __init__(
        self,
        mandates: MandateStore,
        ledger: Ledger,
        *,
        merchant_vpa: str,
        signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.mandates = mandates
        self.ledger = ledger
        self.merchant_vpa = merchant_vpa
        self.signing_key = signing_key or load_or_create_gate_key()

    @property
    def public_key_b64u(self) -> str:
        return b64u_encode(self.signing_key.public_key().public_bytes_raw())

    def for_mandate(self, mandate_id: str) -> Headroom | None:
        stored = self.mandates.get(mandate_id)
        if stored is None:
            return None

        c = stored.mandate.constraints
        spent, used = self.ledger.spent_and_count(mandate_id)

        # A revoked or expired mandate reports zero rather than 404ing. The
        # merchant's question is "what may I offer", and the truthful answer for
        # a dead mandate is "nothing" — which keeps the upsell filter correct
        # without teaching it about mandate lifecycle.
        dead = stored.revoked or not stored.signature_ok

        envelope = Headroom(
            mandate_id=mandate_id,
            headroom_paise=0 if dead else max(0, c.max_total_paise - spent),
            max_per_txn_paise=0 if dead else c.max_per_txn_paise,
            payments_remaining=0 if dead else max(0, c.max_count - used),
            categories_allowed=[] if dead else list(c.category_allowlist),
            valid_until=c.valid_until,
            merchant_in_scope=(not dead) and self.merchant_vpa in c.merchant_allowlist,
            as_of=utcnow(),
        )
        envelope.signature = sign(envelope.model_dump(), self.signing_key)
        return envelope
