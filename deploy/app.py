"""
The production build: everything behind one port.

In development the four services run on four ports and Vite proxies `/api/*` to
them. That is right for development — each service restarts on its own — and
wrong for deployment, where it means four processes, a reverse proxy, and four
things that can be up while a fifth is down.

Here the same four ASGI apps are **mounted** into one, and the built console is
served from `/`. One process, one port, same origin, no proxy. The console's
`/api/gate`, `/api/merchant` and `/api/sim` paths are unchanged, so nothing in
the front end knows the difference.

The part that needs care is lifespan. Starlette does **not** run the lifespan of
a mounted sub-app, so mounting naively would silently disable the sweeper, the
reconciliation poller, and the event-bus loop binding that SSE depends on — the
app would look healthy and quietly stop doing three of the things it exists to
do. `lifespan` below enters each sub-app's lifespan explicitly.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from contracts.schemas import utcnow

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("pact.deploy")

REPO = Path(__file__).resolve().parent.parent
CONSOLE_DIST = Path(os.environ.get("PACT_CONSOLE_DIST", REPO / "console" / "dist"))

# The services call each other over HTTP — the merchant asks the gate for
# headroom, the demo beats drive both. In development those are separate ports;
# here they are mounted on this same app, so the internal URLs have to point
# back at us.
#
# This MUST run before the sub-apps are imported: `merchant.app` builds its gate
# client at import time, and a client built against the wrong URL would fail
# every headroom lookup — which fails *closed*, so the upsell would silently
# offer nothing and the growth demo would quietly do nothing at all.
# Without a profile the gate has no merchant VPA and refuses to start. Default
# it here so a bare `docker run` works, while still letting the environment win.
os.environ.setdefault("PACT_PROFILE", "razorpay-track01")

SELF = os.environ.get("PACT_SELF_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"
os.environ.setdefault("PACT_GATE_URL", f"{SELF}/api/gate")
os.environ.setdefault("PACT_MERCHANT_URL", f"{SELF}/api/merchant")

# Imported after logging is configured so their startup warnings are formatted.
from core.app import app as gate_app, service as gate_service  # noqa: E402
from merchant.app import app as merchant_app, service as merchant_service  # noqa: E402
from rails.razorpay.webhooks import WebhookProcessor, build_app  # noqa: E402
from sim.demo import app as demo_app  # noqa: E402


def _on_webhook_applied(order_id: str, state: str, entity: dict) -> None:
    merchant_service.audit.append_step(
        order_id=order_id,
        state=state,
        action="webhook.payment.captured",
        outcome="OK",
        detail="confirmed by webhook",
        ref=entity.get("id"),
    )
    merchant_service.publish_stats()


webhook_app = build_app(
    WebhookProcessor(
        merchant_service.db, merchant_service.rail, on_applied=_on_webhook_applied
    ),
    path="/rail",
)

MOUNTED = (
    ("/api/gate", gate_app),
    ("/api/merchant", merchant_app),
    ("/api/sim", demo_app),
    ("/api/webhooks", webhook_app),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Run every mounted app's lifespan.

    Without this the gate's reservation sweeper and the merchant's
    reconciliation poller never start, and neither event bus is bound to the
    running loop — so SSE would connect and then deliver nothing. All three
    failures are invisible from a health check, which is exactly why this is
    explicit rather than assumed.
    """
    async with AsyncExitStack() as stack:
        for path, sub in MOUNTED:
            await stack.enter_async_context(sub.router.lifespan_context(sub))
            log.info("started %s", path)
        yield


app = FastAPI(title="PACT", version="1.0.0", lifespan=lifespan)

for path, sub in MOUNTED:
    app.mount(path, sub)


@app.get("/healthz")
async def healthz() -> dict:
    """One check that covers everything, for whatever is watching the container."""
    return {
        "ok": True,
        "rail": merchant_service.rail.name,
        "auditor": "enabled" if gate_service.auditor.enabled else "deterministic",
        "console": CONSOLE_DIST.is_dir(),
        "self_url": SELF,
        "mounted": [p for p, _ in MOUNTED],
        "at": utcnow(),
    }


if CONSOLE_DIST.is_dir():
    # Mounted last so it cannot shadow the API routes above.
    app.mount("/", StaticFiles(directory=CONSOLE_DIST, html=True), name="console")
    log.info("serving the console from %s", CONSOLE_DIST)
else:
    log.warning(
        "no console build at %s. Run `npm run build` in console/. The API is up "
        "either way.",
        CONSOLE_DIST,
    )

    @app.get("/")
    async def no_console() -> JSONResponse:
        return JSONResponse(
            {
                "error": "the console has not been built",
                "fix": "cd console && npm install && npm run build",
                "api": [p for p, _ in MOUNTED],
            },
            status_code=503,
        )
