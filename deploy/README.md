# deploy

Everything behind one port.

```bash
docker compose up --build      # http://localhost:8080
```

Or pull the image CI publishes, which is the one the six demo beats have already
run against:

```bash
docker run -p 8080:8080 -v pact-data:/data ghcr.io/swetank18/pact:latest
```

The GHCR package is private by default. Make it public once, in the repository's
package settings, if anonymous pulls are wanted.

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

## Two more that bit

**`/admin/reset` deadlocked.** It made blocking HTTP calls to the gate and the
merchant, awaited on the event loop — and here those services *are* this
process. The loop sat waiting on a request only it could serve until the client
timed out, then returned 500. The console binds that call to `0`, so the reset
key was dead in the deployed topology and worked perfectly in development, where
the four services are separate processes. Now off the loop, like the demo beats
already were.

**The signature parity vector 404d.** `fixtures/` is served in development by a
Vite dev-server middleware, which does not exist in a built bundle. So the
console's parity badge — the one claim on screen a judge can check by looking —
was silently absent from the only build anyone would demo from. It degraded to
"unavailable" rather than reporting a failure, which is correct behaviour and
exactly why no test caught it; it took opening a browser.

`deploy/app.py` now serves that one file **by name**. Deliberately not a
`StaticFiles` mount on `fixtures/`, because that directory also holds the gate's
private signing key — mounting it would have published the key over HTTP in
order to fix a badge. `scripts/smoke.py` asserts both halves: the vector is
served, and the key and its directory are not.

## Verification status

**The image builds and works**, and this is checked on every push rather than
once. `.github/workflows/container.yml` builds it through the compose file — so
the volume, the healthcheck and the environment are exercised too — waits for
the healthcheck to go green, then:

- 13 smoke checks, including all six demo beats asserted on what each is meant
  to prove, and a live SSE frame count
- a restart with the volume attached, asserting the orders and the gate's
  signing key both survived
- all four console surfaces driven in Chromium, failing on any console error or
  failed request

~208 MB, 22 layers. Published to `ghcr.io/swetank18/pact:latest` only after all
of that passes, so what ships is the artefact that was tested.

**Render is deployed and checked. Fly is not.**
`https://pact-9btr.onrender.com`, created 2026-09-05 with:

```bash
RENDER_API_KEY=rnd_… python3 scripts/render.py create --plan starter
```

Live on the first attempt, no iteration on machine size or health check grace
periods. 5/5 smoke checks, and every console surface plus all six firewall tabs
rendered in Chromium with no console error and no failed request.

**The live instance is on the free plan, which is not what `render.yaml` says.**
Starter was refused with a 402 — no payment method on the workspace — and Render
allows a disk only on a paid instance. So `/data` is ephemeral there: a restart
issues a new gate signing key and empties the ledger, and headroom envelopes
already handed out stop verifying. It also sleeps after ~15 minutes idle, and
asleep it is not running the sweeper or the reconciler. `render.yaml` remains
the blueprint for the deploy this should have; `scripts/render.py` refuses to
send anything that disagrees with it.

`fly.toml` is still unexecuted — there are no Fly credentials here. Smoke-test
whatever comes up:

```bash
python3 scripts/smoke.py --base https://your-instance --skip-beats
```

`--skip-beats` because the beats write orders and force a stockout. Run them
against a public instance only if you mean to.
