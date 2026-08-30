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
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/dev.sh          # gate :8000, merchant :8100, webhooks :8110
./scripts/test.sh         # 85 tests
```

With no Razorpay credentials it runs on the `mock_upi` rail, which exercises
every line above the adapter. Test keys only — the client refuses to start on a
key that is not `rzp_test_`.

The console is separate: `cd console && npm install && npm run dev`.

## What is here

```
contracts/   frozen wire shapes, reason codes, the signing procedure
core/        RAIL AGNOSTIC. gate, mandate, ledger, audit, rail.py, config
rails/       one directory per settlement rail: razorpay, mock_upi
merchant/    catalog, quote engine, headroom upsell, saga, MCP tools
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

`fixtures/keys/test_vector.json` is the cross language vector. The Python
implementation in `contracts/` and the TypeScript one in `console/src/lib/` were
each written from RFC 8785 separately — parity between two ports of the same
code proves nothing.

```bash
python3 scripts/gen_test_vector.py     # regenerate
cd console && npm run parity           # assert byte equality from the TS side
```

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

- Test mode only. No live money has moved through this.
- One merchant, one catalog.
- The intent auditor is probabilistic, which is exactly why it steps up rather
  than blocking. The eight deterministic checks plus quote binding are the
  system; the auditor is the ninth and it is cuttable.
- `RAZORPAY_CAPTURE_FAILED` is in the frozen reason code enum, so a vendor name
  is baked into a contract two other lanes assert on. It should have been
  `RAIL_CAPTURE_FAILED`. Allowlisted in the layering test rather than hidden,
  and worth fixing between events.
- The regulatory direction this aims at is press reporting, not a published
  specification. The delegation shape it borrows from does exist today.
- The headroom endpoint is worth nothing unless buyer agents call it. That is a
  distribution problem, not a technical one.
