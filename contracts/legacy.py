"""
Reading JSON that was stored under the contract's old names.

On 2026-10-21 the contract renamed `*_paise` to `*_minor`, `payee_vpa` to
`payee`, `merchant_vpa` to `merchant_payee`, and the delegator's `vpa` to
`account`, when the project took a dollar rail. `core/db.py` renames the
columns on boot. The JSON bodies inside them — mandates, quotes, decisions,
saga steps — cannot be renamed by SQL, and a body that no longer parses is an
order the console cannot show and a mandate the gate cannot read.

So every read of a stored body goes through `upgrade_keys`. Values are never
touched; only key names change, and only the ones listed here.

A mandate stored before the rename was signed over its old key names. Its
signature was verified when it was registered, which is the moment
`signature_ok` records, and the amounts it bounds are the same numbers the
principal signed. So it keeps working. It is not re-verifiable against its new
form, and nothing tries to.
"""

from __future__ import annotations

from typing import Any

RENAMED = {"payee_vpa": "payee", "merchant_vpa": "merchant_payee"}


def _key(key: str, parent: str | None) -> str:
    if key in RENAMED:
        return RENAMED[key]
    if key.endswith("_paise"):
        return key[: -len("_paise")] + "_minor"
    if key == "vpa" and parent == "delegator":
        return "account"
    return key


def upgrade_keys(value: Any, parent: str | None = None) -> Any:
    """Rename the contract's old keys anywhere in a stored JSON value."""
    if isinstance(value, dict):
        return {_key(k, parent): upgrade_keys(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [upgrade_keys(v, parent) for v in value]
    return value
