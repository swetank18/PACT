# console

The surfaces a judge sees, plus the one piece of real cryptography that runs on
the buyer's device.

```bash
npm install
npm run dev          # mock services + vite, http://localhost:5173
npm test             # signature parity and render smoke tests
```

`npm run dev` starts the dev mock alongside Vite. To run against the real
services instead, start them and run `npm run vite` on its own.

## The surfaces

| Route         | What it is                                                                   |
| ------------- | ---------------------------------------------------------------------------- |
| `#/grant`     | The human writes a delegation and signs it on this device.                     |
| `#/checkout`  | The conversation with the buyer agent: quote, headroom, upsell, gate result.   |
| `#/console`   | The merchant console: revenue, live orders, gate decisions, audit trail.       |
| `#/firewall`  | The principal's own console. Six tabs, light mode, its own kill switch.        |
| `#/slides`    | Six slides, in the app so there is no window switch on stage.                  |

## The firewall surface

`src/surfaces/firewall/`. The other four surfaces answer the merchant's
questions; this one answers the person whose money it is — what are my agents
doing, what was refused on my behalf, and how do I stop it. It takes the whole
viewport, brings its own light palette (declared on one class, so it and the
dark console cannot bleed into each other), and hash routes its own tabs at
`#/firewall/<tab>`.

Three rules it is built around, and each of them shows up as something the
screen refuses to say:

**Nothing on screen is a number the engine did not produce.** The health score,
the threat summary and the budget bars are all functions of the headroom
envelope and the decision list. Where the engine has no answer the screen says
so — a mandate whose envelope did not arrive reads "the gate did not answer"
rather than showing zero spend, and the intent meter says the auditor answers
yes or no rather than inventing a confidence percentage to fill a bar.

**The kill switch is server side.** "Pause all agents" revokes every live
mandate at the gate. A flag held in this browser would be a client-side control,
and `../eval/results/results.md` spends a paragraph on why those are not
controls. Because there is no pause in the mandate lifecycle, the remaining
authority on each mandate is read *before* the revoke — a revoked mandate
reports zero headroom, so that is the only moment the remainder can be captured
— and resume re-signs each one with exactly what was left, never with what it
started with.

**The ablation matrix is the measured run.** It is rendered straight out of
`../eval/results/raw.json`; a cell is green because the attack *leaked* with
that check switched off, not because someone decided it ought to. `atk_06`
reads `n/a` rather than as a pass, because it needs the model auditor and there
is no key in this build.

What lives on the device rather than at the gate is the mandate bodies this
browser signed, the agent names, and the preferences — in `localStorage`, with
the same caveat the device key carries and the settings page states outright.

## Signature parity is the blocking gate

A signature produced in this browser has to verify inside the engine. That is
proven two ways and both must be green:

```bash
npm run parity       # under node, byte equality against the cross language vector
```

and the badge in the top bar, which runs the same assertions in the actual
browser at boot. If the badge says FAILED, nothing else in this directory
matters — fix that first.

The vector lives at `../fixtures/keys/test_vector.json` and is regenerated with
`python3 ../scripts/gen_test_vector.py`. The Python and TypeScript
canonicalisers were written separately from RFC 8785 on purpose; parity between
two ports of the same code proves nothing.

**Where the vector comes from differs by build, and that bit once.** In
development a Vite middleware serves `fixtures/`. A built bundle has no such
middleware, so the fetch 404d and the badge read "unavailable" in the deployed
build — degrading honestly rather than claiming a parity failure, which is
correct and is precisely why no test caught it. `deploy/app.py` now serves that
one file by name. If the badge is missing rather than red, check that route
before checking the crypto.

## Looking at it

```bash
npm test                                          # 49 tests, including a jsdom mount
npm run browser -- http://localhost:8080 shots    # a real browser, every surface
```

The unit tests prove the components do not throw. They cannot prove a stylesheet
loaded, a font resolved, an asset path is right, or that a fetch made at boot
returns anything — and until 2026-08-31 nobody had ever seen this rendered.
`browser-check.mjs` runs beats 1, 2 and 5 so the board is populated, visits every
surface and all six firewall tabs, and fails on any console error, any failed
request, any surface that renders nothing, or a missing parity badge. It found
one thing on its own: run long enough, and both SSE connections were tearing
themselves down every fifteen seconds, because sse_starlette's keepalive is an
SSE *comment* and a comment fires no event in the browser, so the idle watchdog
in `src/lib/stream.ts` was never fed. Both streams now send a named `heartbeat`. CI runs it against the
container on every push and uploads the screenshots; the committed ones are in
`../docs/screenshots/`.

The signing procedure, defined once in `src/lib/crypto.ts` and nowhere else:

```
strip `signature` → RFC 8785 JCS → Ed25519 → base64url unpadded
```

## Keyboard, on stage

| Key   | What happens                          |
| ----- | ------------------------------------- |
| `1`–`6` | The six demo beats                  |
| `s`   | Force a stockout on the next fulfilment |
| `0`   | Reset the gate, merchant and wallet   |
| `←` `→` | Move through the slides             |

Keys are ignored while a text field has focus, so typing in the checkout
composer does not fire a beat.

## Layout

```
src/
  lib/
    jcs.ts          RFC 8785, written from the RFC
    crypto.ts       Ed25519, base64url, the signing procedure
    device.ts       the device key, generated and held in the browser
    contracts.ts    console-side mirror of the frozen shapes
    api.ts          REST for gate / merchant / sim, all through the vite proxy
    stream.ts       SSE: capped backoff, resync on reconnect, idle watchdog
    store.tsx       one provider owns both streams and the polling backstop
    parity.ts       the blocking gate, run in the browser
  components/       the kit shared across surfaces
  surfaces/         grant, checkout, console, slides
mock/               dev-only stand-in for the gate, merchant and sim runner
test/               parity and render smoke tests
```

## About `mock/`

`mock/` is a development fixture, not a second implementation of the system. It
serves the shapes the shared contract froze so the surfaces are buildable and
rehearsable before the engine and the buyer agent are up. It verifies real
Ed25519 signatures and enforces ceilings against real reservations; it has no
Razorpay, no database and no intent auditor.

**No number produced by `mock/` belongs on a results slide.**

Nothing under `src/` imports from `mock/`. Switching to the real services is a
matter of not starting it.

## Transport

SSE from the gate and the merchant, with:

- reconnect backoff capped at two seconds, jittered
- a refetch of the last 50 decisions and the stats on **every** reconnect,
  rather than assuming the stream buffered
- an idle watchdog, because `EventSource` will sit in `OPEN` with a dead pipe
  behind it
- a stats poll every two seconds as a backstop
- a connection dot per stream that never shows green optimistically

A dashboard that looks fine but has silently stopped updating is a worse stage
failure than a visible reconnect.
