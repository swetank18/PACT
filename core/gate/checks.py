"""
The nine checks.

Ordered cheapest and most certain first, short circuiting, each timed. The
order is frozen in `contracts.reason_codes.CHECK_ORDER` and is the design:

  1. request_signature   Ed25519 by the delegate pubkey
  2. mandate_signature   verified at registration, re-asserted here
  3. mandate_state       exists, not revoked
  4. validity_window
  5. freshness           within 60s clock skew
  6. replay              nonce INSERT; the uniqueness violation IS the replay
  7. scope               merchant allowlist and category allowlist
  8. ceiling             atomic reservation
  8b. quote_binding      the amount must equal the quote it references
  9. intent              the model auditor, last because it is the only
                         network call

Each check is a plain function of a context. They do not know about HTTP, they
do not know about rails, and they return a code rather than raising, so the
engine can time them uniformly and fail closed on anything unexpected.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from contracts.reason_codes import ReasonCode
from contracts.schemas import (
    AuthorizeRequest,
    InjectedSpan,
    Mandate,
    parse_rfc3339,
    utcnow,
)
from core.ledger.reservations import Ledger
from core.mandate.store import StoredMandate

#: Section 9. A request older than this is stale even if perfectly signed,
#: which is what stops a captured request being useful an hour later.
FRESHNESS_WINDOW_SECONDS = 60


@dataclass(slots=True)
class CheckContext:
    request: AuthorizeRequest
    #: Minted by the engine before the chain runs, so the reservation the
    #: ceiling check takes is keyed to the decision that will be recorded.
    decision_id: str
    stored: StoredMandate | None
    ledger: Ledger
    conn_factory: Callable[[], sqlite3.Connection]
    db: object  # core.db.Database; typed loosely to keep this module rail free
    merchant_vpa: str
    quote: dict | None = None
    #: Set by the ceiling check so the engine can hand it to the token issuer.
    reservation_id: str | None = None
    headroom_paise: int = 0

    @property
    def mandate(self) -> Mandate | None:
        return self.stored.mandate if self.stored else None


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    status: str  # PASS | FAIL | STEP_UP
    reason_code: ReasonCode = ReasonCode.OK
    detail: str = ""
    injected_span: InjectedSpan | None = None


PASS = CheckOutcome(status="PASS")


def _fail(code: ReasonCode, detail: str = "") -> CheckOutcome:
    return CheckOutcome(status="FAIL", reason_code=code, detail=detail)


# --------------------------------------------------------------------- 1 ---


def check_request_signature(ctx: CheckContext) -> CheckOutcome:
    """
    The request must be signed by the key the mandate names as the delegate.

    First because it is the cheapest thing that is also the most certain: an
    unsigned or wrongly signed request is not worth a database round trip.
    """
    from contracts.crypto import verify  # local import keeps the hot path lean

    if ctx.stored is None:
        # We cannot check a signature without the mandate's delegate key. Say
        # what is actually wrong rather than blaming the signature.
        return _fail(ReasonCode.MANDATE_NOT_FOUND, ctx.request.mandate_id)
    if not ctx.request.signature:
        return _fail(ReasonCode.REQUEST_SIG_INVALID, "the request carried no signature")

    payload = ctx.request.model_dump(exclude_none=False)
    if not verify(payload, ctx.request.signature, ctx.stored.mandate.delegate.pubkey):
        return _fail(
            ReasonCode.REQUEST_SIG_INVALID,
            "not signed by the agent this mandate names",
        )
    return PASS


# --------------------------------------------------------------------- 2 ---


def check_mandate_signature(ctx: CheckContext) -> CheckOutcome:
    if ctx.stored is None:
        return _fail(ReasonCode.MANDATE_NOT_FOUND)
    if not ctx.stored.signature_ok:
        return _fail(
            ReasonCode.MANDATE_SIG_INVALID,
            "the delegator's signature over this mandate does not verify",
        )
    return PASS


# --------------------------------------------------------------------- 3 ---


def check_mandate_state(ctx: CheckContext) -> CheckOutcome:
    if ctx.stored is None:
        return _fail(ReasonCode.MANDATE_NOT_FOUND)
    if ctx.stored.revoked:
        return _fail(ReasonCode.MANDATE_REVOKED)
    return PASS


# --------------------------------------------------------------------- 4 ---


def check_validity_window(ctx: CheckContext) -> CheckOutcome:
    assert ctx.mandate is not None
    c = ctx.mandate.constraints
    now = parse_rfc3339(utcnow())
    try:
        start = parse_rfc3339(c.valid_from)
        end = parse_rfc3339(c.valid_until)
    except ValueError:
        # An unparseable window is not a valid window. Fail closed.
        return _fail(ReasonCode.MANDATE_EXPIRED, "the validity window is unparseable")

    if now < start:
        return _fail(ReasonCode.MANDATE_NOT_YET_VALID, f"valid from {c.valid_from}")
    if now > end:
        return _fail(ReasonCode.MANDATE_EXPIRED, f"expired at {c.valid_until}")
    return PASS


# --------------------------------------------------------------------- 5 ---


def check_freshness(ctx: CheckContext) -> CheckOutcome:
    now = parse_rfc3339(utcnow())
    try:
        issued = parse_rfc3339(ctx.request.issued_at)
    except ValueError:
        return _fail(ReasonCode.REQUEST_STALE, "issued_at is not RFC 3339")

    skew = abs((now - issued).total_seconds())
    if skew > FRESHNESS_WINDOW_SECONDS:
        return _fail(
            ReasonCode.REQUEST_STALE,
            f"{int(skew)}s outside the {FRESHNESS_WINDOW_SECONDS}s window",
        )
    return PASS


# --------------------------------------------------------------------- 6 ---


def check_replay(ctx: CheckContext) -> CheckOutcome:
    """
    Insert the nonce. The uniqueness violation IS the replay.

    A SELECT-then-INSERT would be a race and a second round trip. The primary
    key does the work, and it does it atomically by construction.
    """
    conn = ctx.conn_factory()
    try:
        conn.execute(
            "INSERT INTO nonces (nonce, mandate_id, seen_at) VALUES (?, ?, ?)",
            (ctx.request.nonce, ctx.request.mandate_id, utcnow()),
        )
    except sqlite3.IntegrityError:
        return _fail(ReasonCode.NONCE_REPLAY, "this exact request was already used")
    return PASS


# --------------------------------------------------------------------- 7 ---


def check_scope(ctx: CheckContext) -> CheckOutcome:
    assert ctx.mandate is not None
    c = ctx.mandate.constraints

    if ctx.request.payee_vpa not in c.merchant_allowlist:
        # Named explicitly, because the lookalike-VPA attack is only obvious on
        # screen when the near-miss string is visible next to the allowed one.
        return _fail(
            ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED,
            f"{ctx.request.payee_vpa} is not on the allowlist "
            f"({', '.join(c.merchant_allowlist)})",
        )

    if ctx.quote is not None:
        offending = [
            item["category"]
            for item in ctx.quote["items"]
            if item["category"] not in c.category_allowlist
        ]
        if offending:
            return _fail(
                ReasonCode.SCOPE_CATEGORY_MISMATCH,
                f"{offending[0]} is not in {', '.join(c.category_allowlist)}",
            )
    return PASS


# --------------------------------------------------------------------- 8 ---


def check_ceiling(ctx: CheckContext) -> CheckOutcome:
    """
    Atomic reservation. Not a counter.

    On success this has *taken* the budget, which is why it runs after scope —
    reserving money for a request we are about to refuse on scope would mean
    releasing it again, and a reservation that exists for microseconds is a
    reservation that can be observed.
    """
    assert ctx.mandate is not None
    outcome = ctx.ledger.reserve(
        mandate_id=ctx.request.mandate_id,
        decision_id=ctx.decision_id,
        amount_paise=ctx.request.amount_paise,
        constraints=ctx.mandate.constraints,
    )
    ctx.reservation_id = outcome.reservation_id
    ctx.headroom_paise = outcome.headroom_paise
    if not outcome.ok:
        return _fail(outcome.reason_code, outcome.detail)
    return PASS


# -------------------------------------------------------------------- 8b ---


def check_quote_binding(ctx: CheckContext) -> CheckOutcome:
    """
    The payment amount must equal the total of the quote it references.

    This is the check built for this track. A language model that invents a
    price is the most common real failure in agentic commerce, and this makes it
    structurally impossible rather than unlikely. The reason code is the proof.
    """
    if ctx.quote is None:
        return _fail(
            ReasonCode.QUOTE_EXPIRED,
            f"no live quote {ctx.request.quote_id}",
        )

    try:
        expires = parse_rfc3339(ctx.quote["expires_at"])
    except (KeyError, ValueError):
        return _fail(ReasonCode.QUOTE_EXPIRED, "the quote has no usable expiry")

    if parse_rfc3339(utcnow()) > expires:
        return _fail(ReasonCode.QUOTE_EXPIRED, f"expired at {ctx.quote['expires_at']}")

    quoted = int(ctx.quote["total_paise"])
    if quoted != ctx.request.amount_paise:
        return _fail(
            ReasonCode.QUOTE_AMOUNT_MISMATCH,
            f"payment {ctx.request.amount_paise} does not match quote {quoted}",
        )
    return PASS


# --------------------------------------------------------------------- 9 ---

#: Deterministic injection patterns. These run whether or not a model auditor is
#: configured, so the system has a real answer with no API key — rule 2 of the
#: shared contract. They are a floor, not the whole defence, and the ablation
#: matrix reports what they catch on their own.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore (?:all |any )?(?:previous|prior|above) instructions", re.I),
    re.compile(r"disregard (?:all |any )?(?:previous|prior) (?:instructions|rules)", re.I),
    re.compile(r"send (?:a |an )?(?:verification|security|validation) (?:deposit|payment|fee)", re.I),
    re.compile(r"transfer .{0,30}?to (?:the )?(?:following|this) (?:account|vpa|upi)", re.I),
    re.compile(r"you are now (?:a|an|the)\b", re.I),
    re.compile(r"report (?:this )?(?:as )?clean", re.I),
    re.compile(r"do not (?:flag|block|report) this", re.I),
    re.compile(r"system prompt", re.I),
)


def scan_for_injection(text: str) -> InjectedSpan | None:
    for pattern in INJECTION_PATTERNS:
        m = pattern.search(text or "")
        if m:
            return InjectedSpan(text=m.group(0), start=m.start(), end=m.end())
    return None


def check_intent(ctx: CheckContext, auditor) -> CheckOutcome:  # noqa: ANN001
    """
    Last, because it is the only network call.

    Two outcomes are deliberately different verdicts:

      * A detected injection BLOCKS. Instruction-shaped text in a product
        description is not ambiguous.
      * A mere mismatch between the purchase and the stated goal, or an auditor
        that does not answer, STEPS UP. The auditor is probabilistic and a
        probabilistic signal must never block a legitimate sale outright —
        that is the difference between losing the sale and recovering it.
    """
    excerpt = ctx.request.context.page_excerpt or ""
    reasoning = ctx.request.context.agent_reasoning or ""

    span = scan_for_injection(excerpt) or scan_for_injection(reasoning)
    if span is not None:
        return CheckOutcome(
            status="FAIL",
            reason_code=ReasonCode.INTENT_INJECTION_SUSPECTED,
            detail="the page text tried to instruct the agent",
            injected_span=span,
        )

    if auditor is None or not auditor.enabled:
        # Deterministic mode. The system boots and works with no auditor key;
        # it does not crash and it does not silently pretend to have audited.
        return PASS

    assert ctx.mandate is not None
    verdict = auditor.audit(
        intent=ctx.mandate.intent,
        excerpt=excerpt,
        reasoning=reasoning,
        amount_paise=ctx.request.amount_paise,
    )
    if verdict.injection_span is not None:
        return CheckOutcome(
            status="FAIL",
            reason_code=ReasonCode.INTENT_INJECTION_SUSPECTED,
            detail=verdict.detail,
            injected_span=verdict.injection_span,
        )
    if verdict.unavailable:
        return CheckOutcome(
            status="STEP_UP",
            reason_code=ReasonCode.AUDITOR_UNAVAILABLE,
            detail=verdict.detail or "the auditor did not answer in time",
        )
    if not verdict.matches_intent:
        return CheckOutcome(
            status="STEP_UP",
            reason_code=ReasonCode.INTENT_MISMATCH,
            detail=verdict.detail,
        )
    return PASS


def freshness_window() -> timedelta:
    return timedelta(seconds=FRESHNESS_WINDOW_SECONDS)
