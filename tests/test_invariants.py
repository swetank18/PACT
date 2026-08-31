"""
The invariants that hold everywhere: layering, money, determinism, fail closed.

These are the tests that stop a system decaying at hour 20, when someone imports
a rail into a check "just to read the payment id" and nobody notices in review.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from contracts.money import MoneyError, gst, parse_paise, rupees
from contracts.reason_codes import CHECK_ORDER, REASON_TEXT, ReasonCode, Verdict, verdict_for
from contracts.schemas import QuoteItemRequest

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- layering ---


def _imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_nothing_in_core_imports_a_rail():
    """
    **The rule that makes this work.**

    The gate decides on authority. A rail moves money. If a check needs to know
    which rail it is on, the design is wrong. This is a real grep rather than a
    convention, because at hour 20 it will get violated otherwise.
    """
    offenders: list[str] = []
    for path in (REPO / "core").rglob("*.py"):
        for name in _imports_in(path):
            if name == "rails" or name.startswith("rails."):
                offenders.append(f"{path.relative_to(REPO)} imports {name}")
    assert not offenders, "core must not import rails:\n  " + "\n  ".join(offenders)


#: The contract used to carry `RAZORPAY_CAPTURE_FAILED`, which baked a vendor
#: name into the enum Lane B asserts on and Lane C colours by. It is now
#: `RAIL_CAPTURE_FAILED` and this set is empty. It stays as a set rather than
#: being deleted so that adding to it is a visible, deliberate act with a
#: comment attached, instead of a quiet edit to an assertion.
KNOWN_VENDOR_NAMES_IN_CONTRACT: set[str] = set()

VENDORS = ("razorpay", "stripe", "adyen")


def _executable_source(path: Path) -> str:
    """
    The file with comments and docstrings removed.

    Prose in `core/` may reference a rail to explain a design decision — saying
    "Razorpay captures payments, not orders" is exactly the kind of note that
    stops the next person getting it wrong. What must not happen is *code* in
    the rail-agnostic layer that depends on a specific vendor.
    """
    import io
    import tokenize

    out: list[str] = []
    with path.open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    prev_type = tokenize.INDENT
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        # A string that is the whole statement is a docstring.
        if tok.type == tokenize.STRING and prev_type in (
            tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL, tokenize.ENCODING,
        ):
            prev_type = tok.type
            continue
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(tok.string)
        prev_type = tok.type
    return " ".join(out)


def test_no_vendor_name_appears_in_rail_agnostic_code():
    """
    The import rule with the loophole closed. `core/` and `contracts/` may
    *mention* a rail in prose, but no executable line may name one — a string
    literal comparing against "razorpay" would pass the import check and still
    couple the gate to a vendor.
    """
    offenders: list[str] = []
    for directory in ("core", "contracts"):
        for path in (REPO / directory).rglob("*.py"):
            source = _executable_source(path)
            for allowed in KNOWN_VENDOR_NAMES_IN_CONTRACT:
                source = source.replace(allowed, "")
            for vendor in VENDORS:
                if vendor in source.lower():
                    offenders.append(f"{path.relative_to(REPO)} names {vendor} in code")
    assert not offenders, "\n  ".join(["vendor coupling in the rail-agnostic layer:"] + offenders)


def test_no_reason_code_names_a_vendor():
    """
    A reason code is the thing two other lanes branch on and the audience reads
    off a screen. A vendor name in one leaks the rail into the contract, and it
    is the kind of leak nobody can undo later without breaking both consumers.
    """
    named = {c.value for c in ReasonCode if any(v in c.value.lower() for v in VENDORS)}
    assert named == KNOWN_VENDOR_NAMES_IN_CONTRACT, (
        f"vendor-named reason codes: {sorted(named)}"
    )


#: Codes the engine defines but does not itself raise. Empty, and it should stay
#: that way — see the test below for why.
UNPRODUCED_CODES: set[str] = set()


def test_every_reason_code_is_actually_produced():
    """
    A code nothing emits is worse than a missing code.

    Lane C has a branch for every code, Lane B asserts on them, and the audit
    trail is the artefact the whole trust story rests on. A code that only
    exists in the enum is a branch that has never rendered and an assertion that
    can never fire — and it hides a real gap: for three releases STOCK_UNAVAILABLE,
    the capture failure and SAGA_ROLLED_BACK were declared here while the saga
    wrote English prose into `detail` and no code at all. The trail said what
    happened in a sentence, and the contract said nothing.
    """
    produced: set[str] = set()
    for directory in ("core", "merchant", "rails"):
        for path in (REPO / directory).rglob("*.py"):
            # _executable_source returns tokens separated by spaces, so an
            # attribute access arrives as "ReasonCode . CEILING_TOTAL". Collapse
            # the whitespace before looking for it.
            source = re.sub(r"\s+", "", _executable_source(path))
            for code in ReasonCode:
                if f"ReasonCode.{code.name}" in source:
                    produced.add(code.value)

    missing = {c.value for c in ReasonCode} - produced - {ReasonCode.OK.value}
    assert missing == UNPRODUCED_CODES, (
        "reason codes declared but never emitted by the engine: "
        f"{sorted(missing - UNPRODUCED_CODES)}"
    )


def test_contracts_does_not_import_core_merchant_or_rails():
    """Contracts is the bottom of the stack. Everything may import it; it
    imports nothing of ours."""
    offenders: list[str] = []
    for path in (REPO / "contracts").rglob("*.py"):
        for name in _imports_in(path):
            if name.split(".")[0] in {"core", "merchant", "rails"}:
                offenders.append(f"{path.relative_to(REPO)} imports {name}")
    assert not offenders, "\n".join(offenders)


# ------------------------------------------------------------------ money ---


def test_money_refuses_floats():
    """A float here is always a bug: either rupees times a hundred in floating
    point, or 249.9 where 24990 was meant."""
    with pytest.raises(MoneyError):
        parse_paise(249.9)
    with pytest.raises(MoneyError):
        parse_paise(100.0)  # even a clean float is refused, deliberately


def test_money_refuses_negatives_bools_and_absurd_values():
    with pytest.raises(MoneyError):
        parse_paise(-1)
    with pytest.raises(MoneyError):
        parse_paise(True)
    with pytest.raises(MoneyError):
        parse_paise(10_000_000_00 + 1)


def test_money_accepts_ints_and_integral_strings():
    assert parse_paise(24990) == 24990
    assert parse_paise("24990") == 24990
    with pytest.raises(MoneyError):
        parse_paise("249.90")


def test_tax_is_integer_arithmetic_with_no_float_anywhere():
    # 18% of 74900 paise is 13482.0 exactly; the point is the type, not the value.
    assert gst(74900, 1800) == 13482
    assert isinstance(gst(74901, 1800), int)


def test_indian_grouping():
    assert rupees(12_45_000_00) == "₹12,45,000"
    assert rupees(98282) == "₹982.82"
    assert rupees(0) == "₹0"


# ------------------------------------------------------------ reason codes ---


def test_every_reason_code_has_a_human_string():
    missing = [c for c in ReasonCode if c not in REASON_TEXT]
    assert not missing, f"no display string for {missing}"


def test_only_probabilistic_signals_step_up():
    """
    A step up is for signals that might be wrong. Everything deterministic
    blocks. If a deterministic check ever starts stepping up, the gate has
    become advisory.
    """
    assert verdict_for(ReasonCode.INTENT_MISMATCH) is Verdict.STEP_UP
    assert verdict_for(ReasonCode.AUDITOR_UNAVAILABLE) is Verdict.STEP_UP
    for code in (
        ReasonCode.NONCE_REPLAY,
        ReasonCode.CEILING_TOTAL,
        ReasonCode.QUOTE_AMOUNT_MISMATCH,
        ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED,
        ReasonCode.REQUEST_SIG_INVALID,
        ReasonCode.INTENT_INJECTION_SUSPECTED,
    ):
        assert verdict_for(code) is Verdict.BLOCK, f"{code} must block, not step up"


def test_the_check_order_is_the_frozen_one():
    assert CHECK_ORDER == (
        "request_signature",
        "mandate_signature",
        "mandate_state",
        "validity_window",
        "freshness",
        "replay",
        "scope",
        "ceiling",
        "quote_binding",
        "intent",
    )


# --------------------------------------------------------------- determinism --


def test_the_quote_engine_is_deterministic_across_a_hundred_runs(quotes):
    items = [QuoteItemRequest(sku="STA-NB-A5", qty=2), QuoteItemRequest(sku="CBL-USBC-2M")]
    first = quotes.price(items)[1:]  # everything but the line objects
    for _ in range(100):
        assert quotes.price(items)[1:] == first


def test_quote_totals_add_up(quotes):
    _, subtotal, tax, shipping, total = quotes.price(
        [QuoteItemRequest(sku="STA-NB-A5", qty=3)]
    )
    assert total == subtotal + tax + shipping
    assert all(isinstance(v, int) for v in (subtotal, tax, shipping, total))


def test_free_shipping_threshold_is_applied_at_the_boundary(quotes):
    # Under the threshold pays shipping; over it does not.
    cheap = quotes.price([QuoteItemRequest(sku="STA-STK-01")])
    rich = quotes.price([QuoteItemRequest(sku="FUR-CHR-ERG")])
    assert cheap[3] > 0
    assert rich[3] == 0


# ------------------------------------------------------------- fail closed ---


def test_a_check_that_raises_becomes_a_block(gate, make_mandate, quotes, authorize, monkeypatch):
    """
    A gate that crashes open is worse than no gate. An unexpected exception
    inside a check must become a refusal, not an approval.
    """
    import core.gate.checks as checks

    mandate = make_mandate()
    q = quotes.build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)

    def boom(_ctx):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(checks, "check_scope", boom)
    monkeypatch.setattr(gate, "_runner", lambda name: boom if name == "scope" else gate.__class__._runner(gate, name))

    d = gate.authorize(
        __import__("contracts.schemas", fromlist=["AuthorizeRequest"]).AuthorizeRequest(
            mandate_id=mandate.mandate_id,
            quote_id=q.quote_id,
            amount_paise=q.total_paise,
            payee_vpa="deskkit@razorpay",
            nonce="nonce-for-the-raising-check",
            issued_at=__import__("contracts.schemas", fromlist=["utcnow"]).utcnow(),
        )
    )
    assert d.verdict is not Verdict.ALLOW


def test_garbage_input_never_allows(gate):
    """Fuzz the boundary. Nothing malformed may produce an ALLOW."""
    from contracts.schemas import AuthorizeRequest, utcnow

    hostile = [
        {"mandate_id": "", "quote_id": "", "amount_paise": 0},
        {"mandate_id": "../../etc/passwd", "quote_id": "x", "amount_paise": 1},
        {"mandate_id": "mnd_" + "A" * 5000, "quote_id": "y", "amount_paise": 1},
        {"mandate_id": "mnd_x", "quote_id": "'; DROP TABLE mandates;--", "amount_paise": 1},
    ]
    for payload in hostile:
        req = AuthorizeRequest(
            payee_vpa="deskkit@razorpay",
            nonce=f"n{payload['mandate_id'][:20]}{payload['quote_id'][:10]}",
            issued_at=utcnow(),
            **payload,
        )
        assert gate.authorize(req).verdict is not Verdict.ALLOW


def test_the_mandates_table_still_exists_after_an_injection_attempt(gate, make_mandate):
    """Parameterised SQL everywhere; this asserts it rather than assuming it."""
    make_mandate()
    from contracts.schemas import AuthorizeRequest, utcnow

    gate.authorize(
        AuthorizeRequest(
            mandate_id="'; DROP TABLE mandates;--",
            quote_id="x",
            amount_paise=1,
            payee_vpa="deskkit@razorpay",
            nonce="sqli-probe",
            issued_at=utcnow(),
        )
    )
    with gate.db.read_tx() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM mandates").fetchone()["n"] >= 1


# ------------------------------------------------- generated TS contracts ---


def test_the_generated_typescript_contracts_are_current():
    """
    `contracts/generated.ts` is produced from the Python enum and committed, so
    a reviewer sees the diff when a code changes. This asserts nobody edited the
    Python without rerunning the generator — a drift there is not a compile
    error, it is a console rendering a code it does not recognise on stage.
    """
    import subprocess
    import sys

    generated = REPO / "contracts" / "generated.ts"
    before = generated.read_text()
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "gen_ts_contracts.py")],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    after = generated.read_text()
    assert before == after, (
        "contracts/generated.ts is stale. Run: python3 scripts/gen_ts_contracts.py"
    )


def test_every_reason_code_reached_the_typescript_side():
    generated = (REPO / "contracts" / "generated.ts").read_text()
    for code in ReasonCode:
        assert f'"{code.value}"' in generated, f"{code.value} is missing from generated.ts"
