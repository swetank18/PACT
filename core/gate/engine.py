"""
The gate. Runs the chain, times every check, short circuits, and records.

Three properties this file exists to guarantee:

**Fail closed.** Any error, timeout or unparseable input yields BLOCK or
STEP_UP. Never ALLOW. An unexpected exception inside a check is caught here and
becomes a BLOCK, because a gate that crashes open is worse than no gate.

**The chain is always fully reported.** Checks that never ran are emitted as
SKIPPED rather than omitted. Lane C renders them, and they are the visible proof
that the chain short circuits.

**A blocked reservation is released.** The ceiling check takes budget before
quote binding and intent run. If either of those then refuses, the budget goes
back in the same call. Money reserved for a request that was refused is a leak,
and it is the kind that only shows up after forty demo runs.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from contracts.ids import new_id
from contracts.reason_codes import CHECK_ORDER, ReasonCode, Verdict, verdict_for
from contracts.schemas import (
    AuthorizeRequest,
    CheckResult,
    Decision,
    parse_rfc3339,
    utcnow,
)
from core.audit.store import AuditStore
from core.db import Database
from core.gate.auditor import Auditor
from core.gate.checks import (
    CheckContext,
    CheckOutcome,
    check_ceiling,
    check_freshness,
    check_mandate_signature,
    check_mandate_state,
    check_quote_binding,
    check_replay,
    check_request_signature,
    check_scope,
    check_validity_window,
    check_intent,
)
from core.ledger.reservations import Ledger
from core.mandate.store import MandateStore

log = logging.getLogger("pact.gate")

#: How long a settlement token is good for. Shorter than the reservation so a
#: token can never outlive the budget it is backed by.
SETTLEMENT_TOKEN_TTL_SECONDS = 240

#: Checks that take budget. If the chain refuses after one of these has run, the
#: engine compensates before returning.
BUDGET_TAKING = frozenset({"ceiling"})


@dataclass(frozen=True, slots=True)
class GateConfig:
    merchant_vpa: str
    #: Ablation. Names in here are reported as SKIPPED and never run, so Lane B
    #: can rerun the attack set with one layer removed and record what leaks.
    disabled_checks: frozenset[str] = frozenset()


class Gate:
    def __init__(
        self,
        db: Database,
        *,
        config: GateConfig,
        auditor: Auditor | None = None,
        mandates: MandateStore | None = None,
        ledger: Ledger | None = None,
        audit: AuditStore | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.auditor = auditor if auditor is not None else Auditor()
        self.mandates = mandates or MandateStore(db)
        self.ledger = ledger or Ledger(db)
        self.audit = audit or AuditStore(db)

    # ------------------------------------------------------------ the chain --

    def _runner(self, name: str):
        if name == "request_signature":
            return check_request_signature
        if name == "mandate_signature":
            return check_mandate_signature
        if name == "mandate_state":
            return check_mandate_state
        if name == "validity_window":
            return check_validity_window
        if name == "freshness":
            return check_freshness
        if name == "replay":
            return check_replay
        if name == "scope":
            return check_scope
        if name == "ceiling":
            return check_ceiling
        if name == "quote_binding":
            return check_quote_binding
        if name == "intent":
            return lambda ctx: check_intent(ctx, self.auditor)
        raise KeyError(name)

    def authorize(self, request: AuthorizeRequest) -> Decision:
        started = time.perf_counter()
        decision_id = new_id("dec")

        stored = self.mandates.get(request.mandate_id)
        quote = self._load_quote(request.quote_id)

        ctx = CheckContext(
            request=request,
            decision_id=decision_id,
            stored=stored,
            ledger=self.ledger,
            conn_factory=lambda: self.db.conn,
            db=self.db,
            merchant_vpa=self.config.merchant_vpa,
            quote=quote,
        )

        results: list[CheckResult] = []
        failed_at: CheckOutcome | None = None
        ran: set[str] = set()

        for name in CHECK_ORDER:
            if failed_at is not None:
                results.append(CheckResult(name=name, status="SKIPPED", ms=0.0))
                continue

            if name in self.config.disabled_checks:
                # Ablation. Reported honestly as skipped rather than silently
                # passed, so the matrix cannot accidentally credit a disabled
                # layer with a block.
                results.append(
                    CheckResult(name=name, status="SKIPPED", ms=0.0, detail="ablated")
                )
                continue

            t0 = time.perf_counter()
            try:
                outcome = self._runner(name)(ctx)
            except Exception as exc:  # noqa: BLE001
                # Fail closed. A check that raises is a check that did not pass.
                log.exception("check %s raised", name)
                outcome = CheckOutcome(
                    status="FAIL",
                    reason_code=ReasonCode.AUDITOR_UNAVAILABLE
                    if name == "intent"
                    else ReasonCode.REQUEST_SIG_INVALID,
                    detail=f"{name} errored: {type(exc).__name__}",
                )
            elapsed = (time.perf_counter() - t0) * 1000
            ran.add(name)

            results.append(
                CheckResult(
                    name=name,
                    status=outcome.status,
                    ms=round(elapsed, 3),
                    detail=outcome.detail,
                    injected_span=outcome.injected_span,
                )
            )
            if outcome.status in ("FAIL", "STEP_UP"):
                failed_at = outcome

        # ---------------------------------------------------------- verdict --

        if failed_at is None:
            verdict, reason, detail = Verdict.ALLOW, ReasonCode.OK, ""
        else:
            reason = failed_at.reason_code
            detail = failed_at.detail
            verdict = (
                Verdict.STEP_UP if failed_at.status == "STEP_UP" else verdict_for(reason)
            )

        # If the ceiling check took budget and we then refused, give it back.
        # STEP_UP keeps the reservation: the human is being asked right now and
        # releasing it would let a concurrent request spend the money underneath
        # them. The sweeper reclaims it if they never answer.
        if verdict is Verdict.BLOCK and "ceiling" in ran and ctx.reservation_id:
            released = self.ledger.release(decision_id)
            if released:
                log.info("released %s paise reserved for blocked %s", released, decision_id)

        settlement_token = None
        if verdict is Verdict.ALLOW:
            settlement_token = self._issue_token(
                decision_id=decision_id,
                mandate_id=request.mandate_id,
                quote_id=request.quote_id,
                amount_paise=request.amount_paise,
            )

        decision = Decision(
            decision_id=decision_id,
            mandate_id=request.mandate_id,
            verdict=verdict,
            reason_code=reason,
            reason_detail=detail,
            payee_vpa=request.payee_vpa,
            amount_paise=request.amount_paise,
            quote_id=request.quote_id,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            checks=results,
            page_excerpt=request.context.page_excerpt or None,
            settlement_token=settlement_token,
            at=utcnow(),
        )

        self.audit.record_decision(decision)
        return decision

    # ------------------------------------------------------------- helpers --

    def _load_quote(self, quote_id: str | None) -> dict | None:
        if not quote_id:
            return None
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT body_json FROM quotes WHERE quote_id = ?", (quote_id,)
            ).fetchone()
        return json.loads(row["body_json"]) if row else None

    def _issue_token(
        self, *, decision_id: str, mandate_id: str, quote_id: str, amount_paise: int
    ) -> str:
        token = new_id("stl")
        expires = (
            parse_rfc3339(utcnow()) + timedelta(seconds=SETTLEMENT_TOKEN_TTL_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO settlement_tokens
                    (token, decision_id, mandate_id, quote_id, amount_paise,
                     used_at, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (token, decision_id, mandate_id, quote_id, amount_paise, expires, utcnow()),
            )
        return token

    def redeem_token(self, token: str, *, amount_paise: int) -> tuple[bool, ReasonCode, str | None]:
        """
        Single use. Returns (ok, reason, decision_id).

        The UPDATE ... WHERE used_at IS NULL is the whole mechanism: two
        concurrent redemptions, exactly one rowcount of 1. A SELECT-then-UPDATE
        would let both through, which would let one ALLOW pay for two orders.
        """
        now = utcnow()
        with self.db.immediate_tx() as conn:
            row = conn.execute(
                "SELECT decision_id, amount_paise, used_at, expires_at "
                "FROM settlement_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return False, ReasonCode.TOKEN_INVALID, None
            if row["used_at"] is not None:
                return False, ReasonCode.TOKEN_ALREADY_USED, row["decision_id"]
            if row["expires_at"] < now:
                return False, ReasonCode.TOKEN_EXPIRED, row["decision_id"]
            if int(row["amount_paise"]) != amount_paise:
                return False, ReasonCode.QUOTE_AMOUNT_MISMATCH, row["decision_id"]

            cur = conn.execute(
                "UPDATE settlement_tokens SET used_at = ? WHERE token = ? AND used_at IS NULL",
                (now, token),
            )
            if cur.rowcount != 1:
                return False, ReasonCode.TOKEN_ALREADY_USED, row["decision_id"]

        return True, ReasonCode.OK, row["decision_id"]
