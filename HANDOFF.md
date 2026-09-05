# Handoff

Context for picking this up cold. Written 2026-08-31, after the build described
below. Read this first, then `README.md`, then the per-directory READMEs.

Nothing here duplicates the code. It covers what the code cannot tell you: what
was decided and why, what is verified and what is not, and what is still open.

---

## 0. The exposure, and how it was closed

**Done on 2026-08-31. Nothing here is outstanding except one click, described at
the bottom.** Kept rather than deleted, because the next person needs to know
the history was rewritten and why.

### What was exposed

Two things, both anonymously fetchable from a public repository:

- **Internal planning documents.** Removed from `HEAD` early on and the history
  rewritten, but a force-push does not delete orphaned commits — GitHub keeps
  serving them by SHA. `00-SHARED-CONTRACTS.md` at `02b9841` returned **200**.
- **The gate's Ed25519 signing key**, `fixtures/keys/gate_signing_key.hex`. Not
  an orphan: tracked on `main` from `00cc29a` to `948c080`, so it was in every
  clone. Returned **200**.

### What it did and did not mean

Worth keeping straight, because overstating it is as bad as missing it. The key
signs headroom envelopes. Nothing in `merchant/` verifies an envelope signature
today — it fetches headroom from the gate over HTTP and trusts the transport —
and `authorize` re-derives every ceiling from the ledger server side. So a
forged envelope could never move money. What the leak destroyed was the claim
the design rests on: that a merchant can trust a signed envelope it was handed
without asking. That was false for as long as anyone could download the key.

Deployed instances were never affected: `.dockerignore` excludes key material,
`PACT_GATE_KEY_PATH` points at the mounted volume, and the gate generates its
own on first boot.

### How it was closed

Deleting and recreating the repository needs `delete_repo` scope, which was not
available. The same end state was reached with `repo` scope alone, and more
safely — the old repository is **archived, not destroyed**, so this is
reversible:

1. History rewritten with `git filter-repo --invert-paths --path
   fixtures/keys/gate_signing_key.hex`. Verified: zero occurrences across all 40
   commits, and `HEAD`'s tree hash unchanged at `b9110ec` — no file content
   moved, only history.
2. The old repository renamed to **`swetank18/PACT-pre-purge`** and made
   **private**. Every leaked object is now behind authentication.
3. A fresh public `swetank18/PACT` created, and the rewritten `main` and
   `lane-c-console` pushed to it. The old SHAs do not exist in it: the API
   returns `422 No commit found` for `00cc29a`.

### Verified closed

Anonymous, cache-busted, after the Fastly cache expired — `raw.githubusercontent`
serves a stale copy for up to five minutes after a repository goes private, so
an immediate re-check will show a misleading **200**:

| Fetched | Result |
| --- | --- |
| signing key at `00cc29a`, new repo | **404** |
| planning doc at `02b9841`, new repo | **404** |
| signing key at `00cc29a`, archived repo | **404** |
| planning doc at `02b9841`, archived repo | **404** |

The key is also refused in code. `core/ledger/headroom.py` matches the SHA-256
of its public half and **refuses to start** — so a clone predating the purge,
which still has the file on disk with `DEFAULT_KEY_PATH` pointing at it, fails
loudly instead of booting with a key anyone can download.

### The registry package, and why it was deleted

The GHCR package survived the purge bound to the **archived** repository, so the
new repo's CI failed at the publish step with `denied: permission_denied:
write_package`. Everything before it passed; only the push to the registry was
blocked.

GitHub has no API to rebind a package to a different repository — the link is
set by the first push and is otherwise a UI-only setting. So the package was
**deleted** and recreated by the next CI run, which is the documented way round
this.

Deleting it cost nothing and cleaned something up. Its eleven versions were all
tagged with pre-purge commit SHAs that no longer exist in this repository, so
every tag but `latest` was already a dead reference. The images never contained
the signing key — `.dockerignore` has excluded key material since before the
first image was built — so there was no secret to purge from them, only dead
metadata.

If it ever needs doing again by hand, the alternative is one click:
github.com/users/swetank18/packages/container/pact/settings → *Manage Actions
access* → add the repository with **Write**.

### Still true, and still the rule

The planning documents live in `_private/` locally and are gitignored. **Do not
commit them, and do not quote them into files that get committed.** Test keys
only.

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

~11k lines of Python, ~10k of TypeScript. **186 Python tests, 55 console
tests**, all green, plus two GitHub Actions workflows that build the container
image and drive the six demo beats and every console surface against it on
every push.

Added 2026-09-05: a fifth console surface, `#/firewall` — the **principal's**
console, built to `userUI(1).md`. The other four answer the merchant's
questions; this one answers the person whose money it is. Six tabs, light mode,
its own kill switch. `console/README.md` has the detail; the two decisions
worth carrying are in section 4.

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
python3 scripts/load.py  --base http://localhost:8080      # ceiling + throughput
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

Added 2026-09-05:

- **The demo runs end to end, repeatably, and there is a video of it.**
  `console/demo-video.mjs` drives the real console — grant, sign on device,
  checkout, six beats, the principal's console, the audit trail, six slides —
  and fails the take on any console error, failed request, 4xx, or beat that
  does not finish. Six consecutive takes at 65.6–65.7 s with phase times
  identical run to run. The committed take is `docs/demo/pact-demo.webm`; CI
  records one against the image it publishes on every push.
- **Two hours of steady load, and the ledger still agrees to the paise.** 49,501
  purchases at 6.9/s: no transport or server errors, 593,961 SSE frames to one
  subscriber held open throughout with zero reconnects, descriptors ending lower
  than they started, p50 flat at 66–67 ms, the saga draining the instant the
  load stopped, and ₹4,86,50,572.82 matching the merchant's own count exactly.
  **One thing failed and it is real** — see the memory bullet below.
  `docs/soak.md` has all of it.
- **The deployment manifests agree with the image.** `fly.toml`, `render.yaml`,
  `docker-compose.yml` and the Dockerfile are cross-checked on port, health
  path, the `/data` mount for both the database and the signing key, one
  instance, and no credential in any of them. Mutation-checked.

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
- **Render has now been deployed. Fly has not.** `https://pact-9btr.onrender.com`,
  created 2026-09-05 through `scripts/render.py` against Render's API. Live on
  the first attempt: `/healthz` green with all four sub-apps mounted, 5/5 smoke
  checks — including the two that bit before, the parity vector *is* served and
  the signing key is *not* — and every console surface plus all six firewall
  tabs driven in Chromium with no console error and no failed request. Beat 4
  ran against it: four attacks, four blocks, each on a different check.
  `fly.toml` is still unexecuted; there are no Fly credentials here.

  **It is on the free plan, which is not what `render.yaml` describes.** Starter
  was refused with a 402 — no card on the workspace — so it runs with no disk.
  That is a materially different thing and the difference is not cosmetic:
  `/data` is ephemeral, so a restart issues a **new gate signing key** and
  empties the ledger, and every headroom envelope already handed out stops
  verifying. The instance also sleeps after ~15 minutes idle, and asleep it is
  not running the reservation sweeper or the reconciliation poller. Add a card
  and `RENDER_API_KEY=… python3 scripts/render.py create --plan starter` builds
  the one the blueprint actually specifies.
- **Load has now been run, and the limit is known rather than guessed.**
  `scripts/load.py` at 32 concurrent buyers: 200/200 purchases, 53/s, p50 396
  ms, p99 1.6 s. At 64 it saturates — p50 20 s, a third complete — and it
  degrades by *refusing* rather than by settling without live authority. The
  ceiling holds throughout: twenty buyers racing one mandate spend ₹13,564.10
  against a ceiling of ₹13,564.10, exact in both directions.
- **Memory climbs under sustained load, and nothing here has run long enough to
  say where it stops.** The two-hour soak is the first run to look: RSS goes
  from 88 MB cold to 154 MB and is still climbing at **21 MB/hour** at the end,
  which reaches the 512 MB `fly.toml` asks for in about seventeen hours of
  continuous load. A control run separates it from the comfortable explanation:
  a *fresh* process against the same 306 MB database sits flat at 111–114 MB, so
  roughly 40 MB of the 154 is process history rather than working set and a
  restart reclaims it. It is anonymous heap — `smaps_rollup` says 107 of 132 MB
  anonymous, `VmData` 336 MB against 133 MB resident, which is the shape of an
  allocator holding freed memory. **That is a hypothesis, not a finding**, and
  the mechanism is unidentified. A 66-second demo is nowhere near it; leaving an
  instance deployed over a weekend is.
- **The volume grows at 5,900 bytes an order and nothing reclaims it.** 139 MB
  an hour at the rate the soak ran, so the 1 GB both manifests ask for holds
  about five hours of continuous load. A third of that is `decisions`, the audit
  trail, which is the part that must not be pruned. `docs/soak.md` has the
  per-table anatomy and the reason the one prunable table is not worth going
  near the replay defence for.
  Still **not** measured: what happens at the moment the volume is actually
  full, a run of more than two hours, and a restart under load.

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

**The principal's kill switch revokes; it does not set a flag.** There is no
pause in the mandate lifecycle, and a flag held in the browser would be a
client-side control — the thing `eval/results/results.md` spends a paragraph
explaining is not a control. So "pause all agents" revokes every live mandate at
the gate. Because a revoked mandate reports **zero** headroom by design, the
remainder on each one is read *before* the revoke; that is the only moment it
exists. Resume re-signs each mandate with exactly what was left, never with what
it started with, and skips any whose window has closed. The same reading applies
on screen: a paused mandate shows the captured remainder rather than the
envelope's zero, because rendering the zero would claim it was fully spent.

**The firewall never shows a number the engine did not produce.** The health
score, the threat card and the budget bars are all functions of the headroom
envelope and the decision list. Where there is no answer the screen says so. The
sharpest case is the intent meter: `userUI(1).md` asks for a confidence
percentage, and `core/gate/auditor.py` answers `matches_intent` as a **boolean**.
The meter renders the engine's actual answer and says on screen that the auditor
answers yes or no — inventing a percentage to fill the bar would have been the
one thing on that screen a judge could not check.

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
| Both SSE streams tore down and resynced every fifteen seconds | `sse_starlette`'s keepalive is an SSE **comment**, and a comment fires no event in the browser — so `stream.ts`'s idle watchdog was never fed and treated a healthy pipe as dead, forever. The console survived it, because a visible reconnect is what that module is built for, but the "live" dot flickered and every reconnect refetched. Both streams now send a named `heartbeat`, which the console has always listened for. Found on 2026-09-05 only because the browser check grew long enough to run past fifteen seconds; a harness with no watchdog counted 7,395 frames and zero reconnects over the same window. |
| The simulation harness measured a different system than it reset | `BuyerAgent` hardcoded the dev ports while `sim/run.py` read `PACT_GATE_URL` for its reset and ablate calls. With a dev topology *and* a deployed instance both up, it would reset one, measure the other, and still print numbers. |
| The cross-check was itself the wrong number | It compared a three-seed harness total against a merchant counter that is reset each seed, got a ratio near three, and shipped "the harness has a bug" in `results.md` for the life of the project. Neither was wrong. Fixed, it agrees to the paise. |
| A missing webhook signature passed verification | Found by mutating `compare_digest` to return True on an empty signature — the whole suite still passed. Nothing covered a delivery with no `X-Razorpay-Signature` while a secret was configured: the cheapest possible forgery. |
| A gate timeout was reported as `TOKEN_INVALID` | The merchant refuses the order, which is right. But that code means "that settlement token is not valid" and reads as forgery, so a load spike put 97 of them in the audit trail in one run — pointing whoever read it at an attacker who did not exist. Now `GATE_UNAVAILABLE`, which still blocks. |

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

Regenerated again on **2026-09-05**, after a day of changes to the console, the
merchant's admin surface, the deployment and the test suite. `git diff` on
`eval/results/` is **two lines, both the generated timestamp**. Nothing else in
either file moved — which is the point of running it rather than assuming it:
the changes were all meant to be outside the pricing, gate and upsell paths, and
now that is measured rather than believed.

---

## 7. Open work, roughly prioritised

1. **Exercise the Razorpay path with real test keys.** The largest remaining
   unverified surface — see section 3 for exactly how narrow it now is.
   The client is now driven against a fake built from `API_NOTES.md`, so what
   remains is whatever the notes themselves got wrong. Run it and diff against
   the expectations in that file.
2. Run the auditor with a key, then re-run `--suite ablation` so `atk_06`
   reports a real result instead of N/A.
3. **A deploy with a disk on it.** Render is live but on the free plan, which
   has no disk — so its ledger and signing key do not survive a restart, which
   is most of what a deployment is for. `RENDER_API_KEY=… python3
   scripts/render.py create --plan starter` builds the one `render.yaml`
   describes the moment there is a card on the workspace. Fly is untouched:
   `./scripts/deploy.sh fly` runs the preflight, the deploy and the smoke test,
   and refuses clearly without credentials or a volume.
4. **Find out where the memory line goes.** Section 3: 21 MB/hour, unidentified,
   reclaimed by a restart, seventeen hours from the 512 MB the machine has. An
   overnight run of `scripts/soak.py` answers whether it flattens; nothing here
   has run one. It does not touch the demo, and it decides whether this can be
   left deployed.
5. **The human rehearsals.** The machine's are automated and green — six clean
   takes, `docs/RUNBOOK.md` — and the backup video is recorded and committed.
   What no script can do is six run-throughs out loud, timed, on the laptop that
   will be used, at least one through the projector.

Closed since this file was written: **the exposure in section 0** — history
rewritten, the old repository archived private, a clean one pushed, all four
fetches verified 404; the container image is built and driven by CI on every
push; the console has been opened in a browser and is screenshotted every run;
the Razorpay client and the auditor are both exercised; `RAZORPAY_CAPTURE_FAILED`
is now `RAIL_CAPTURE_FAILED` and the layering allowlist is empty.

Closed on 2026-09-05: **the backup video** is recorded, committed and re-recorded
by CI on every push, with a written run of show and six clean rehearsal takes;
**the soak** has been run for two hours and both questions it existed to answer
now have numbers, including one that failed; **Render is deployed**, with the
free-plan caveat stated rather than glossed; and the deployment manifests are
cross-checked against the image they deploy.

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
  once nobody wants it. Both were re-pushed to the new repository after the
  purge, and both are clean.
- **The history was rewritten on 2026-08-31.** Every SHA before that date
  differs from the one in the archived repository. Any clone taken before the
  purge should be re-cloned rather than pulled — a pull will conflict on every
  commit.
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
| `docs/RUNBOOK.md` | The run of show: what to press, what to say, and what to do when it breaks |
| `docs/soak.md` | Two hours under load: the memory line that failed, and what is in the volume |
| `docs/demo/pact-demo.webm` | The backup video. Know where it is before you need it |
| `docs/screenshots/` | What it looks like |
