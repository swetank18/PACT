"""
Money is integer minor units — cents, paise — in the currency the profile
names. Never floats, never major units in a payload.

This module exists so that the rule has a place to be enforced rather than
merely stated. `MinorUnits` is an int alias for readability; `parse_minor` is the
only sanctioned way to accept money from outside the process, and it refuses
floats outright instead of silently rounding one.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MinorUnits = int

#: ₹10 crore, or $10 million: a ceiling that catches a stray multiplier in any
#: currency the profiles use.
MAX_MINOR = 10_000_000_00


class MoneyError(ValueError):
    """Raised when a value that should be integer minor units is not."""


def parse_minor(value: Any, *, field: str = "amount_minor") -> MinorUnits:
    """
    Accepts an int or a string of digits. Rejects floats.

    A float here is always a bug — either someone multiplied rupees by 100 in
    floating point, or a JSON payload carried 249.9 where it should have carried
    24990. Both are the class of error that ends up as a one paisa mismatch at
    the gate, so this refuses rather than rounding and hoping.
    """
    if isinstance(value, bool):
        raise MoneyError(f"{field} must be integer minor units, got a bool")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, str):
        try:
            dec = Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"{field} is not a number: {value!r}") from exc
        if dec != dec.to_integral_value():
            raise MoneyError(f"{field} must be whole minor units, got {value!r}")
        amount = int(dec)
    elif isinstance(value, float):
        raise MoneyError(
            f"{field} must be integer minor units, got a float ({value!r}). "
            "Money never travels as a float."
        )
    else:
        raise MoneyError(f"{field} must be integer minor units, got {type(value).__name__}")

    if amount < 0:
        raise MoneyError(f"{field} must not be negative, got {amount}")
    if amount > MAX_MINOR:
        raise MoneyError(f"{field} of {amount} exceeds the sanity ceiling {MAX_MINOR}")
    return amount


#: Decimal places per ISO 4217 currency, for the ones a profile may name. A
#: currency missing here is refused rather than assumed to have two: JPY has
#: none, and dividing yen by a hundred prints a price a hundred times too low.
DECIMALS: dict[str, int] = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "AUD": 2, "JPY": 0}

#: How each currency is written. INR keeps the grouping Indian readers expect.
SYMBOLS: dict[str, str] = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def _group_indian(whole: int) -> str:
    """Last three digits, then pairs: 12,45,000."""
    s = str(whole)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def format_money(minor: MinorUnits, currency: str) -> str:
    """
    Display only. Never put the output of this in a payload.

    Whole amounts drop their fraction (`₹12,45,000`, `$249`), the rest show it
    in full (`₹982.82`, `$12.50`). Rupees group the Indian way, everything else
    in thousands.
    """
    if currency not in DECIMALS:
        raise MoneyError(f"no display rule for currency {currency!r}")
    places = DECIMALS[currency]
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 10**places) if places else (abs(minor), 0)
    body = _group_indian(whole) if currency == "INR" else f"{whole:,}"
    symbol = SYMBOLS.get(currency, f"{currency} ")
    return f"{sign}{symbol}{body}" if frac == 0 else f"{sign}{symbol}{body}.{frac:0{places}d}"


def tax_at(subtotal_minor: MinorUnits, rate_bps: int) -> MinorUnits:
    """
    Tax in integer minor units, half-up rounding.

    Rate is basis points so the whole calculation stays in integers: 18% is
    1800, not 0.18. There is no floating point anywhere in the price path.
    """
    return (subtotal_minor * rate_bps + 5000) // 10000
