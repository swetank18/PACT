# PACT

**The merchant reads what the buyer is allowed to spend, before it quotes.**

Agentic commerce has standardised how an agent pays. It has not given the
merchant any way to read the buyer's authority envelope *before* making an
offer. That gap is the product: a signed delegation the buyer's agent carries, a
gate that decides on authority alone, and a merchant that only ever offers what
will be approved.

The consequence is the point. If the merchant can see remaining authority, every
cross-sell it makes is provably approvable, so the spending gate stops being
friction and becomes a conversion instrument.

## Status

| Area                            | State                                                   |
| ------------------------------- | ------------------------------------------------------- |
| `console/` — the three surfaces | Built. 23 tests green.                                   |
| `fixtures/`, `scripts/`         | Cross language signature vector published and verified.  |
| Engine, merchant, rails         | Separate lane.                                           |
| Buyer agent, simulation, eval   | Separate lane.                                           |

## Getting started

```bash
cd console
npm install
npm run dev          # http://localhost:5173
```

That starts a dev mock of the backend services alongside the console, so all
three surfaces and all six demo beats work from a cold checkout. See
[`console/README.md`](console/README.md).

## Signature parity

The mandate is signed in the browser on the buyer's device. The agent receives
the signed mandate and never the key, which is the reason it cannot spend
outside what the human granted.

For that to be true rather than decorative, a signature produced in the browser
has to verify in the engine, byte for byte:

```
strip `signature` → RFC 8785 JCS → Ed25519 → base64url unpadded
```

`fixtures/keys/test_vector.json` is the cross language vector. The Python
implementation in `scripts/` and the TypeScript one in `console/src/lib/` were
each written from RFC 8785 separately — parity between two ports of the same
code would prove nothing.

```bash
python3 scripts/gen_test_vector.py     # regenerate the vector
cd console && npm run parity           # assert byte equality from the TS side
```

The console also runs the same assertions in the browser at boot and shows the
result in the top bar.

## Invariants

These hold everywhere in the repository:

- **Money is integer paise.** Never floats, never rupees in a payload.
- **Prices are computed server side.** The model never does arithmetic that
  reaches a payload, which makes a hallucinated price a structural impossibility
  rather than a thing to hope about.
- **Timestamps are RFC 3339 UTC with a `Z`.**
- **IDs are prefixed:** `mnd_`, `dec_`, `qte_`, `ord_`, `stl_`, `sim_`.
- **Fail closed.** Any error, timeout or unparseable input yields BLOCK or
  STEP_UP. Never ALLOW.

## Layout

```
console/     the merchant console, conversational checkout, grant screen
fixtures/    shared, append only — the cross language signature vector
scripts/     RFC 8785 JCS and the vector generator
```

## Limitations

Stated here as plainly as they are stated on the last slide:

- Test mode only. No live money has moved through this.
- One merchant, one catalog.
- The intent auditor is probabilistic, which is exactly why the design steps up
  rather than blocking on it. The deterministic checks are the system.
- The regulatory direction this is aimed at is press reporting, not a published
  specification. The delegation shape it borrows from does exist today.
- The headroom endpoint is worth nothing unless buyer agents call it. That is a
  distribution problem, not a technical one.
