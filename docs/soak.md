# The soak

Two hours of steady load against one instance, watching the shape of the process
rather than its speed.

`scripts/load.py` answers "does it fall over in a burst" and takes about a
minute, so everything it reports is a peak. The two questions that decide
whether a deployed instance survives a weekend are slow, and neither is visible
at sixty seconds:

- **Does memory come back down?** `fly.toml` asks for a 512 MB machine.
- **Does the volume fill?** Both manifests ask for a 1 GB disk.

A listener that is never removed, a saga task that is never awaited, a WAL that
is never checkpointed — all three are invisible in a burst and fatal over hours,
and they present in production as a health check that stays green until the
process is OOM-killed mid-purchase.

```bash
python3 scripts/soak.py --base http://localhost:8080 --minutes 120 \
    --db pact-soak.db --json soak.json
python3 scripts/soak.py --replay soak.json      # re-judge it later, free
```

---

## Results

**2026-09-05. Two hours, four concurrent buyers, 49,501 purchases at 6.9/s
against the single-port build. One failure, and it is a real one.**

| | | |
| --- | --- | --- |
| Transport and server errors | **none** in 49,501 purchases | OK |
| RSS | 88 MB cold → **154 MB**, climbing **+21 MB/hour** and not flattening | **FAIL** |
| File descriptors | 105–110, ended on 105 | OK |
| Threads | 27, throughout | OK |
| Latency | p50 66 ms at the start, 67 ms at the end (1.02×) | OK |
| SSE | 593,961 frames to one subscriber held open for two hours, **0 reconnects** | OK |
| Database | 27 → 283 MB, **5,900 bytes per order**, 139 MB/hour | measured |
| Saga drain | in-flight orders finished **0.0 s** after the load stopped | OK |
| Ledger | 49,501 orders, ₹4,86,50,572.82, agreeing **to the paise** with what the harness settled | OK |

The one failure is the memory line and it is worth having. At 21 MB/hour a
512 MB machine is reached in about **17 hours** of continuous load at this rate.
Nothing about a demo goes near that — the run of show is 66 seconds — but "leave
it deployed over a weekend" does, and before this run nobody knew.

Everything else is the answer you want. The descriptor count is the one that
would have been most expensive to get wrong: one SSE subscriber, held open for
two hours across 593,961 frames, and the process ends holding fewer descriptors
than it started with.

---

## What it does

Four buyers, each placing a full purchase — fresh mandate, quote, authorize,
order — and then pausing half a second. A fresh HTTP client per purchase on
purpose: that is what a fleet of agents actually looks like, and a pooled client
would hide a file-descriptor leak on the server by never opening a new socket.

Alongside them, for the whole run:

- **One SSE subscriber**, held open from the first second to the last. This is
  the leak that matters most here — a subscriber queue that is never drained or
  a listener never removed on disconnect is the shape that only goes up.
- **A console poller**, hitting the three feeds the merchant console renders
  every two seconds. A browser left open on that console for the length of an
  event is the most likely long-lived client this will ever have, and those
  feeds are the ones whose result sets grow with every order.
- **A restocker**, every five seconds. Stock is finite and in memory — forty of
  the SKU the buyers want — so without it the first minute exhausts the shelf
  and every purchase after that captures, fails to fulfil and refunds. That path
  is worth soaking and it has its own tests; it is not what this is measuring,
  and it makes the merchant's GMV disagree with the harness for a reason that
  says nothing about the instance.

The rate is deliberately modest. `scripts/load.py` found the ceiling at 53
purchases a second and saturation at 64 concurrent buyers; this runs at about an
eighth of that, because a soak that runs at the ceiling measures the ceiling.

---

## What it asserts

| | |
| --- | --- |
| No transport or server errors | A soak that 5xxs is not a soak |
| RSS is flat, and never above the machine | Two failures, not one: a flat 480 MB does not survive a 512 MB machine, and 8 MB/hour is a leak even when every sample is small |
| File descriptors do not accumulate | The classic SSE listener leak |
| Threads do not accumulate | |
| Latency at the end matches the start | A slow leak shows here before it shows in RSS |
| The saga drains after the load stops | A saga still working a minute later is a finding |
| The merchant's ledger agrees to the paise | The harness counts what it settled; the merchant counts independently, server side, from its own tables |

Two of those thresholds were wrong at first and are worth knowing about, because
both were the harness being confidently wrong rather than the instance being
broken:

**The trend is fitted, and start-up is discarded.** First sample against last
turned a 1.6 MB warm-up step in a ninety-second trial into "63 MB/hour, dead in
six hours". It is a least-squares fit now, over samples after the first ten
minutes, and it is not judged at all on a run shorter than fifteen. The peak is
still taken over every sample including start-up, because a machine that OOMs
while warming up is just as dead.

**The ledger check waits for the saga.** The workers stop at the deadline with a
few purchases still in flight — captured, not yet fulfilled, because the saga is
a background task. Reading the merchant's stats at that instant reported six
missing orders as a ledger mismatch.

---

## Memory: it climbs, then it stops

RSS is not flat from the first second and should not be expected to be.
`core/db.py` opens a SQLite connection per thread, lazily, and each one takes its
own page cache, so memory steps up until every worker thread in the pool has
served a request and then flattens.

**A control run says the level is a property of the process, not of the data.**
The same code, the same load, but started against the 306 MB database the long
run had just produced:

| | Two hours, from an empty database | Fifteen minutes, from a 306 MB one |
| --- | --- | --- |
| RSS | 88 → 154 MB, +21 MB/hour | **111–114 MB, flat** (−8 MB/hour, which is noise) |
| Bytes per order | 5,900 | 5,903 |

A fresh process serving the *same* data needs about 114 MB. The two-hour-old
process was holding 154 MB to do the same work, so roughly **40 MB of it is
process history rather than working set, and a restart reclaims it**. That rules
out the comfortable explanation — this is not simply page caches sized by the
database — and it rules out the alarming one, a leak that grows without bound in
live objects, at least on this evidence.

What it is has not been identified. It is anonymous heap: `smaps_rollup` at 93
minutes showed 107 MB of the 132 MB RSS as anonymous and only 3 MB file-backed,
with `VmData` at 336 MB against 133 MB resident, which is the shape of glibc
arenas holding freed memory rather than returning it. That is a hypothesis, not
a finding.

The two runs agreeing on bytes-per-order to 0.05% — 5,900 against 5,903, from
completely different starting states — is the strongest evidence here that both
measurements are sound. It also found a bug in this harness: the per-order
figure divided post-warm-up growth by *every* order the run settled, which
understates by whatever fraction of the run the warm-up was. Eight percent over
two hours, three-fold over fifteen minutes. Fixed, and the two runs agree.

That is why the trend fit ignores the first ten minutes. Fitted through the
warm-up, this run reports a leak. Fitted after it, it reports the truth.

---

## The volume: what is actually in it

The interesting half. Measured with `dbstat` against the soak's own database
part way through the run, at 12,527 orders and 70.5 MB. The shares are what
matter; the totals kept growing to 283 MB, at the same 5,900 bytes an order:

| | Share | Bytes per order |
| --- | ---: | ---: |
| `decisions` | 34.8% | 2,053 |
| `mandates` | 17.4% | 1,027 |
| `saga_steps` (+ its index) | 14.7% | 867 |
| `quotes` | 10.0% | 587 |
| `orders` | 5.8% | 342 |
| `settlement_tokens` | 3.5% | 206 |
| `reservations` | 2.7% | 158 |
| `nonces` (+ its index) | 2.3% | 136 |
| everything else — indexes | 8.8% | 521 |

**5,897 bytes per order, and nothing reclaims any of it.** Every table is
append-only; the sweeper releases expired *reservations*, which changes their
state rather than removing rows.

At the rate this ran — 6.9 purchases a second — that is about 144 MB an hour, so
the 1 GB both manifests ask for holds roughly **seven hours** of continuous
load. At the 53/s ceiling `load.py` found it is 1.1 GB an hour, and the same
disk holds **under one hour**. Neither is anywhere near a demo, and neither is a
production number: the answer to both is a bigger disk or a stated retention
period, and now there is a figure to size either against.

Two things follow, and they point in opposite directions.

**The audit trail is a third of it, and it is the part you must not prune.**
`decisions.body_json` is 1.2 KB of the 2 KB row: the whole serialised decision,
which is the ten-element check chain with each check's status, timing and
detail. That is not overhead — it is what the transaction drawer renders and
what a dispute would rest on. The biggest table being the audit trail is the
system storing what it says it stores. Deleting it to save disk is deleting the
thing being demonstrated, so retention there is an operator's policy decision —
how long must this be able to answer "why was that payment allowed" — and not
something a sweeper should decide.

And 34.8% is a **floor**, not a midpoint. The harness artifact below inflates
`mandates`, not `decisions`: under a real buyer the mandate rows amortise across
several purchases while decision rows stay at one per authorize, and more than
that once blocked attempts are counted. Take the artifact out and the audit
trail's share goes up.

**`nonces` is prunable by construction, and it is not worth it.** The chain runs
`freshness` (check 5) before `replay` (check 6) and short-circuits, so a request
too old to pass freshness never reaches the nonce table at all — which means a
nonce old enough can never be consulted again. True, and it buys 136 bytes an
order out of 5,897. Against 2.3% it is not worth going near the replay defence,
and the horizon is subtler than it looks: freshness is `abs(now - issued_at)`,
so a request dated 59 seconds in the future is still fresh 118 seconds after its
nonce row was written. Any such prune must retain **twice** the freshness window,
not one. Written down here so nobody re-derives it as a good idea and gets the
arithmetic wrong.

The number that is genuinely harness-inflated: `mandates`, at 17.4%. This issues
one mandate per purchase to keep the ledger arithmetic clean. A real buyer
spends several purchases against one mandate, so the real per-order figure is
lower than 5,897.

---

## What this still does not answer

- **Where the memory line goes.** Two hours established that it climbs at
  21 MB/hour and that a restart reclaims it. Whether it flattens at some level
  below 512 MB, or reaches it in the seventeen hours the slope implies, needs a
  run of that length. Nothing here has run overnight.
- **Days, not hours.** Two hours is enough to separate warm-up from a leak. It
  is not enough to see a slow fragmentation, a log rotation, or a certificate
  expiring.
- **The volume actually full.** The growth rate is measured and the fill time
  follows from it. What SQLite and this code do at the moment the disk returns
  `SQLITE_FULL` has not been tested.
- **A restart under load.** The container survives a restart with the volume
  attached — CI asserts it on every push — but not while sixty buyers are
  mid-purchase.
- **Anything about a machine that is not this one.** The numbers are from a
  16-core laptop. The 512 MB and 1 GB in the manifests are what a deploy would
  actually get, which is why they are the thresholds this asserts against.
