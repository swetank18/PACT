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


# --------------------------------------------------------------- the docs ---


def test_every_relative_link_in_the_docs_resolves():
    """
    A link in a README that points at nothing.

    Cheap to break and invisible until somebody follows it — `docs/RUNBOOK.md`
    shipped pointing at `eval/results.md`, which is one directory deeper than
    that. The documentation here carries the claims and the reading order, so a
    dead link is the reader losing the thread at exactly the moment they went
    looking for evidence.

    Anchors are stripped rather than checked: a heading that moves is a much
    smaller problem than a file that is not there, and checking them would mean
    parsing every heading in every file to a slug, which is a second thing to
    get wrong.
    """
    import re

    skip = {".git", ".venv", "node_modules", "dist", "_private", "__pycache__"}
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

    dead: list[str] = []
    for path in REPO.rglob("*.md"):
        if any(part in skip for part in path.parts):
            continue
        for target in link.findall(path.read_text()):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                dead.append(f"{path.relative_to(REPO)} -> {target}")

    assert not dead, "dead links: " + ", ".join(dead)


#: Only the range this project's prose actually reaches. A wider table would be
#: pretending to a generality nothing needs.
_WORDS = {"eight": 8, "nine": 9, "ten": 10, "eleven": 11}


def test_everything_that_states_how_many_checks_there_are_agrees_with_the_gate():
    """
    `CHECK_ORDER` is the gate. Everything else is a claim about it.

    They disagreed. checks.py numbered them 1-9 with quote_binding as "8b" and
    called it "the nine checks"; six other places repeated the nine; and
    CHECK_ORDER held ten entries while the firewall drawer printed "10 checks
    ran, 0 skipped" on screen, in the recorded walkthrough, next to a slide
    saying nine.

    The count is the first thing anyone reading the design counts and the
    cheapest claim in the project to check, so a stale one reads exactly like a
    careless one. Asserted against `len(CHECK_ORDER)` rather than against a
    literal, so adding a check moves every claim or fails here.
    """
    total = len(CHECK_ORDER)

    #: (file, pattern capturing the count, what it is). Prose is spelled out and
    #: the two UI strings are digits, so both spellings are accepted.
    claims = [
        ("core/gate/checks.py", r"The (\w+) checks\."),
        ("contracts/reason_codes.py", r"The (\w+) checks, in the order they run"),
        ("core/gate/auditor.py", r"The intent auditor\. Check (\w+),"),
        ("buyer/agent.py", r"the full (\w+) checks"),
        ("console/src/lib/api.ts", r"The (\w+) checks\. Returns a decision"),
        ("console/src/surfaces/slides/Slides.tsx", r"signed authorize → (\d+) checks"),
        ("scripts/gen_hacksummit_deck.py", r"across its (\w+) checks"),
        ("scripts/gen_hacksummit_deck.py", r"The gate and its (\w+) checks"),
        ("docs/RUNBOOK.md", r"a decision, (\w+) checks in order"),
        # Burned into the backup video's captions, where it is the least
        # correctable of the lot: fixing it means re-recording the take.
        ("console/demo-video.mjs", r"cold and pays\. (\w+) checks"),
    ]

    wrong = []
    for path, pattern in claims:
        text = (REPO / path).read_text(encoding="utf-8")
        found = re.search(pattern, text)
        assert found is not None, (
            f"{path}: the line this asserts on is gone. Either restore it or drop "
            f"the claim here — pattern was {pattern!r}"
        )
        raw = found.group(1)
        stated = _WORDS.get(raw.lower(), None) if not raw.isdigit() else int(raw)
        assert stated is not None, f"{path}: cannot read {raw!r} as a number"
        if stated != total:
            wrong.append(f"{path} says {raw}")

    assert not wrong, (
        f"CHECK_ORDER has {total} entries but " + ", ".join(wrong) + ". Update them."
    )

    # The auditor is last for a reason — it is the only check that leaves the
    # process — and "check ten" is only true while it stays there.
    assert CHECK_ORDER[-1] == "intent", (
        "the intent auditor is no longer last, so core/gate/auditor.py calling "
        "itself the last check is now false"
    )


def test_the_documented_test_counts_are_the_real_ones(request):
    """
    A test count written into prose, and then not updated.

    On 2026-09-07 the README said 185 in one place, HANDOFF said 186, and the CI
    job name said 114, against a suite that collects 189. The console count was
    52 in the README and 55 in HANDOFF. Nothing was wrong with the suite: the
    number simply lives in five files and had been updated in fewer than five.

    It is worth asserting because of who reads it. "188 tests, all green" is the
    first claim a judge can check and the cheapest one to check — run the suite
    and count — so a number that is merely old reads exactly like a number that
    was inflated.

    What is asserted is what pytest **collects**, not what passes: one test in
    `test_headroom.py` skips unless the clone predates the key purge, so the
    passing count differs between machines and asserting on it would fail here
    on some of them and not others.

    The console count cannot be collected from Python without running vitest, so
    the three places that state it are asserted to agree with *each other* —
    which is the drift that actually happened.
    """
    collected = {item.location[0].replace("\\", "/") for item in request.session.items}
    on_disk = {f"tests/{p.name}" for p in (REPO / "tests").glob("test_*.py")}
    if collected != on_disk:
        pytest.skip("only part of the suite was collected; this needs ./scripts/test.sh")

    total = len(request.session.items)
    readme = (REPO / "README.md").read_text()
    handoff = (REPO / "HANDOFF.md").read_text()
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    deck = (REPO / "scripts" / "gen_hacksummit_deck.py").read_text()
    explainer = (REPO / "scripts" / "gen_explainer_deck.py").read_text()

    def stated(where: str, text: str, pattern: str) -> int:
        found = re.search(pattern, text)
        assert found is not None, (
            f"{where}: the line this asserts on is gone. Either restore it or drop "
            f"the claim here — pattern was {pattern!r}"
        )
        return int(found.group(1))

    both = r"(\d+)\s+Python\s+tests,\s+(\d+)\s+console\s+tests"
    python_claims = {
        "README.md, the quickstart": stated(
            "README.md", readme, r"scripts/test\.sh\s+#\s*(\d+)\s+tests"
        ),
        "README.md, what CI proves": stated("README.md", readme, both),
        "HANDOFF.md, section 1": stated("HANDOFF.md", handoff, both),
        "ci.yml, the job name": stated("ci.yml", ci, r"name:\s+python\s+\((\d+)\s+tests"),
        # The submitted deck is generated, and the reason it is generated is so
        # a number that moves in the repository can be carried into it without
        # opening PowerPoint. That only holds if something notices when one
        # does. This is the artifact a judge actually reads.
        "gen_hacksummit_deck.py, the proof chip": stated(
            "gen_hacksummit_deck.py", deck, r'"(\d+)\s+\+\s+\d+\s+tests'
        ),
        "gen_hacksummit_deck.py, the CI slide": stated(
            "gen_hacksummit_deck.py", deck, both
        ),
        "gen_explainer_deck.py, the evidence slide": stated(
            "gen_explainer_deck.py", explainer, r"tests — (\d+) pytest"
        ),
    }
    wrong = [f"{where} says {n}" for where, n in python_claims.items() if n != total]
    assert not wrong, (
        f"the suite collects {total} tests but " + ", ".join(wrong) + ". Update them."
    )

    console_claims = {
        "README.md": int(re.search(both, readme).group(2)),  # type: ignore[union-attr]
        "HANDOFF.md": int(re.search(both, handoff).group(2)),  # type: ignore[union-attr]
        "ci.yml": stated("ci.yml", ci, r"name:\s+console\s+\((\d+)\s+tests"),
        "gen_hacksummit_deck.py": int(re.search(both, deck).group(2)),  # type: ignore[union-attr]
        "gen_hacksummit_deck.py, the proof chip": int(
            re.search(r'"\d+\s+\+\s+(\d+)\s+tests', deck).group(1)  # type: ignore[union-attr]
        ),
        "gen_explainer_deck.py": int(
            re.search(r"tests — \d+ pytest, (\d+) vitest", explainer).group(1)  # type: ignore[union-attr]
        ),
    }
    assert len(set(console_claims.values())) == 1, (
        "the console test count disagrees with itself: "
        + ", ".join(f"{where} says {n}" for where, n in console_claims.items())
    )

    # gen_explainer_deck.py prints a combined figure on two slides. It is the
    # sum, so it drifts twice as easily and reads as the most confident number
    # on the page.
    combined = total + next(iter(console_claims.values()))
    for found in re.finditer(r'\("(\d+)", "tests', explainer):
        assert int(found.group(1)) == combined, (
            f"gen_explainer_deck.py says {found.group(1)} tests but the suites "
            f"collect {total} + {next(iter(console_claims.values()))} = {combined}"
        )


# ------------------------------------------------------- the console build ---


def test_every_console_import_that_escapes_console_is_copied_into_the_image():
    """
    An import the console makes outside its own directory, with no `COPY` for it.

    The Dockerfile builds the console in a stage that holds `console/` and
    whatever else is copied in by name, and the layout is mirrored so a relative
    import like `../../../contracts/generated` resolves. Add a second one — the
    firewall tab's `../../../../eval/results/raw.json` — without adding the
    `COPY`, and nothing local complains: `npm run build` runs at the repo root,
    where the whole tree is present. The container build is the only thing that
    sees the difference, and it fails at the registry push, three minutes after
    the commit that broke it.

    The Dockerfile's own comment predicted this exact failure and it happened
    anyway, which is the argument for a test rather than a comment.
    """
    console_src = REPO / "console" / "src"
    dockerfile = (REPO / "Dockerfile").read_text()

    # The console build stage only, so a COPY into the runtime stage does not
    # satisfy an import that has to resolve at `vite build` time.
    stage = dockerfile.split("AS console", 1)[1].split("\nFROM ", 1)[0]
    copied = [
        line.split()[1]
        for line in stage.splitlines()
        if line.startswith("COPY ") and not line.startswith("COPY --from")
    ]

    # `import x from "../../foo"` and `from "../../foo"` alike.
    spec = re.compile(r"""from\s+["']((?:\.\./)+[^"']+)["']""")

    missing: list[str] = []
    for path in list(console_src.rglob("*.ts")) + list(console_src.rglob("*.tsx")):
        for raw in spec.findall(path.read_text()):
            target = (path.parent / raw).resolve()
            try:
                rel = target.relative_to(REPO)
            except ValueError:
                continue  # Not under the repo at all; npm's problem, not ours.
            if rel.parts[0] == "console":
                continue  # Still inside the stage's own tree.

            # TypeScript omits the extension; the COPY cannot.
            candidates = {str(rel), *(f"{rel}{ext}" for ext in (".ts", ".tsx", ".json"))}
            if any(
                c == src or c.startswith(src.rstrip("/") + "/")
                for c in candidates
                for src in copied
            ):
                continue
            missing.append(f"{path.relative_to(REPO)} imports {raw}")

    assert not missing, (
        "these reach outside console/ but nothing copies them into the console "
        "build stage, so the image builds without them: " + "; ".join(sorted(set(missing)))
    )
