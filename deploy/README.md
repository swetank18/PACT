# deploy

Everything behind one port.

```bash
docker compose up --build      # http://localhost:8080
```

Or without Docker, which is what was used to verify this:

```bash
cd console && npm install && npm run build && cd ..
PACT_PROFILE=razorpay-track01 .venv/bin/python -m uvicorn deploy.app:app --port 8080
```

Then open `http://localhost:8080` and press `1` through `6`.

## What this is

In development the four services run on four ports and Vite proxies `/api/*` to
them. That is right for development and wrong for deployment. Here the same four
ASGI apps are **mounted** into one and the built console is served from `/`:

| Path | What |
| --- | --- |
| `/` | The console |
| `/api/gate` | The gate engine |
| `/api/merchant` | Catalog, quotes, upsell, orders, saga |
| `/api/sim` | The six demo beats |
| `/api/webhooks/rail` | The rail callback receiver |
| `/healthz` | Rail, auditor mode, console presence, mounted paths |

One process, one port, same origin, no reverse proxy. The console's paths are
unchanged, so nothing in the front end knows the difference.

## Two things that will bite whoever changes this

**Mounted apps do not get a lifespan.** Starlette does not run the lifespan of a
sub-app you mount. Mounting these naively disables the gate's reservation
sweeper, the merchant's reconciliation poller, and the event-bus loop binding
that SSE depends on. The app stays green, the console connects, and then nothing
ever arrives. `deploy/app.py` enters each sub-app's lifespan explicitly through
an `AsyncExitStack`.

**The services call each other over HTTP.** The merchant asks the gate for
headroom; the demo beats drive both. Those URLs must point back at this app, and
they are set *before* the sub-apps are imported, because `merchant.app` builds
its gate client at import time. A client built against the wrong URL fails every
headroom lookup, which fails **closed** — so the upsell silently offers nothing
and the growth demo quietly does nothing at all.

That second one is not hypothetical. It happened while building this, via a
different route: with no `PACT_PROFILE` set the gate booted with an empty
merchant VPA, every headroom envelope reported `merchant_in_scope: false`, and
the upsell withheld everything. Health checks were green throughout. The gate
now refuses to start without a merchant VPA rather than running in that state.

## One worker, deliberately

`--workers 1`. The ledger's correctness rests on SQLite's write lock, the
simulated rail keeps its idempotency table in memory, and the saga runs as a
background task inside the process. None of that survives being spread across
workers. Scaling means Postgres and a real queue, not `--workers 4`.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `PORT` | `8080` | |
| `PACT_PROFILE` | `razorpay-track01` | Sets the merchant VPA. **Required** — the gate will not start without one. |
| `PACT_DB_URL` | `sqlite:////data/pact.db` | Mount a volume on `/data`. |
| `PACT_GATE_KEY_PATH` | `/data/gate_signing_key.hex` | Lose this and headroom envelopes already issued stop verifying. |
| `PACT_SAGA_STEP_DELAY_S` | `0.35` | Paces the rollback for the stage. `0` for the simulation. |
| `PACT_RAIL` | falls back to `mock_upi` | Never defaults to a live rail. |
| `RAZORPAY_KEY_ID` / `_SECRET` / `_WEBHOOK_SECRET` | unset | Test keys only; the client refuses a non-`rzp_test_` key. |
| `ANTHROPIC_API_KEY` | unset | Without it the gate runs deterministic mode, by design. |

## Hosting

This wants a host that runs a persistent process with a writable volume:
Railway, Render, Fly.io, or any VPS. It is **not** deployable to a serverless
platform as it stands — the pollers, the background saga and the SQLite write
lock all need a process that outlives a request.

## Verification status

Verified natively, on this machine, at `deploy/app.py` on port 8080: all four
mounts respond, the console is served, all six demo beats pass, and 75 SSE
frames were delivered to a subscriber during the run — which is the check that
proves the mounted lifespans are actually running.

**The container image itself is unbuilt.** There is no Docker daemon in the
environment this was written in. The `Dockerfile` and `docker-compose.yml` are
written but unproven; what *was* verified is the layout assumption they rest on
— the console builds correctly against `contracts/generated.ts` from the mirrored
directory structure the build stage creates. Expect to iterate on the image once
on a machine with Docker.
