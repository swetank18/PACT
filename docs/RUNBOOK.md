# Run of show

The stage script. What to press, what to say while it runs, and what to do when
something breaks.

Nothing here is typed on stage. The whole demo is eleven key presses, and the
machine time between them is measured rather than estimated — the numbers below
came out of `console/demo-video.mjs`, which drives this exact sequence against a
real instance and records it.

The backup video is [`docs/demo/pact-demo.webm`](demo/pact-demo.webm). **Know
where it is before you need it.**

---

## Before you go on

Five minutes, in this order. Each step exists because it has failed at least
once.

```bash
docker compose up -d --build                      # or the published image
python3 scripts/smoke.py --base http://localhost:8080
```

Thirteen checks, about thirty seconds, and it drives all six beats. If this is
green the demo works *on this machine, right now* — which is a different claim
from "it worked yesterday" and is the only one that matters.

Then, in the browser:

- [ ] Open `http://localhost:8080`. Press `0`. The board clears and the strip
      shows the reset time in milliseconds.
- [ ] The header reads **SIGNATURE PARITY · 2 VECTORS**. If it says
      *unavailable*, the browser is not verifying against the engine's vector
      and the strongest claim on screen is not being made. This was silently
      broken once in the built bundle and nothing but looking caught it.
- [ ] Both connection dots by `GATE` and `MERCHANT` are live.
- [ ] Full screen. 1280×800 or larger, the console is built to be read at ten
      metres.
- [ ] Notifications off. Nothing else running that can steal focus — the beats
      are bound to number keys, and a chat window taking focus eats them.
- [ ] The backup video is on this laptop, plays, and you know the shortcut for
      full screen.

**The venue's network is not on the critical path.** Everything runs in one
container on localhost, including the rail. Worth one sentence on stage, because
every other demo in the room is one dropped packet from dying.

---

## The sequence

Phase times are what the recorder measured on a laptop, and include the pause
that lets the screen be read. The container is slower than development — its
saga step delay is 0.35 s against 0.05 s — so budget a little more.

| # | Press | ~time | On screen | The line |
|---|---|---|---|---|
| 0 | `0` | 4 s | The board clears | "Nothing up my sleeve. This is a cold instance." |
| 1 | *Grant* → **Grant and sign** | 4 s | A real Ed25519 signature, then the mandate chip in the header | "The human signs the delegation **on their device**. The agent never gets the key. It carries a signed envelope — ₹15,000, five purchases, three categories." |
| 2 | *type nothing*, **Send** | 7 s | Quote card, headroom bar, gate verdict | "The agent shops. The merchant prices it server side and the gate decides on authority alone." |
| 3 | `1` | 4 s | An order, a decision, eight checks in order | "End to end. Discovery, mandate, quote, gate, settlement — milliseconds, and every check is on screen including the ones that were skipped." |
| 4 | `2` | 4 s | An addon offered and accepted, AOV rises | "**This is the product.** The merchant read the buyer's remaining authority *before* it made the offer, so the offer was provably approvable." |
| 5 | `3` | 2 s | The same offer, made blind, rejected | "The obvious build. Recommend, then find out. `CEILING_PER_TXN` — a failed offer and a buyer who now distrusts the agent." |
| 6 | `4` | 2 s | Four blocks, four reason codes | "Replay, a merchant outside scope, a tampered amount, a prompt injection in a product description. Each one a machine-readable code, not prose." |
| 7 | `5` | 7 s | Capture, fulfilment fails, refund, budget released, alternative accepted | "The money has already moved and the warehouse is empty. Refund, the ceiling gives the money back, an alternative is offered — and the buyer signs it. **The merchant cannot spend on the buyer's behalf.**" |
| 8 | `6` | 2 s | Nothing happens, visibly | "The rail delivers the same webhook twice. The second one does nothing, because replay is a primary key rather than a check." |
| 9 | — | 3 s | The audit trail | "Every decision and every saga step, in order, with codes." |
| 10 | *Pitch*, `→` ×5 | 17 s | Six slides, ending on limitations | "Including what we have **not** verified." |

**About a minute of machine time.** The rest is narration, so the pacing is
yours; the numbers above are the floor, not the plan.

If you have to cut, cut in this order: beat 6, the audit trail pause, beat 1.
Never cut beat 2 or beat 3 — they are the contrast the whole pitch rests on —
and never cut beat 5, which is the brief's explicit ask.

---

## When it breaks

| What happened | Do this |
|---|---|
| A beat does nothing | Press `0`, then the beat again. The strip shows what it is doing; the buttons disable while a beat runs. |
| The strip says *simulation runner not reachable* | The container is down. `docker compose up -d`, wait for healthy, `0`. If that is not immediate: **play the video.** |
| A dot by `GATE` or `MERCHANT` is dead | Reload the page. SSE reconnects on its own, but a reload is faster than explaining. |
| The parity badge says *unavailable* | Say so and move on — it is honest degradation, not a failure. Do not claim the parity check passed. |
| The console is blank | Reload. If still blank, the console bundle is not being served: `curl localhost:8080/healthz` reports `console: false`. **Play the video.** |
| Numbers look wrong | Press `0` and start again. Never explain a number you did not expect; the reset takes under a second. |
| Anything at all, twice | **Play the video and keep talking.** It is 56 seconds, captioned, and it is the same demo. |

The one rule: **do not debug on stage.** Every failure above resolves to either
one key press or the video.

---

## Rehearsals

Two different things, and only one of them can be automated.

**The machine's rehearsal is automated and it is green.** `demo-video.mjs`
drives the full sequence — grant, sign, checkout, all six beats, the audit
trail, the slides — and fails the take on any console error, any failed request,
any 4xx, or any beat that does not finish.

```bash
cd console && node demo-video.mjs http://localhost:8080 ../docs/demo
```

Six takes on 2026-09-05, back to back against the single-port build:

| Take | Total | |
|---|---|---|
| 1 | 55.9 s | clean — kept as `docs/demo/pact-demo.webm` |
| 2 | 55.8 s | clean |
| 3 | 55.8 s | clean |
| 4 | 55.8 s | clean |
| 5 | 55.7 s | clean |
| 6 | — | **failed**, and correctly: another process on the machine killed the instance mid-take, and the recorder refused it rather than saving 56 seconds of `ERR_CONNECTION_REFUSED` as the backup video |

Phase times were identical to a tenth of a second across every clean take, which
is the property that matters: **the sequence does not drift**. The one that
failed is the more useful result — that is exactly what the take gate is for.

**The human's rehearsal is not automated and is not done by reading this.** Six
run-throughs out loud, timed, with the laptop you will use, at least one of them
with the projector. The lines above are a script to adapt, not to read.

---

## Related

| File | For |
|---|---|
| [`../HANDOFF.md`](../HANDOFF.md) | What is verified and what is not. Read section 3 before answering a judge's question about coverage. |
| [`../scripts/smoke.py`](../scripts/smoke.py) | The preflight, and the six beats as assertions |
| [`../console/demo-video.mjs`](../console/demo-video.mjs) | The recorder, and the run-of-show timings |
| [`../eval/results.md`](../eval/results.md) | Every number that appears on the results slide |
