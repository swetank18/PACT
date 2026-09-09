# Demo runbook

The deterministic scenario the film records, and the one to run live. It is the
stage sequence from [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) reduced to the seven
beats that carry the story on camera.

## The scenario

**User** — someone who has delegated a month of office restocking to an agent.
**Goal** — let the agent buy, without letting it spend outside what was granted.
**Grant** — ₹15,000, five purchases, three categories.

## Before recording

| Step | Command | Expected |
| --- | --- | --- |
| Build the console | `cd console && npm run build` | `console/dist/index.html` exists |
| Start a cold instance | see `README_VIDEO.md` step 1 | `/healthz` reports `ok`, `console: true` |
| Confirm the rail | `curl -s :8090/healthz` | `"rail":"mock_upi"`, `"auditor":"deterministic"` |
| Confirm the beats | `curl -sX POST :8090/api/sim/demo/beat/1` | `"completed": true` |
| Reset the board | press `0` in the console, or the reset control | GMV ₹0, no orders, no decisions |

Run the whole flow twice before the take that ships. The second run is the one
that catches a beat left half-finished by the first.

## The flow, as recorded

| # | Action | On screen | Why it is in the film |
| --- | --- | --- | --- |
| 1 | *Grant* → **Grant and sign** | a real Ed25519 signature, then the mandate chip in the header | The human signs on their own device; the agent never receives the key. |
| 2 | *Checkout* → type "restock office supplies for the month" → **Send** | quote card, headroom bar, gate verdict | Intent stated by a person, priced server side. |
| 3 | **Add** on the suggested add-on | the item joins the order, the gate approves, the authority bar splits into spent / this purchase / add-on / remaining | **The product.** The offer carried a *fits remaining authority* badge before it was shown, and accepting it is a state change on screen, not a counter. |
| 4 | Press `1` | an order, a decision, the checks in order | The happy path, end to end, including the checks that were skipped. |
| 5 | Press `3` | the same offer made blind, refused | `CEILING_PER_TXN`. This beat is *supposed to fail* — it is the contrast the pitch rests on. |
| 6 | Press `4` | four blocks, four reason codes | Replay, out-of-scope payee, tampered amount, prompt injection. |
| 7 | Press `5` | capture, fulfilment failure, refund, budget release, alternative accepted | The failure path, and the fact that the buyer must sign the replacement. |
| 8 | *Firewall* → *Transactions* → a blocked row → **Replay this decision** | the check chain walked onto the one that failed | The same decision from the account holder's side. |

Beat `2` still runs in the take — it fills the merchant board for the beats that
follow — but the film cuts the acceptance from the checkout surface at step 3,
where a person performs it, because that is the better shot.

The attach tile used to read 0% behind it, counting only `accept_alternative`
and so missing every add-on taken the ordinary way. Fixed on 2026-09-09; see the
finding in `script/project_brief.md`. The board now moves when an add-on is
taken, which makes step 4 read correctly on camera as well.

Beat `6` (a duplicate webhook that does nothing) is exercised by CI but left out
of the film: it is the least legible beat on camera, and the runtime is better
spent on the rollback.

## Timing

The recorder waits a fixed settle time after each beat — 13 s for beat 1, 12 s
for beats 2 and 3, 15 s for beat 4, 21 s for beat 5 — chosen so the board has
finished animating before the cut. With `PACT_SAGA_STEP_DELAY_S=0.25` the whole
take runs about two minutes.

## If something breaks mid-take

There is no recovery inside a take: the recorder writes marks as it goes, and a
retry mid-run would put a half-finished board on camera. Kill the run, reset the
database, and start again. A take costs about two minutes.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Send` stays disabled | the composer is empty | the recorder types the intent first; check the placeholder text still matches the component |
| A beat does nothing | the previous beat is still running | raise its settle time in `record_walkthrough.mjs` |
| Headroom shows a 404 in the log | was a race between the first headroom poll and the mandate reaching the gate, not the reset — fixed in `Grant.tsx`, which now registers before handing the mandate to the rest of the app | if it recurs, something is actually wrong |
| The board is not empty at the start | a previous take's data | delete the database file and restart the instance |
