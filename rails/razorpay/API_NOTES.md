# Razorpay API notes

**Verified against the live documentation on 2026-08-30.** Not written from
memory and not written from the planning docs. Every field below was read off
the current docs pages linked at the bottom.

Anything marked **UNVERIFIED** was not confirmed against a live page or a live
call. Treat those as assumptions to check before the pitch, not as facts.

---

## Auth

HTTP Basic. Username is `KEY_ID`, password is `KEY_SECRET`.

Test mode keys start `rzp_test_`. **Never a live key in this repo, not even in a
comment.**

---

## Create order

```
POST https://api.razorpay.com/v1/orders
```

| Field      | Type   | Required | Notes                                                        |
| ---------- | ------ | -------- | ------------------------------------------------------------ |
| `amount`   | int    | yes      | Smallest currency sub-unit. For INR that is **paise**.        |
| `currency` | string | yes      | ISO 4217, 3 characters.                                       |
| `receipt`  | string | no       | Max **40 ASCII characters**. Our idempotency handle, see below. |
| `notes`    | object | no       | Max 15 key-value pairs, 256 chars each.                       |

Response: `id`, `entity` (always `"order"`), `amount`, `amount_paid`,
`amount_due`, `currency`, `receipt`, `status`, `attempts`, `notes`,
`created_at`, `offer_id`.

`status` is one of `created`, `attempted`, `paid`.

---

## Capture

```
POST https://api.razorpay.com/v1/payments/{id}/capture
```

| Field      | Type   | Required | Notes                                          |
| ---------- | ------ | -------- | ---------------------------------------------- |
| `amount`   | int    | **yes**  | Must equal the authorised amount.              |
| `currency` | string | **yes**  | Must match the original payment's currency.    |

Both are mandatory. This is the field people get wrong from memory — capture is
not a bare POST.

Only payments in state `authorized` can be captured. Capturing an
already-captured payment returns **HTTP 400**, not a success:

> "Your payment has been declined as the order is already paid."

Response includes `id`, `status` (`"captured"`), `amount`, `currency`,
`captured` (bool), and `error_code` / `error_description` on failure.

---

## Refund

```
POST https://api.razorpay.com/v1/payments/{id}/refund
```

| Field     | Type   | Required                     | Notes                                        |
| --------- | ------ | ---------------------------- | -------------------------------------------- |
| `amount`  | int    | optional full, **required partial** | Paise. Partial refunds **are** supported. |
| `speed`   | string | no                           | `optimum` for instant, falls back to normal.  |
| `notes`   | object | no                           | Max 15 pairs.                                 |
| `receipt` | string | no                           | Idempotency handle, see below.                |

Refund `status`: `pending`, `processed`, `failed`. **`pending` is a real state**
— a refund is not necessarily final the moment the call returns, so the saga
must not treat a 200 as "money is back". `failed` happens on payments older than
six months and on bank or account issues.

---

## Idempotency — the important finding

**There is no idempotency header on these endpoints.** The docs do not document
one for orders, capture or refunds.

What exists instead is `receipt`, on **orders** and **refunds** only:

- Orders: a duplicate `receipt` is rejected with a "Duplicate request" error.
- Refunds: a reused `receipt` on the same payment is rejected with
  "Duplicate receipt found for this refund request".

Capture has **no** `receipt` and therefore no server-side idempotency at all;
its protection is that a second capture 400s because the payment is no longer
`authorized`.

Two consequences for our implementation:

1. `receipt` is at most 40 ASCII chars, so the full
   `sha256(order_ref | amount_paise | attempt)` will not fit. We send a
   **truncated hex prefix** as `receipt` and keep the full key ourselves.
2. Because coverage is partial and error-shaped rather than a clean replay of
   the original response, **we keep our own idempotency table** mapping key to
   the stored result and short-circuit on repeat before ever calling Razorpay.
   That is the only way "calling twice must not charge twice" is demonstrable
   across all three operations rather than two.

---

## Webhooks

Signature is in the **`X-Razorpay-Signature`** header.

```
expected = HMAC_SHA256(key = webhook_secret, message = RAW request body)
reject unless expected == received
```

The message is the **raw body**. Do not parse and re-serialise it before
verifying — the docs say so explicitly, and re-serialising is the standard way
this check silently stops working. FastAPI hands us `await request.body()`
for exactly this.

Envelope:

```json
{
  "entity": "event",
  "account_id": "acc_XXX",
  "event": "payment.captured",
  "contains": ["payment"],
  "payload": { "payment": { "entity": { "id": "pay_XXX", "status": "captured" } } },
  "created_at": 1724990000
}
```

The payment object is at `payload.payment.entity`.

Payment events: `payment.authorized`, `payment.captured`, `payment.failed`.
Also `payment.downtime.started` / `.resolved` / `.updated`, which we ignore.

Refund event names were **UNVERIFIED** — the payloads page 404s at the URL the
search index gives. We handle refund state by polling `fetch_payment` and the
refund object rather than depending on a refund webhook, which is the safer
design anyway.

If the webhook secret is rotated, older retried deliveries still carry the old
signature. Not a concern in a hackathon, noted for completeness.

---

## Rate limits

**UNVERIFIED.** No public test-mode rate limit number was confirmed. The client
is written with a bounded retry and backoff regardless, so an undocumented limit
degrades into slowness rather than a crash.

---

## Design decisions that follow from the above

- **Webhooks are not the only path.** A reconciliation poller queries by order
  for anything pending and older than 30 seconds, because a webhook that never
  arrives is a scenario the brief explicitly asks us to handle.
- **Every handler is idempotent, keyed on the Razorpay payment id.** Webhooks
  are unordered and duplicated; `payment.captured` arriving twice, or arriving
  after the poller already reconciled, must change nothing.
- **A refund returning `pending` is not a completed compensation.** The saga
  records `REFUND_ISSUED` with the refund id and its status, and only a
  `processed` refund closes the compensation.

---

## Sources

- https://razorpay.com/docs/api/orders/create/
- https://razorpay.com/docs/api/payments/capture/
- https://razorpay.com/docs/api/refunds/create-instant/
- https://razorpay.com/docs/webhooks/validate-test/
- https://razorpay.com/docs/webhooks/payments/
