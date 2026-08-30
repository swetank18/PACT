"""
Prefixed, sortable ids. Section 5 of the shared contract freezes the prefixes.

ULID shaped: 10 characters of Crockford base32 timestamp followed by 16 of
randomness. Lexicographic order is time order, which means `ORDER BY id` in the
audit store is chronological without a second index, and a human reading two ids
on screen can tell which came first.
"""

from __future__ import annotations

import os
import time
from typing import Literal

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

Prefix = Literal["mnd", "dec", "qte", "ord", "stl", "sim", "rsv", "evt"]

_TIME_CHARS = 10
_RANDOM_CHARS = 16


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: Prefix) -> str:
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return f"{prefix}_{_encode(ms, _TIME_CHARS)}{_encode(rand, _RANDOM_CHARS)}"


def has_prefix(value: str, prefix: Prefix) -> bool:
    return isinstance(value, str) and value.startswith(f"{prefix}_")
