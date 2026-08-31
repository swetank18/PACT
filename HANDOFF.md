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

24 commits. ~11k lines of Python, ~4.6k of TypeScript. 114 Python tests, 32
console tests, all green.

---

## 2. Running it

```bash
docker compose up --build      # everything on http://localhost:8080
```

Development, services on separate ports with hot reload:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/dev.sh                            # 8000 gate, 8100 merchant, 8110 hooks, 8300 beats
cd console && npm install && npm run dev    # 5173
./scripts/test.sh                           # 114 tests
```

`scripts/test.sh` exists because a ROS install on this machine puts broken pytest
plugins on `PYTHONPATH`; it clears the variable. Running `pytest` directly fails
with an unrelated `lark` import error.

The simulation needs the services up:

```bash
python sim/run.py --all --sessions 200 --seeds 3     # regenerates eval/results/
python sim/run.py --suite attacks|benign|chaos|ablation
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

### NOT verified — the honest boundary

- **The Razorpay client has never run against the real API.** No test keys in
  this environment. It is written from `rails/razorpay/API_NOTES.md`, which was
  written from the live docs on 2026-08-30, but that code path is unexercised.
  Everything above the adapter is tested against `mock_upi`.
  To close it: `export RAZORPAY_KEY_ID=rzp_test_… RAZORPAY_KEY_SECRET=… PACT_RAIL=razorpay`.
  The client refuses to start on a non-`rzp_test_` key.
- **The container image has never been built.** No Docker daemon here. The
  `Dockerfile` and `docker-compose.yml` are written but unproven. What *was*
  verified is the layout assumption they rest on. Expect to iterate.
- **The intent auditor has never run.** No `ANTHROPIC_API_KEY`. The gate runs in
  deterministic mode: eight checks + quote binding + a regex injection scan.
  `atk_06` targets the auditor and is reported **N/A**, not as a pass.
- **No browser screenshot of the console.** Chromium is present but hangs under
  snap confinement in screenshot mode. The UI is covered by 32 tests including a
  full jsdom mount, and it was driven end to end through its own proxy — but
  nobody has looked at it rendered.

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

---

## 7. Open work, roughly prioritised

1. **Purge the orphaned commits.** Section 0. Needs `delete_repo` scope.
2. **Exercise the Razorpay path with test keys.** The single largest unverified
   surface. `API_NOTES.md` has the field-by-field expectations to check against.
3. **Build the container once on a machine with Docker.** Expect small fixes.
4. **Look at the console in a browser.** Nobody has.
5. Run the auditor with a key, then re-run `--suite ablation` so `atk_06`
   reports a real result instead of N/A.
6. Rehearsals and the backup video. Not started.
7. `RAZORPAY_CAPTURE_FAILED` is a vendor name in the frozen reason-code enum. It
   should have been `RAIL_CAPTURE_FAILED`. Allowlisted in the layering test
   rather than hidden; worth fixing between events.

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
