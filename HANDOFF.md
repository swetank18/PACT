# Handoff

Context for picking this up cold. Written 2026-08-31, after the build described
below. Read this first, then `README.md`, then the per-directory READMEs.

Nothing here duplicates the code. It covers what the code cannot tell you: what
was decided and why, what is verified and what is not, and what is still open.

---

## 0. Do this first

**The repository is public and internal planning documents are still fetchable
from orphaned commits.**

They were removed from `HEAD` and the history was rewritten, but a force-push
does not delete orphaned commits — GitHub keeps serving them by SHA. The repo
was made private as a stopgap and has since been made public again, so they are
readable by anyone with the SHA right now. Verified 2026-08-31: anonymous fetch
of `00-SHARED-CONTRACTS.md` at `02b9841` returns **200**.

`HEAD` is clean. Only the orphans are the problem.

The fix requires an auth scope this environment does not have:

```bash
gh auth refresh -h github.com -s delete_repo
# then: delete the repo, recreate it, push main + lane-c-console
```

Stopgap if that is not wanted right now:
`gh api -X PATCH repos/swetank18/PACT -f private=true`

The documents themselves live in `_private/` locally and are gitignored. They
are the source of the three-lane structure and the frozen contract. **Do not
commit them, and do not quote them into files that get committed.**

**A second thing needs the same purge.** `fixtures/keys/gate_signing_key.hex`
was committed and sat in a public repo for 25 commits. It is the gate's own
Ed25519 signing key — the one that signs headroom envelopes a merchant then
trusts without asking. It is a demo key, it is regenerated on first boot when
absent, and it should not have been there: the loader chmods it `0600`, which
buys nothing when the same bytes are in the history.

Untracked and gitignored as of 2026-08-31. Removing it from `HEAD` does not
remove it from history, so it goes into the same purge as the planning
documents. Anyone deploying before that purge should treat the key as public and
let the container generate a fresh one on its volume — which is what it does.

---

## 1. What this is

A merchant that AI buyers can transact with, plus the thing nobody else has: the
merchant can **read the buyer's remaining spending authority before it quotes**,
so every offer it makes is provably approvable.

Three planes: a merchant (catalog, deterministic quotes, checkout), a growth
layer (headroom-aware upsell, recovery), and a trust layer (signed mandate, nine
checks, audit trail, rollback saga).

The work was organised as three lanes. **All three are now on `main` and all
three were built here**, though the original division of labour still explains
the directory ownership.

| Lane | Directories | State |
| --- | --- | --- |
| A — backend | `contracts/ core/ rails/ merchant/` | Built, tested |
| B — agent + evidence | `buyer/ sim/ eval/` | Built, tested, numbers generated |
| C — interfaces | `console/` | Built, tested, wired to the real services |

~11k lines of Python, ~4.6k of TypeScript. **170 Python tests, 33 console
tests**, all green, plus two GitHub Actions workflows that build the container
image and drive the six demo beats and all four console surfaces against it on
every push.

---

## 2. Running it

```bash
docker compose up --build      # everything on http://localhost:8080

# or, without building: the image CI publishes after the beats have run on it
docker run -p 8080:8080 -v pact-data:/data ghcr.io/swetank18/pact:latest
```

Development, services on separate ports with hot reload:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/dev.sh                            # 8000 gate, 8100 merchant, 8110 hooks, 8300 beats
cd console && npm install && npm run dev    # 5173
./scripts/test.sh                           # 170 tests
```

`scripts/test.sh` exists because a ROS install on this machine puts broken pytest
plugins on `PYTHONPATH`; it clears the variable. Running `pytest` directly fails
with an unrelated `lark` import error.

The simulation needs the services up, and now honours where they are:

```bash
python sim/run.py --all --sessions 200 --seeds 3     # regenerates eval/results/
python sim/run.py --suite attacks|benign|chaos|ablation

# against the single-port build rather than the four dev ports
PACT_GATE_URL=http://localhost:8080/api/gate \
PACT_MERCHANT_URL=http://localhost:8080/api/merchant \
  python sim/run.py --all --sessions 200 --seeds 3
```

That second form did not work until 2026-08-31 — see section 5.

Smoke-test any running instance, including a deployed one:

```bash
python3 scripts/smoke.py --base http://localhost:8080      # stdlib only
cd console && npm run browser -- http://localhost:8080 shots
```

Press `1`–`6` in the console for the six demo beats. Nothing is typed on stage.

---

## 3. Verified vs not

Be precise about this. Over-claiming here is the easiest way to embarrass
everyone at the event.

### Verified, by running it

- Full purchase path end to end against the real services: cold manifest
  discovery → mandate → headroom → server-computed quote → headroom upsell →
  gate ALLOW in ~4 ms → saga to `FULFILLED`.
- All six demo beats, in both the dev topology and the single-port build.
- The rollback: capture succeeds, fulfilment fails, refund issued, budget
  released, alternative offered, buyer signs the replacement, `RECOVERED`.
- Race: 20 concurrent payments against a mandate with room for 5 → exactly 5
  approved. The naive read-then-write ledger approves all 20 and overspends 4×.
- Reset: 40 presses, max 0.27 ms in-process, 19 ms over HTTP.
- Restart persistence: orders survive, gate signing key survives.
- 75 SSE frames delivered to a live subscriber in the single-port build — which
  is what proves the mounted lifespans actually run.

Added 2026-08-31, and all of it runs on every push rather than once by hand:

- **The container image builds and works.** CI builds it through the compose
  file, waits for the healthcheck, and runs all six beats against it: 13 smoke
  checks green, ~208 MB, 22 layers. Published to
  `ghcr.io/swetank18/pact:latest` only after that passes.
- **It survives a restart with the volume attached** — orders and the gate's
  signing key both. Verified falsifiable: pointed at a fresh volume the check
  reports the new key rather than passing quietly.
- **The console has been seen.** Chromium, all four surfaces, zero console
  errors and zero failed requests. Screenshots in `docs/screenshots/`.
- **The Razorpay client runs**, against a fake built from `API_NOTES.md` that
  refuses what the real API refuses. 27 tests, mutation-checked.
- **The auditor runs**, through an injected transport. 19 tests. Every failure
  path returns `unavailable` and steps up, never approval.
- **The harness's tally agrees with the merchant's ledger to the paise.**

### NOT verified — the honest boundary

- **The Razorpay client has never run against the *real* API.** Still true, and
  still the largest gap. There are no test keys here. What changed is that it is
  no longer *unexercised*: `tests/fake_razorpay.py` enforces the documented rules
  and 27 tests drive the real client through it. The boundary is now exactly the
  accuracy of `API_NOTES.md` — a field the live API requires that the notes
  failed to record is still invisible.
  To close it: `export RAZORPAY_KEY_ID=rzp_test_… RAZORPAY_KEY_SECRET=… PACT_RAIL=razorpay`.
  The client refuses to start on a non-`rzp_test_` key.
- **The intent auditor's model has never been called.** No `ANTHROPIC_API_KEY`.
  The gate runs deterministic mode: eight checks + quote binding + a regex
  injection scan. The auditor's *wiring* is now tested — request shape, every
  failure path, and the three-way mapping onto verdicts through the gate — but
  nothing has measured how well the model answers. `atk_06` stays **N/A**, not a
  pass. Run the ablation suite with a key to change that.
- **Neither deployment target has been deployed.** `fly.toml` and `render.yaml`
  are written and unexecuted; there are no Fly or Render credentials here. The
  image they point at is not unexecuted.
- **Nothing has been run under load.** One container, one worker, and no
  concurrency test above the 20-thread race in `tests/test_race.py`.

---

## 4. Decisions that are not obvious from the code

**Ceilings are reserved, not counted.** Budget is a `SUM` over reservations
computed inside the same `BEGIN IMMEDIATE` that inserts the new row. SQLite's
default deferred transaction takes its write lock at the first write, so two
concurrent authorizes would both read the same balance. `naive_reserve` is kept
deliberately so `tests/test_race.py` can print both numbers.

**Replay is a `PRIMARY KEY`, not a `SELECT`.** The `IntegrityError` *is* the
replay. A select-then-insert would be another race and a second round trip.

**Nothing in `core/` imports `rails/`.** Enforced by an AST grep in
`tests/test_invariants.py`, plus a second test that forbids any vendor name in
executable code in `core/` or `contracts/` — a string comparison against a rail
name would pass the import check and still couple the gate. Comments and
docstrings are stripped before the check, so prose may reference a rail.

**The merchant cannot spend on the buyer's behalf.** `accept_alternative`
returns a *quote the buyer signs*, not a completed order. An earlier version had
the merchant completing the purchase; that is a hole big enough to drive the
whole threat model through.

**Arm B and arm C run against a fully ablated gate.** Arm B with no gate at all
cannot transact — no settlement token — so it would measure nothing. Arm C's cap
lives in the *agent*, which is the real distinction: a client-side control is not
a control, because a compromised agent does not run it.

**`--workers 1` in the container, deliberately.** The ledger rests on SQLite's
write lock, the simulated rail's idempotency table is in memory, and the saga
runs as a background task in-process. Scaling means Postgres and a queue.

**Schema changes are additive and nullable, applied on every boot.**
`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, which
was harmless while every database was a throwaway demo file and stopped being
harmless the moment the deployment grew a named volume — a `docker pull` puts
new code in front of an old file. `core/db.py` applies `ADD_COLUMNS` guarded by
`PRAGMA table_info`. Never with `NOT NULL` (fails outright on a populated table)
and never with a `DEFAULT` (silently backfills every historical row with a value
that was never true of it — in an audit trail that is a fabricated record, which
is worse than a null). Both are asserted.

**Contracts are generated, not duplicated.** `scripts/gen_ts_contracts.py`
produces `contracts/generated.ts` from the Python enum; the console re-exports
it. A Python test regenerates and fails if the committed copy is stale. A drift
here is not a compile error — it is a console rendering an unknown code on stage.

---

## 5. Bugs found while building, and what they cost

Kept because each one is a class of mistake that will recur.

| Bug | Why it mattered |
| --- | --- |
| Upsell filter added raw addon prices to the quote total | Tax applies to the addon; the filter understated what the gate would see and offered items that then failed `CEILING_PER_TXN`. **This falsified the central revenue claim.** Now reprices the combined basket. |
| Empty merchant VPA on boot | Every headroom envelope reported `merchant_in_scope: false`, the upsell fails *closed* and withheld everything — the whole growth feature off, **health checks green**. The gate now refuses to start. |
| `RailResult` is a slots dataclass with no `__dict__` | The idempotent-replay path crashed on the second capture — exactly the call that must not double-charge. |
| Refunded order ended in `RECOVERED` | Double-counted the refunded amount into GMV. The most flattering possible bug. |
| Upsell rejections attributed from any ceiling/scope BLOCK | Reported arm D rejecting 61% of its own offers; true figure is 0%. |
| `link_recovery` moved the saga row but not the order row | Order feed and audit trail would have disagreed on screen. |
| Demo beats driven by the random basket sampler | Four of six did the wrong thing; beat 5 never reached the stockout. |
| Mounted sub-apps get no lifespan in Starlette | Would have silently disabled the sweeper, the reconciler and SSE. |

Found on 2026-08-31, all of them by building something that looks rather than by
reading code. Each is listed with what would have gone wrong on stage.

| Bug | Why it mattered |
| --- | --- |
| `/admin/reset` made blocking HTTP calls to itself, awaited on its own event loop | In the single-port build the gate and the merchant *are* this process, so the loop sat waiting on a request only it could serve, timed out after ten seconds, and 500d. The console binds that to `0`. **The reset key was dead in the deployed topology and fine in development**, where the services are separate processes. |
| Three reason codes declared and emitted by nothing | `STOCK_UNAVAILABLE`, the capture failure and `SAGA_ROLLED_BACK` were in the frozen enum while the saga wrote English into `detail`. The console had branches that had never rendered and Lane B had assertions that could never fire — a rollback on stage was prose, where the pitch claims a machine-readable trail. |
| The parity vector 404s in the deployed build | Served by a Vite dev-server middleware that does not exist in a built bundle. The badge degraded honestly to "unavailable" rather than claiming a failure, which is correct and is exactly why no test caught it. **It took opening a browser.** The one claim on screen a judge can check by looking was silently absent from the only build anyone would demo. |
| The gate's signing key was committed | Section 0. |
| The simulation harness measured a different system than it reset | `BuyerAgent` hardcoded the dev ports while `sim/run.py` read `PACT_GATE_URL` for its reset and ablate calls. With a dev topology *and* a deployed instance both up, it would reset one, measure the other, and still print numbers. |
| The cross-check was itself the wrong number | It compared a three-seed harness total against a merchant counter that is reset each seed, got a ratio near three, and shipped "the harness has a bug" in `results.md` for the life of the project. Neither was wrong. Fixed, it agrees to the paise. |
| A missing webhook signature passed verification | Found by mutating `compare_digest` to return True on an empty signature — the whole suite still passed. Nothing covered a delivery with no `X-Razorpay-Signature` while a secret was configured: the cheapest possible forgery. |

---

## 6. The numbers, including the one that does not flatter

`eval/results/results.md` is generated. Never hand-edit it.

**C vs D is unambiguous and is the headline.** PACT nets ~25% more than a naive
client-side cap, with a **0% false block rate against C's ~28%**.

**B vs D is not what the pitch wanted.** An ungated agent channel converts more,
because nothing stops it. Under this loss model it nets more below roughly **20%
adversarial traffic**. `results.md` carries a six-point sensitivity sweep with
the crossover named, rather than a rate picked to flatter us.

**This was not adjusted and should not be.** What the loss model cannot price —
chargebacks, dispute handling, an account in bad standing, the reputational cost
of accepting unauthorised agent payments — all points the other way, and is
named rather than estimated.

The strongest honest number: **arm D's net is flat across every adversarial rate
swept.** It refuses all of it. Arm B degrades linearly.

Assumptions are tabulated in `eval/README.md` with how to vary each. Arm A is
**modelled, not simulated** — there is no agent to run.

**The cross-check now agrees.** For the life of the project `results.md` carried
"the harness and the services disagree, the services are right, the harness has
a bug". Neither was wrong: it compared a three-seed harness total against a
merchant counter that is reset before each seed. Fixed, the harness's tally and
the merchant's own ledger match to the paise — which is a stronger statement
than the old line was, and it is now the one printed.

**The numbers reproduce.** The full run was regenerated on 2026-08-31 against
the single-port build, a topology it had never run against. Every measured
number is byte identical to the previous run: four arms, seed ranges, attacks,
chaos, ablation, the sweep. Only the timestamp and the cross-check line moved.

---

## 7. Open work, roughly prioritised

1. **Purge the orphaned commits, and the signing key with them.** Section 0.
   Needs `delete_repo` scope, which this environment does not have.
2. **Exercise the Razorpay path with real test keys.** Still the single largest
   unverified surface, though a much narrower one than it was: the client is now
   driven against a fake built from `API_NOTES.md`, so what remains is whatever
   the notes got wrong. Run it and diff against the expectations in that file.
3. Run the auditor with a key, then re-run `--suite ablation` so `atk_06`
   reports a real result instead of N/A.
4. **Actually deploy it.** `fly.toml` or `render.yaml`, one command, then
   `python3 scripts/smoke.py --base https://… --skip-beats` against the result.
   Do not run the beats against a public instance without meaning to.
5. Rehearsals and the backup video. Not started.
6. Load. One container, one worker, and nothing has run concurrency above the 20
   threads in `tests/test_race.py`.

Closed since this file was written: the container image is built and driven by
CI on every push and published to GHCR; the console has been opened in a browser
and is screenshotted every run; the Razorpay client and the auditor are both
exercised; `RAZORPAY_CAPTURE_FAILED` is now `RAIL_CAPTURE_FAILED` and the
layering allowlist is empty.

---

## 8. Working agreements for this repo

- **Commit and push in small increments, frequently.** Not one large push at the
  end. This was asked for explicitly.
- **No `Co-Authored-By` trailer and no session link in commit messages.** Asked
  for explicitly.
- Commit messages explain *why*, and name bugs found rather than smoothing over
  them. The existing history is the reference for tone.
- `main` is the branch. `lane-c-console` exists as a preservation branch from
  when the console was briefly removed; it is behind `main` and can be deleted
  once nobody wants it.
- Never commit anything from `_private/`.
- Test keys only. The Razorpay client refuses a non-`rzp_test_` key on purpose.

---

## 9. Where to read next

| File | For |
| --- | --- |
| `README.md` | The project, the invariants, the claims and where they are tested |
| `deploy/README.md` | The single-port build and its two silent traps |
| `eval/README.md` | Every assumption behind the numbers, and how to vary it |
| `rails/razorpay/API_NOTES.md` | What was verified against the live docs, dated, with unverified items marked |
| `console/README.md` | The three surfaces, and the signature-parity gate |
| `tests/test_invariants.py` | The rules that stop the design decaying |
| `.github/workflows/container.yml` | What is actually proven on every push, and against what |
| `scripts/smoke.py` | The six beats as assertions; runs against any instance, stdlib only |
| `console/browser-check.mjs` | The console in a real browser, and what it asserts |
| `docs/screenshots/` | What it looks like |
