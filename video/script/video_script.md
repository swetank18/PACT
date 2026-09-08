# PACT — video script

Generated from `script/timeline.json`. Runtime **5m 49.9s**, 1920×1080, 30 fps, 18 scenes.

Timings are measured, not intended: each scene lasts exactly as long as its narration plus its lead-in and tail, so what is written here is what was cut.

---

## S01 — 1 - The problem

**TIME** 00:00.00–00:14.72  ·  **DURATION** 14.72s  ·  **VOICE IN** 00:00.60

**NARRATION**

> An A I agent can now hold a payment credential and complete a checkout on your behalf. Agentic commerce standardised how that agent pays. It never gave the merchant any way to read what the agent is allowed to spend.

**ON-SCREEN** PAYMENT: AUTHORISED · AUTHORITY: UNKNOWN

**VISUAL** Cold open. Statement type on the dark ground, then two cards land: a green PAYMENT · AUTHORISED beside a dashed red AUTHORITY · UNKNOWN.

**SCREEN ACTION** Motion graphics. Cards arrive on the words 'pays' and 'allowed to spend'.

**CAMERA** Static. The type does the work; no push-in.

**TRANSITION** Hard cut in from black.

**AUDIO** Room tone establishes under the first line.

**TECHNICAL PURPOSE** State the gap in one frame: settlement is solved, authority is not.

---

## S02 — 1 - The problem

**TIME** 00:14.72–00:38.27  ·  **DURATION** 23.55s  ·  **VOICE IN** 00:15.02

**NARRATION**

> Three things follow. The merchant recommends and only then finds out, so the offer trips a limit it could not see. The obvious fix, a cap inside the agent, holds against the agent's own mistakes and nothing else, because a compromised agent simply does not run it. And bolted onto a blind merchant, every authority check can only subtract.

**ON-SCREEN** The upsell is made blind · A client-side cap is not a control · The gate becomes pure friction

**VISUAL** Three cards, each a named failure mode, arriving in sequence.

**SCREEN ACTION** Cards rise as each is named in the narration.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Make the problem concrete and specific before any product appears.

---

## S03 — 2 - The solution

**TIME** 00:38.27–00:53.97  ·  **DURATION** 15.70s  ·  **VOICE IN** 00:38.67

**NARRATION**

> PACT closes that gap. A human signs a spending mandate in their own browser, on their own key. The agent carries that mandate and never the key. And before the merchant prices anything, it reads how much authority is left.

**ON-SCREEN** PACT · The merchant reads what the buyer is allowed to spend, before it quotes.

**VISUAL** Product identity: the wordmark, the one-line value proposition, three capability chips.

**SCREEN ACTION** Wordmark, then tagline, then chips.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Name the product and its claim in a single frame a viewer can screenshot.

---

## S04 — 3 - How it works

**TIME** 00:53.97–01:18.37  ·  **DURATION** 24.40s  ·  **VOICE IN** 00:54.27

**NARRATION**

> The mandate is signed on the buyer's device with Ed25519. The agent carries it to the merchant, which reads the remaining headroom before it builds a quote. Every request then goes to the gate, which decides on authority alone, across ten checks in a fixed order, cheapest and most certain first. Only an allowed decision reaches a settlement rail.

**ON-SCREEN** HUMAN · AGENT · MERCHANT · GATE · LEDGER · RAIL

**VISUAL** The architecture: six nodes from the signing device to the settlement rail, the gate emphasised, then all ten of its checks as a row of chips, read from CHECK_ORDER.

**SCREEN ACTION** Nodes arrive left to right in narration order; check chips fill in as they are listed.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Give the viewer the mental model the rest of the film assumes.

---

## S05 — 3 - How it works

**TIME** 01:18.37–01:45.56  ·  **DURATION** 27.20s  ·  **VOICE IN** 01:18.67

**NARRATION**

> What the merchant reads is deliberately thin. Headroom remaining, purchases left, the per-transaction ceiling, allowed categories, expiry. Not the buyer's identity, not their intent, not their spend history, and never the total budget. A merchant that learns eight thousand nine hundred rupees remaining learns what it can sell. One that learns eight thousand nine hundred of fifteen thousand learns how rich the buyer is.

**ON-SCREEN** IN THE ENVELOPE · NEVER IN IT

**VISUAL** Two columns — what the headroom envelope carries, and what it must never carry — closing on the ₹8,900 contrast.

**SCREEN ACTION** Fields arrive per column, then the contrast line.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Show that the privacy boundary is a design decision, not an omission.

---

## S06 — 4 - Technical differentiation

**TIME** 01:45.56–02:12.36  ·  **DURATION** 26.80s  ·  **VOICE IN** 01:45.86

**NARRATION**

> Three things make this hold up. First, ceilings are reserved, not counted. The budget is summed over reservations inside the same immediate transaction that writes the new row. The naive read-then-write version is kept in the repository so the two can be run side by side: twenty concurrent agents against a five thousand rupee cap, and the naive ledger approves twenty of them and overspends by fifteen thousand.

**ON-SCREEN** BEGIN IMMEDIATE · atomic: 5 approved · naive: 20 approved

**VISUAL** Two ledgers side by side. The reserved one holds its cap; the counted one overruns with a hatched red bar.

**SCREEN ACTION** Bars fill on the sentence that names each number.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Prove the concurrency claim with the repository's own comparison rather than an assertion.

---

## S07 — 4 - Technical differentiation

**TIME** 02:12.37–02:30.06  ·  **DURATION** 17.70s  ·  **VOICE IN** 02:12.66

**NARRATION**

> Second, the gate decides authority and a rail moves money, and neither knows the other. Nothing in the core imports a rail. A test walks the syntax tree to enforce it, and a second test closes the loophole: no executable line in the core may even name a vendor.

**ON-SCREEN** core/ enforces authority · rails/ moves money · no import, no vendor string

**VISUAL** core/ and rails/ as two blocks with a crossed connection between them.

**SCREEN ACTION** Blocks, then the refusal glyph, then the test that enforces it.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Explain why the gate stays portable across settlement rails.

---

## S08 — 4 - Technical differentiation

**TIME** 02:30.06–02:51.53  ·  **DURATION** 21.46s  ·  **VOICE IN** 02:30.37

**NARRATION**

> Third, every offer is provably approvable. The test suite puts every add-on the merchant suggests through the real gate, and a single block fails the build. That is the inversion: the same gate that refuses bad transactions now shapes good ones, so the spending limit stops being friction and becomes a conversion instrument.

**ON-SCREEN** every suggested add-on · -> the real gate · one BLOCK fails the suite

**VISUAL** Four suggested add-ons, each stamped ALLOW by the real gate, then the line that turns the limit into a conversion instrument.

**SCREEN ACTION** Chips stamp in sequence.

**CAMERA** Static.

**TRANSITION** Cut to the product.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Land the commercial argument immediately before the live demo shows it happening.

---

## S09 — 5 - Live walkthrough

**TIME** 02:51.53–03:14.18  ·  **DURATION** 22.65s  ·  **VOICE IN** 02:51.93

**NARRATION**

> This is the running system. The human grants authority on their own device: fifteen thousand rupees, five purchases, three categories. The signature is produced in the browser, and the badge in the header is the console verifying that the browser's Ed25519 and canonical J S O N agree with the server's, byte for byte.

**ON-SCREEN** Granting authority · Ed25519, in the browser

**VISUAL** The running console. The grant surface, a real Ed25519 signature, the mandate chip, then intent typed into the checkout composer.

**SCREEN ACTION** Pointer moves to Grant and sign; the mandate appears; the buyer's intent is typed a character at a time and sent.

**CAMERA** Full-frame UI at 1:1. Caption chip upper left.

**TRANSITION** Cut from motion graphics to product.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Show the signature happening on the device, not asserted in a diagram.

---

## S10 — 5 - Live walkthrough

**TIME** 03:14.18–03:29.37  ·  **DURATION** 15.19s  ·  **VOICE IN** 03:14.47

**NARRATION**

> The agent shops. The merchant prices the basket server side, the gate runs its checks in order, and the order settles. Every check is on screen, including the ones that were skipped, and the whole path takes milliseconds.

**ON-SCREEN** Discovery -> quote -> gate -> settlement

**VISUAL** An order, a gate decision, and the check list rendering in order.

**SCREEN ACTION** Beat 1 is triggered; the board fills.

**CAMERA** Full-frame UI.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Establish the happy path end to end before any contrast is drawn.

---

## S11 — 5 - Live walkthrough

**TIME** 03:29.37–03:47.72  ·  **DURATION** 18.36s  ·  **VOICE IN** 03:29.66

**NARRATION**

> Now the product itself. The merchant read the remaining authority before it made this offer, which is why the badge on it says the item fits. The buyer accepts, the add-on is re-quoted into the same order, and the authority bar moves — an upsell that was approvable before it was ever shown.

**ON-SCREEN** The offer, made against headroom · FITS REMAINING AUTHORITY

**VISUAL** An add-on offered against remaining headroom, accepted; average order value rises.

**SCREEN ACTION** Beat 2.

**CAMERA** Full-frame UI.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** This is the product claim, demonstrated: an offer that is approvable before it is made.

---

## S12 — 5 - Live walkthrough

**TIME** 03:47.72–04:01.66  ·  **DURATION** 13.94s  ·  **VOICE IN** 03:48.02

**NARRATION**

> The same offer, made the obvious way. Recommend first, find out afterwards. The gate refuses it with a per-transaction ceiling: a failed offer, and a buyer who now trusts their agent a little less.

**ON-SCREEN** The same offer, made blind · CEILING_PER_TXN

**VISUAL** The same offer made blind, refused with CEILING_PER_TXN.

**SCREEN ACTION** Beat 3, which is expected to fail and is asserted on failing.

**CAMERA** Full-frame UI.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** The contrast the whole pitch rests on, shown rather than described.

---

## S13 — 5 - Live walkthrough

**TIME** 04:01.66–04:18.71  ·  **DURATION** 17.05s  ·  **VOICE IN** 04:01.96

**NARRATION**

> Four attacks in sequence. A captured request replayed, a payee outside the mandate's scope, a price the model invented, and an instruction hidden in a product description. Each one refused server side, and each with a machine-readable code rather than prose.

**ON-SCREEN** NONCE_REPLAY · SCOPE_MERCHANT_NOT_ALLOWED · QUOTE_AMOUNT_MISMATCH · INTENT_INJECTION_SUSPECTED

**VISUAL** Four attacks and four machine-readable refusals.

**SCREEN ACTION** Beat 4.

**CAMERA** Full-frame UI.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Show that refusals carry codes a caller can act on, not prose.

---

## S14 — 5 - Live walkthrough

**TIME** 04:18.71–04:40.91  ·  **DURATION** 22.20s  ·  **VOICE IN** 04:19.01

**NARRATION**

> Then the failure worth planning for. The money has already moved and the warehouse is empty. Compensations run in reverse: the payment is refunded, the ceiling gives the budget back, and an alternative is offered. Accepting it returns a quote the buyer signs, because the merchant holds no key and must never be able to spend on the buyer's behalf.

**ON-SCREEN** capture -> fulfilment fails -> refund -> budget released -> alternative

**VISUAL** Capture succeeds, fulfilment fails, compensations run in reverse, an alternative is offered and signed.

**SCREEN ACTION** Beat 5, running its full saga at the deployed step delay.

**CAMERA** Full-frame UI.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Demonstrate the failure path — the part most demos skip — including who must sign the replacement.

---

## S15 — 5 - Live walkthrough

**TIME** 04:40.91–04:51.64  ·  **DURATION** 10.73s  ·  **VOICE IN** 04:41.21

**NARRATION**

> Everything so far was the merchant's view. This is the same decision seen by the person whose money it is: every check in order, and the one that stopped it.

**ON-SCREEN** The same decision, from the buyer's side

**VISUAL** The firewall surface: the same decision from the buyer's side, walked check by check onto the one that failed.

**SCREEN ACTION** Pointer opens a blocked row and replays the decision.

**CAMERA** Full-frame UI.

**TRANSITION** Cut back to motion graphics.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Close the loop between the merchant's view and the account holder's.

---

## S16 — 6 - Impact

**TIME** 04:51.64–05:16.54  ·  **DURATION** 24.90s  ·  **VOICE IN** 04:51.94

**NARRATION**

> Four arms, two hundred sessions each, three seeds, run against the real services. Against a naive client-side cap, PACT nets about a quarter more, with a zero percent false block rate against that cap's twenty-eight. Its losses to adversarial traffic are zero, and stay zero at every adversarial rate swept, while an ungated agent channel degrades linearly.

**ON-SCREEN** 4 arms x 200 sessions x 3 seeds · false block 0.0% vs 27.8% · losses per 100: zero

**VISUAL** The four-arm experiment as stacked bars, then the two figures that carry the argument.

**SCREEN ACTION** Rows arrive per arm; the two large figures land last.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Put a measured result behind the claim, generated by the harness rather than typed.

---

## S17 — 6 - Impact

**TIME** 05:16.54–05:38.93  ·  **DURATION** 22.39s  ·  **VOICE IN** 05:16.84

**NARRATION**

> And the number that does not flatter us, reported anyway. Below roughly twenty percent adversarial traffic, an ungated channel still nets more under this loss model. The baseline arm is modelled rather than simulated, and the payment client has never run against the live A P I. All three are stated in the results the harness generates, not buried.

**ON-SCREEN** Reported anyway · crossover ~ 20% · arm A is modelled · the live rail is untested

**VISUAL** Three limitations, stated in the same type as the results.

**SCREEN ACTION** Rows arrive as each is named.

**CAMERA** Static.

**TRANSITION** Cut.

**AUDIO** Narration only.

**TECHNICAL PURPOSE** Credibility. The crossover, the modelled arm and the untested rail are named, not buried.

---

## S18 — 7 - Close

**TIME** 05:38.93–05:49.92  ·  **DURATION** 10.99s  ·  **VOICE IN** 05:39.33

**NARRATION**

> PACT. A signed delegation the agent carries, a gate that decides on authority alone, and a merchant that only ever offers what will be approved.

**ON-SCREEN** PACT · github.com/swetank18/PACT · pact-9btr.onrender.com

**VISUAL** Wordmark, the one-line proposition, the repository and the deployed instance.

**SCREEN ACTION** Wordmark, line, then the URLs.

**CAMERA** Static.

**TRANSITION** Hold, then fade out with the audio.

**AUDIO** Narration, then room tone fades.

**TECHNICAL PURPOSE** Leave one sentence and two addresses on screen.

---
