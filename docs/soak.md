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
| RSS | 88 MB cold → **154 MB**, climbing **+21 MB/hour** and not flattening | **FAIL** — found and fixed, below |
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

**That line has since been found and closed**, and the fix was confirmed on
2026-09-07 by a run that crosses the fix while it is running: 620 bytes a
purchase before, 7 after. The simulated rail was keeping every intent it ever
created. See *Found: the rail remembered every purchase*, below. The table above
is left as it was measured on the day — it is the run that found the problem,
and rewriting it would delete the evidence.

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

## Memory: what this run could and could not say

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
out the comfortable explanation: this is not simply page caches sized by the
database.

**What it did not rule out, and was read as ruling out.** This section used to
end by saying the control run also ruled out the alarming explanation — a
structure growing without bound in live objects. It does not, and the reasoning
was wrong in a way worth keeping rather than quietly deleting. A control run
starts a *fresh* process, and a fresh process has no history of either kind:
neither allocator retention nor a live map that grows one entry per purchase.
Both present identically as "reclaimed by a restart". The control separates
working set from process history. It cannot separate the two kinds of history,
and the conclusion drawn from it was the one that happened to be comfortable.

The other reading offered here was that it is anonymous heap held by the
allocator: `smaps_rollup` at 93 minutes showed 107 MB of the 132 MB RSS as
anonymous and only 3 MB file-backed, with `VmData` at 336 MB against 133 MB
resident. That observation is real and it is still true. It is also what a
growing Python dictionary looks like.

---

## Found: the rail remembered every purchase

**2026-09-06.** `rails/mock_upi/adapter.py` kept three maps — `_intents`, the
`_by_payment` alias onto the same objects, and `_idem`, the idempotency record —
and **nothing ever removed an entry from any of them**. One `_Intent`, one alias
and one `RailResult` per purchase, held for the life of the process. It is the
only unbounded per-transaction structure in the codebase: `EventBus` caps each
subscriber queue at 256 frames, the catalog's stock map is fixed at the SKU
list, and every other collection lives in SQLite.

Measured three ways, which agree:

| | | |
| --- | --- | ---: |
| The maps alone, quiesced, with real key shapes | 50,000 purchases | **662 bytes each** |
| A live instance, idle RSS against purchases | 25,070 purchases | **663 bytes each** |
| The same instance under `tracemalloc` | 12,022 purchases | **662.8 bytes each** |

Three instruments that fail differently, agreeing to a byte. The idempotency key
is a sha256 hexdigest, so 64 characters of it is string; the rest is the intent,
its two dictionary entries and the result.

`tracemalloc` is the one that settles what kind of memory it is. RSS cannot tell
"something holds a reference" from "the allocator kept freed pages"; tracemalloc
only sees live Python allocations, and it named the lines:

```
+175.8 B/purchase  rails/mock_upi/adapter.py:118  _Intent(intent_id=new_id(...))
+104.9 B/purchase  rails/razorpay/client.py:81    idempotency_key(...).hexdigest()
 +87.9 B/purchase  rails/mock_upi/adapter.py:154  RailResult(ok=True, ...)
 +71.9 B/purchase  rails/mock_upi/adapter.py:122  intent.payment_id = ...
 +25.9 B/purchase  ×3, the three dictionary inserts
```

with a live-object census of **+1.00 `_Intent` and +1.00 `RailResult` per
purchase**, never released. So the allocator was never the story. It was a
reference, held on purpose, by code that had no reason to let go.

### The fix, and why a cap rather than a sweeper

`MAX_REMEMBERED = 20_000`, oldest evicted first. A real rail does not keep your
intents in your process, and a simulated one has no business doing it either.

The two maps degrade into each other rather than off a cliff. An intent that
outlives its idempotency record still answers a replayed capture, because its
status is already `captured`; an idempotency record that outlives its intent
still returns the original result. Only a purchase old enough to have lost both
is forgotten, and by then it settled 20,000 purchases ago — forty-eight minutes
at the soak's rate, and further past anything that replays than the saga's
retries, which are seconds apart. The ceiling is about 13 MB.

### Confirmed: 2026-09-07, two hours, the slope changing inside one run

The run that settles it is a normal `scripts/soak.py` at the reference settings
— four buyers, half a second of think time, 6.8 purchases a second — carried
past 20,000 purchases so the cap engages **while it is running**. The same
process, the same load, the same database, before and after:

| Fitted against purchases, not the clock | | |
| --- | ---: | ---: |
| Before the cap, 3,000–18,000 purchases | **620 bytes a purchase** | 15.4 MB/hour at 6.9/s |
| After the cap, 21,000 purchases on | **7 bytes a purchase** | 0.17 MB/hour |

Seven bytes, against `tracemalloc`'s 8.7 measured a different way. The harness's
own verdict on the same run is **PASS**, RSS 116–127 MB, and it no longer
matters much what the residual is: it is not distinguishable from noise, and the
fit over the last third of the run is very slightly *negative*.

**Why this is fitted against purchases.** The laptop suspended twice mid-run, for
ten minutes and then twenty-nine. Wall-clock time advances through a suspend and
the process does no work, so a clock-based trend is flattered by exactly the
dead time — the harness reported +2.0 MB/hour, and that number is too kind.
Indexing on purchases is immune to it, and it is also what makes the before and
after comparable when the rate wanders.

**A run shorter than 20,000 purchases cannot see this fix.** Two attempts here
measured nothing and looked like they had measured something: a forty-minute
soak that settled 16,483 purchases, and a 25,000-purchase comparison where the
cap only bit in the last fifth. Both reported the *old* behaviour. If you are
checking this, count purchases, not minutes.

An earlier confirmation used a cap of 2,000 rather than the shipped 20,000, so
that eviction would start early enough to measure inside a short run: the maps
sat at exactly 2,000 entries while the counter behind them reached 25,070, and
`tracemalloc` fell from 662.8 to **8.7 bytes a purchase** with `_Intent` and
`RailResult` gone from the census entirely.

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

- **Days, rather than the two hours this now passes.** The line is flat after
  the cap — 7 bytes a purchase, and the harness passes the run — but the longest
  clean stretch here is still one soak, and nothing has run overnight. What is
  left to find at 7 bytes a purchase is not a leak; it is whether something else
  appears at hour nine that two hours cannot show.
- **The RSS floor, which is not zero.** The process settles around 116–127 MB
  serving a 200 MB database, and that level is a property of the process rather
  than of the data — the control run established it and the fix did not change
  it. A 512 MB machine holds it comfortably. A smaller one would need the SQLite
  page cache sized deliberately rather than left at its default per connection.
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
