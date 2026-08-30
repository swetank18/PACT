"""
Money is integer paise. Never floats, never rupees in a payload.

This module exists so that the rule has a place to be enforced rather than
merely stated. `Paise` is an int alias for readability; `parse_paise` is the
only sanctioned way to accept money from outside the process, and it refuses
floats outright instead of silently rounding one.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

Paise = int

MAX_PAISE = 10_000_000_00  # ₹10 crore. A ceiling that catches a stray multiplier.


class MoneyError(ValueError):
    """Raised when a value that should be integer paise is not."""


def parse_paise(value: Any, *, field: str = "amount_paise") -> Paise:
    """
    Accepts an int or a string of digits. Rejects floats.

    A float here is always a bug — either someone multiplied rupees by 100 in
    floating point, or a JSON payload carried 249.9 where it should have carried
    24990. Both are the class of error that ends up as a one paisa mismatch at
    the gate, so this refuses rather than rounding and hoping.
    """
    if isinstance(value, bool):
        raise MoneyError(f"{field} must be integer paise, got a bool")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, str):
        try:
            dec = Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"{field} is not a number: {value!r}") from exc
        if dec != dec.to_integral_value():
            raise MoneyError(f"{field} must be whole paise, got {value!r}")
        amount = int(dec)
    elif isinstance(value, float):
        raise MoneyError(
            f"{field} must be integer paise, got a float ({value!r}). "
            "Money never travels as a float."
        )
    else:
        raise MoneyError(f"{field} must be integer paise, got {type(value).__name__}")

    if amount < 0:
        raise MoneyError(f"{field} must not be negative, got {amount}")
    if amount > MAX_PAISE:
        raise MoneyError(f"{field} of {amount} exceeds the sanity ceiling {MAX_PAISE}")
    return amount


def rupees(paise: Paise) -> str:
    """Display only. Never put the output of this in a payload."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)

    # Indian grouping: last three digits, then pairs.
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])

    return f"{sign}₹{s}" if frac == 0 else f"{sign}₹{s}.{frac:02d}"


def gst(subtotal_paise: Paise, rate_bps: int) -> Paise:
    """
    Tax in integer paise, banker-free half-up rounding.

    Rate is basis points so the whole calculation stays in integers: 18% is
    1800, not 0.18. There is no floating point anywhere in the price path.
    """
    return (subtotal_paise * rate_bps + 5000) // 10000
