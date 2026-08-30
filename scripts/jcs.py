"""
RFC 8785 JSON Canonicalization Scheme — self contained implementation.

Independent of the browser implementation on purpose. Cross language parity is
only meaningful if the two sides were written separately from the spec.
"""
from __future__ import annotations

import math

# RFC 8785 §3.2.2.2 — the two-character escapes, everything else below 0x20
# becomes \u00xx lowercase hex.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _escape(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if cp in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[cp])
        elif cp < 0x20:
            out.append("\\u%04x" % cp)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(n) -> str:
    """ECMAScript Number::toString. Money is integer paise, so the integer path
    is the one that carries load; the float path is here so the function is
    honest rather than because we intend to use it."""
    if isinstance(n, bool):
        raise TypeError("bool is not a JSON number")
    if isinstance(n, int):
        if abs(n) > 2 ** 53 - 1:
            raise ValueError("integer outside IEEE-754 exact range: %d" % n)
        return str(n)
    if not math.isfinite(n):
        raise ValueError("NaN and Infinity are not valid JSON")
    if n == int(n) and abs(n) < 1e21:
        return str(int(n))
    r = repr(float(n))
    if "e" in r:  # python 1e+21 / 1e-07  ->  ES6 1e+21 / 1e-7
        mant, exp = r.split("e")
        sign = "+" if not exp.startswith("-") else "-"
        r = "%se%s%d" % (mant, sign, abs(int(exp)))
    return r


def _utf16_key(s: str) -> bytes:
    """RFC 8785 sorts object keys by UTF-16 code unit, which is exactly
    lexicographic order over the big endian UTF-16 encoding."""
    return s.encode("utf-16-be", errors="surrogatepass")


def _ser(v, out: list) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(_escape(v))
    elif isinstance(v, (int, float)):
        out.append(_number(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _ser(item, out)
        out.append("]")
    elif isinstance(v, dict):
        out.append("{")
        for i, k in enumerate(sorted(v.keys(), key=_utf16_key)):
            if not isinstance(k, str):
                raise TypeError("object keys must be strings")
            if i:
                out.append(",")
            out.append(_escape(k))
            out.append(":")
            _ser(v[k], out)
        out.append("}")
    else:
        raise TypeError("not JSON serialisable: %r" % type(v))


def canonicalize(value) -> bytes:
    out: list = []
    _ser(value, out)
    return "".join(out).encode("utf-8")
