"""
The gate engine's HTTP surface. Port 8000.

Rail agnostic. Nothing in this file, or anything it imports, knows that
Razorpay exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from contracts.crypto import verify
from contracts.reason_codes import CHECK_ORDER, ReasonCode, Verdict
from contracts.schemas import AuthorizeRequest, Decision, Mandate, utcnow
from core import config
from core.audit.store import AuditStore, EventBus
from core.db import Database
from core.gate.auditor import Auditor
from core.gate.engine import Gate, GateConfig
from core.ledger.headroom import HeadroomService
from core.ledger.reservations import Ledger
from core.mandate.store import MandateStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("pact.gate.app")

#: From the profile, never a literal here. See core/config.py — a default VPA
#: hardcoded in the rail-agnostic layer is exactly the coupling the layering
#: test greps for.
PROFILE = config.load()
MERCHANT_VPA = PROFILE.merchant_vpa
SWEEP_INTERVAL_SECONDS = 10

if not MERCHANT_VPA:
    # Fail at boot, loudly, rather than at runtime, silently.
    #
    # With no merchant VPA the scope check can never match, so every purchase is
    # refused SCOPE_MERCHANT_NOT_ALLOWED and every headroom envelope reports
    # merchant_in_scope false — which makes the upsell filter withhold
    # everything, because it fails closed. The result is a system where health
    # checks are green, the console renders, and nothing can ever be bought.
    # That is far harder to diagnose than not starting.
    raise RuntimeError(
        "No merchant VPA configured, so the gate would refuse every purchase "
        "and offer nothing. Set PACT_PROFILE to a file in profiles/ (for "
        "example PACT_PROFILE=razorpay-track01) or set PACT_MERCHANT_VPA."
    )


class GateService:
    """Everything the app holds, in one object so tests can build it directly."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db = Database(db_url)
        self.bus = EventBus()
        self.audit = AuditStore(self.db, self.bus)
        self.mandates = MandateStore(self.db)
        self.ledger = Ledger(self.db)
        self.auditor = Auditor()
        self.config = GateConfig(merchant_vpa=MERCHANT_VPA)
        self.gate = Gate(
            self.db,
            config=self.config,
            auditor=self.auditor,
            mandates=self.mandates,
            ledger=self.ledger,
            audit=self.audit,
        )
        self.headroom = HeadroomService(
            self.mandates, self.ledger, merchant_vpa=MERCHANT_VPA
        )

    def set_disabled_checks(self, names: set[str]) -> None:
        """Ablation. Rebuilds the gate with a new config; cheap and explicit."""
        self.config = GateConfig(
            merchant_vpa=MERCHANT_VPA, disabled_checks=frozenset(names)
        )
        self.gate.config = self.config


service = GateService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.bus.bind_loop(asyncio.get_running_loop())

    async def sweeper() -> None:
        # Releases expired reservations so an abandoned checkout does not
        # sequester the buyer's budget until the demo is reset.
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                freed = await asyncio.to_thread(service.ledger.sweep)
                if freed:
                    log.info("sweeper released %d expired reservation(s)", freed)
                    service.bus.publish("stats", {"swept": freed})
            except Exception:  # noqa: BLE001
                log.exception("sweeper failed; continuing")

    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="PACT gate", version="1.0.0", lifespan=lifespan)

# The console is same-origin through its dev proxy, so this is only for a
# direct browser hit during debugging. Test mode, local only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ mandates --


@app.post("/v1/mandates")
async def register_mandate(mandate: Mandate) -> dict:
    accepted, code = await asyncio.to_thread(service.mandates.register, mandate)
    return {
        "mandate_id": mandate.mandate_id,
        "accepted": accepted,
        "reason_code": str(code),
    }


@app.post("/v1/mandates/{mandate_id}/revoke")
async def revoke_mandate(mandate_id: str) -> dict:
    ok = await asyncio.to_thread(service.mandates.revoke, mandate_id)
    if not ok:
        raise HTTPException(404, {"reason_code": str(ReasonCode.MANDATE_NOT_FOUND)})
    service.bus.publish("mandate", {"mandate_id": mandate_id, "revoked": True})
    return {"ok": True}


@app.get("/v1/mandates/{mandate_id}/headroom")
async def get_headroom(mandate_id: str) -> dict:
    envelope = await asyncio.to_thread(service.headroom.for_mandate, mandate_id)
    if envelope is None:
        raise HTTPException(404, {"reason_code": str(ReasonCode.MANDATE_NOT_FOUND)})
    return envelope.model_dump()


@app.get("/v1/gate/pubkey")
async def gate_pubkey() -> dict:
    """So the merchant can verify the headroom envelopes it is handed."""
    return {"algorithm": "Ed25519", "public_key_b64u": service.headroom.public_key_b64u}


# ----------------------------------------------------------------- authorize --


@app.post("/v1/authorize")
async def authorize(request: AuthorizeRequest) -> dict:
    decision = await asyncio.to_thread(service.gate.authorize, request)
    # The settlement token goes to this caller only. It is never broadcast.
    return decision.model_dump()


@app.post("/v1/settlement/redeem")
async def redeem(payload: dict) -> dict:
    """
    Called by the merchant when it places an order. Single use.

    The merchant asks the gate rather than checking a token itself, because the
    token's uniqueness is ledger state and the ledger lives here.
    """
    token = str(payload.get("settlement_token", ""))
    amount = int(payload.get("amount_paise", 0))
    ok, code, decision_id = await asyncio.to_thread(
        service.gate.redeem_token, token, amount_paise=amount
    )
    return {"ok": ok, "reason_code": str(code), "decision_id": decision_id}


@app.post("/v1/settlement/commit")
async def commit_reservation(payload: dict) -> dict:
    """Capture confirmed. Moves the reservation RESERVED -> COMMITTED."""
    decision_id = str(payload.get("decision_id", ""))
    moved = await asyncio.to_thread(service.ledger.commit, decision_id)
    return {"ok": moved}


@app.post("/v1/settlement/release")
async def release_reservation(payload: dict) -> dict:
    """Rollback. The headroom returns."""
    decision_id = str(payload.get("decision_id", ""))
    released = await asyncio.to_thread(service.ledger.release, decision_id)
    service.bus.publish("budget_released", {"decision_id": decision_id, "amount_paise": released})
    return {"ok": released > 0, "released_paise": released}


# ----------------------------------------------------------------- decisions --


@app.get("/v1/decisions")
async def list_decisions(limit: int = 50) -> dict:
    rows = await asyncio.to_thread(service.audit.list_decisions, min(limit, 200))
    return {"decisions": rows}


# sse_starlette's default keepalive is an SSE *comment*, and a comment fires no
# event in the browser — so the console's idle watchdog was never fed by it and
# every stream tore itself down and resynced every fifteen seconds. Named
# instead: `heartbeat` is already on the console's listener list, where it feeds
# the watchdog and renders nothing.
def _heartbeat() -> ServerSentEvent:
    return ServerSentEvent(event="heartbeat", data="{}")


@app.get("/v1/decisions/stream")
async def decisions_stream(request: Request) -> EventSourceResponse:
    async def gen():
        async for event in service.bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": json.dumps(event.data)}

    return EventSourceResponse(gen(), ping=5, ping_message_factory=_heartbeat)


@app.get("/v1/decisions/{decision_id}")
async def get_decision(decision_id: str) -> dict:
    row = await asyncio.to_thread(service.audit.get_decision, decision_id)
    if row is None:
        raise HTTPException(404, "no such decision")
    return row


@app.post("/v1/decisions/{decision_id}/step_up")
async def resolve_step_up(decision_id: str, payload: dict) -> dict:
    """
    The human answered the step up.

    Approving requires their signature over this specific decision, verified
    against the delegator key on the mandate. Without that this endpoint would
    let anyone who can reach the gate approve anything, which would make the
    whole step up theatre.
    """
    row = await asyncio.to_thread(service.audit.get_decision, decision_id)
    if row is None:
        raise HTTPException(404, "no such decision")

    decision = Decision.model_validate(row)
    if decision.verdict is not Verdict.STEP_UP:
        return {"verdict": str(decision.verdict), "reason_code": str(decision.reason_code)}

    approve = bool(payload.get("approve"))
    if not approve:
        decision.verdict = Verdict.BLOCK
        decision.reason_code = ReasonCode.INTENT_MISMATCH
        decision.reason_detail = "Refused by the human"
        await asyncio.to_thread(service.ledger.release, decision_id)
        await asyncio.to_thread(service.audit.update_decision, decision)
        return {"verdict": "BLOCK", "reason_code": str(decision.reason_code)}

    stored = await asyncio.to_thread(service.mandates.get, decision.mandate_id)
    if stored is None:
        raise HTTPException(404, {"reason_code": str(ReasonCode.MANDATE_NOT_FOUND)})

    approval = payload.get("approval") or {}
    signature = payload.get("signature") or ""
    if not signature or not verify(approval, signature, stored.mandate.delegator.pubkey):
        # Fail closed. An approval we cannot attribute to the device is not an
        # approval.
        raise HTTPException(
            403,
            {
                "reason_code": str(ReasonCode.REQUEST_SIG_INVALID),
                "detail": "the approval was not signed by the delegator's device key",
            },
        )
    if approval.get("decision_id") != decision_id:
        raise HTTPException(
            403, {"detail": "the signed approval names a different decision"}
        )

    decision.verdict = Verdict.ALLOW
    decision.reason_code = ReasonCode.OK
    decision.reason_detail = "Approved by the human on device"
    token = await asyncio.to_thread(
        service.gate._issue_token,  # noqa: SLF001 - same package, deliberate
        decision_id=decision_id,
        mandate_id=decision.mandate_id,
        quote_id=decision.quote_id or "",
        amount_paise=decision.amount_paise,
    )
    decision.settlement_token = token
    await asyncio.to_thread(service.audit.update_decision, decision)
    return {
        "verdict": "ALLOW",
        "reason_code": str(ReasonCode.OK),
        "settlement_token": token,
    }


# --------------------------------------------------------------------- admin --


@app.post("/v1/admin/reset")
async def reset() -> dict:
    await asyncio.to_thread(service.db.reset)
    service.bus.publish("reset", {"at": utcnow()})
    return {"ok": True}


@app.post("/v1/admin/ablate")
async def ablate(payload: dict) -> dict:
    """
    Disable named checks so Lane B can rerun the attack set with one layer
    removed and record what leaks. Ablated checks report SKIPPED with a reason,
    never a silent PASS.
    """
    names = {str(n) for n in payload.get("disabled", [])}
    unknown = names - set(CHECK_ORDER)
    if unknown:
        raise HTTPException(400, f"unknown checks: {sorted(unknown)}")
    service.set_disabled_checks(names)
    return {"ok": True, "disabled": sorted(names), "chain": list(CHECK_ORDER)}


@app.get("/v1/health")
async def health() -> dict:
    return {
        "ok": True,
        "auditor": "enabled" if service.auditor.enabled else "deterministic",
        "disabled_checks": sorted(service.config.disabled_checks),
        "subscribers": service.bus.subscriber_count,
        "at": utcnow(),
    }
