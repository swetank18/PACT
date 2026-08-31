# PACT

**The merchant reads what the buyer is allowed to spend, before it quotes.**

Agentic commerce has standardised how an agent pays. It has not given the
merchant any way to read the buyer's authority envelope *before* making an
offer. That gap is the product: a signed delegation the buyer's agent carries, a
gate that decides on authority alone, and a merchant that only ever offers what
will be approved.

The consequence is the point. If the merchant can see remaining authority, every
cross-sell it makes is provably approvable — so the spending gate stops being
friction and becomes a conversion instrument.

## Run it

```bash
docker compose up --build     # everything on http://localhost:8080
```

Or without building, from the image CI publishes after the six demo beats have
run against it:

```bash
docker run -p 8080:8080 -v pact-data:/data ghcr.io/swetank18/pact:latest
```

Or for development, with hot reload and the services on separate ports:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/dev.sh          # gate :8000, merchant :8100, webhooks :8110, beats :8300
./scripts/test.sh         # 170 tests

cd console && npm install && npm run dev    # http://localhost:5173
```

With no Razorpay credentials it runs on the `mock_upi` rail, which exercises
every line above the adapter. Test keys only — the client refuses to start on a
key that is not `rzp_test_`.

Then press `1` through `6` in the console. That is the whole pitch; nothing is
typed on stage.

```bash
python sim/run.py --all --sessions 200 --seeds 3    # regenerates eval/results/
```

Deployment notes, and the two traps in the single-port build, are in
[`deploy/README.md`](deploy/README.md). It needs a host that runs a persistent
process — the pollers, the background saga and the SQLite write lock all outlive
a request, so this is not serverless-deployable as it stands. `fly.toml` and
`render.yaml` deploy the published image; neither has been run.

## What CI proves on every push

Not "the tests pass". The image is built, started through the compose file, and
driven:

| | |
| --- | --- |
| `ci` | 170 Python tests, 33 console tests, typecheck, and a contract-drift check |
| `container` | builds the image, waits for its healthcheck, runs the six demo beats against it over HTTP, asserts the SSE stream is live, restarts it with the volume attached and checks the orders and the gate's signing key survived, then drives all four console surfaces in Chromium |

The beats are asserted on what they are meant to prove, not on a 200 — beat 3
has to *fail* to complete, or the contrast it exists to draw is not there.
Screenshots are uploaded from every run; the committed ones are in
[`docs/screenshots/`](docs/screenshots/).

Only after all of that does the image reach `ghcr.io/swetank18/pact:latest`, so
what ships is the artefact that was tested rather than a second build.

## What is here

```
contracts/   frozen wire shapes, reason codes, the signing procedure
core/        RAIL AGNOSTIC. gate, mandate, ledger, audit, rail.py, config
rails/       one directory per settlement rail: razorpay, mock_upi
merchant/    catalog, quote engine, headroom upsell, saga, MCP tools
buyer/       the agent: one program, two flags, no forked scripts
sim/         four-arm experiment, attacks, benign set, chaos, ablation, beats
eval/        generated results. Never hand-edited.
console/     the three surfaces a judge sees
profiles/    one config file per event. Changing events is changing a flag.
```

**Nothing in `core/` imports anything from `rails/`.** The gate decides on
authority; a rail moves money. `tests/test_invariants.py` enforces it with an
AST grep, and a second test catches the loophole — no *executable* line in
`core/` may name a vendor, because a string literal comparing against a rail
would pass the import check and still couple the gate.

## The three claims, and where they are tested

**Ceilings are reserved, not counted.** Budget is a SUM over reservations
computed inside the same `BEGIN IMMEDIATE` that inserts the new row. The naive
read-then-write implementation is kept so the two can be compared:

```
tests/test_race.py
  atomic: 5 approved, 500000 paise, cap 500000
  naive:  20 approved, 2000000 paise, cap 500000 -> overspent by 1500000
```

**Every offer is provably approvable.** `tests/test_addons.py` puts every
suggested addon through the real gate; one BLOCK fails the suite. It also
asserts the naive upsell still offers something the gate rejects, because a
comparison with one side measured is not a comparison.

**The headroom envelope cannot leak.** `tests/test_headroom.py` asserts the
*field set*, not one instance's values. The delegator's identity, the intent
text, the total budget and the spend history are absent from the type. Note
what `headroom_paise` is: what is **left**, never what the budget was. A
merchant that learns "₹8,900 remaining" learns what it can sell; one that learns
"₹8,900 of ₹15,000" learns how rich the buyer is.

## What the experiment says

Four arms, 200 sessions each, three seeds, all against the real services.
`eval/results/results.md` is generated by the harness; the summary is:

**C against D is unambiguous.** PACT nets about a quarter more than a naive
client-side cap, with a 0% false block rate against C's ~28%. Arm C refuses
legitimate sales because a hard cap has no way to ask; arm D steps up and
recovers them.

**B against D is not what we wanted, and it is reported anyway.** An ungated
agent channel converts more, because nothing stops it. Under this loss model it
nets more below roughly 20% adversarial traffic. `results.md` carries a
six-point sensitivity sweep with the crossover named, rather than a rate chosen
to flatter us. What the model cannot price — chargebacks, dispute handling, an
account in bad standing — all points the other way, and is named rather than
estimated.

The number worth pointing at: **arm D's net is flat across every adversarial
rate swept.** It refuses all of it. Arm B degrades linearly.

Arm C is also the row worth pausing on for a reason that has nothing to do with
revenue. Its cap is real, and it lives in the agent. A compromised agent does
not run it. **A client-side control is not a control.**

## The graceful failure

Capture succeeds, fulfilment does not:

```
QUOTED -> RESERVED_STOCK -> GATE_ALLOWED -> PAYMENT_CAPTURED -> FULFILMENT ✗
       -> ROLLING_BACK -> REFUND_ISSUED -> BUDGET_RELEASED
       -> ALTERNATIVE_OFFERED -> (buyer signs) -> RECOVERED
```

Compensations run in reverse, each idempotent, each retried three times. A
refund that fails all three parks the order in `NEEDS_ATTENTION` and raises it
in the console — **never a silent swallow**. That is
`test_a_failed_refund_parks_rather_than_losing_money_quietly`, and it is the one
to volunteer unprompted: anyone can handle the failure they planned for.

Accepting the alternative returns a *quote the buyer signs*, not a completed
order. The merchant holds no key and must never be able to spend on the buyer's
behalf. The replacement carries `recovered_from`, so the recovery is revenue
without the refunded original inflating GMV.

## Signature parity

The mandate is signed in the browser on the buyer's device. The agent receives
the signed mandate and never the key, which is why it cannot spend outside what
the human granted.

```
strip `signature` → RFC 8785 JCS → Ed25519 → base64url unpadded
```

`fixtures/keys/test_vector.json` is the cross language vector, published here
for whoever implements the browser side. It was generated by an implementation
written from RFC 8785 independently of any other — parity between two ports of
the same code proves nothing.

```bash
python3 scripts/gen_test_vector.py     # regenerate
```

The browser side that reproduces it lives on the `lane-c-console` branch, and
runs at boot: the console fetches the vector, verifies it in the browser's own
Ed25519 and JCS, and puts `SIGNATURE PARITY · 2 VECTORS` in the header. It is
the one claim here a judge can check by looking.

It was silently missing in the deployed build for as long as that build existed.
The vector is served by a Vite dev-server middleware, so a built bundle 404d and
the badge degraded to "unavailable" — correct behaviour, and precisely why 32
tests and a full jsdom mount could not see it. `deploy/app.py` now serves that
one file by name, deliberately not by mounting `fixtures/`, which also holds the
gate's private signing key.

## Razorpay

`rails/razorpay/API_NOTES.md` is written from the live docs, dated, with every
unverified item marked as such. The finding that shapes the client:

> **There is no idempotency header.** `receipt` covers orders and refunds only,
> caps at 40 characters, and *rejects* duplicates rather than replaying the
> original response. Capture has no idempotency at all.

So we keep our own table and short-circuit before the call. That is the only way
"calling twice must not charge twice" holds across all three operations rather
than two. A refund returning `pending` is also not a completed compensation, and
the saga does not treat it as one.

**The client has never run against the live API** — there are no test keys in
the environment this was built in. It is instead driven through
`tests/fake_razorpay.py`, which is not a mock that returns what the caller hopes
for: it is built from `API_NOTES.md` and it *refuses* what the real API refuses.
Missing `currency` on capture is a 400. A second capture is the documented
"already paid" 400, not a success. A duplicate `receipt` is rejected rather than
replayed. 27 tests, mutation-checked — dropping `currency` from capture fails
four of them.

What that cannot catch is a field the real API requires that `API_NOTES.md`
failed to record. Only a test key closes that, and it is the largest remaining
gap in the system.

Webhook signatures are HMAC-SHA256 over the **raw** body — re-serialising before
verifying is the standard way that check silently stops working, and
`tests/test_webhook.py` asserts it. Webhooks are never the only path: a
reconciliation poller resolves anything pending and older than 30 seconds.

## Invariants

- **Money is integer paise.** `contracts/money.py` refuses floats outright
  rather than rounding one, because a float here is always a bug.
- **Prices are computed server side.** The model never does arithmetic that
  reaches a payload, which makes a hallucinated price a structural impossibility
  rather than a hope. `QUOTE_AMOUNT_MISMATCH` is the reason code that proves it.
- **Timestamps are RFC 3339 UTC with a `Z`.**
- **IDs are prefixed:** `mnd_`, `dec_`, `qte_`, `ord_`, `stl_`, `sim_`.
- **Fail closed.** Any error, timeout or unparseable input yields BLOCK or
  STEP_UP. A check that raises becomes a BLOCK, because a gate that crashes open
  is worse than no gate.

## Limitations

- Test mode only. No live money has moved through this, and the Razorpay client
  has never run against the live API — see above for what stands in for it and
  what that does not cover.
- The intent auditor's model has never been called. With no `ANTHROPIC_API_KEY`
  the gate runs deterministic mode, which is a complete system by design. The
  auditor's wiring is now tested through an injected transport — every failure
  path returns `unavailable` and steps up, never approval — but nothing has
  measured how well the model actually answers, so `atk_06` is reported **N/A**
  rather than as a pass.
- `fly.toml` and `render.yaml` are written and unexecuted. No credentials. The
  image they deploy is not unexecuted.
- **One instance saturates at somewhere between 32 and 64 concurrent buyers.**
  At 32: 200/200 purchases complete, 53/s, p50 396 ms. At 64: p50 20 s and a
  third complete. It degrades by refusing to settle rather than by settling
  without live authority, which is the right direction, but it is one worker on
  SQLite and scaling it means Postgres and a queue. `scripts/load.py` measures
  both, and asserts the ceiling holds while it does.
- One merchant, one catalog.
- The intent auditor is probabilistic, which is exactly why it steps up rather
  than blocking. The eight deterministic checks plus quote binding are the
  system; the auditor is the ninth and it is cuttable.
- The saga's three settlement-side reason codes were declared in the enum and
  emitted by nothing for three releases — the trail said what happened in
  English and the contract said nothing. Fixed, and `tests/test_invariants.py`
  now fails on any code the engine never raises.
- The regulatory direction this aims at is press reporting, not a published
  specification. The delegation shape it borrows from does exist today.
- The headroom endpoint is worth nothing unless buyer agents call it. That is a
  distribution problem, not a technical one.
- Arm A of the experiment is modelled, not simulated — there is no agent to run.
  Its two parameters are stated in `eval/README.md`.
- The adversarial share of traffic (8%) is an assumption. It is swept.
