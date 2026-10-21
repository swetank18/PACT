# PayPal — what the client relies on

Written 2026-10-20 from PayPal's published OpenAPI specs
(`developer.paypal.com/api/{orders/v2,payments/v2,webhooks/v1,payment-tokens/v3}/schema.json`)
and the REST reference pages. Same discipline as `rails/razorpay/API_NOTES.md`:
**SPEC** means read in the schema or the reference today; **UNVERIFIED** means
believed and not yet seen against a sandbox. `tests/fake_paypal.py` enforces the
SPEC lines and nothing else.

Nothing here has run against the sandbox. See the last section for how to
close that.

---

## Hosts, and why this client refuses one of them

- Sandbox `https://api-m.sandbox.paypal.com`, live `https://api-m.paypal.com`. SPEC.
- A PayPal client id does not say which environment it belongs to — there is
  no `rzp_test_` prefix to check. So the guard is on the **host**: the client
  only talks to the sandbox, and an override that names any other host is
  refused at construction.

## Auth

- `POST /v1/oauth2/token`, HTTP Basic with `client_id:secret`, form body
  `grant_type=client_credentials`. Returns `access_token`, `token_type`
  (`Bearer`) and `expires_in` seconds. SPEC.
- Every other call is `Authorization: Bearer <token>`. The client caches the
  token until a minute before `expires_in` and, on a 401, drops it and retries
  once. A second 401 is an error, not a loop.

## Money is a string, and the decimals belong to the currency

- `money` is `{"currency_code": "USD", "value": "12.34"}`. `value` is a
  **string** matching `^((-?[0-9]+)|(-?([0-9]+)?[.][0-9]+))$`, max 32
  characters. SPEC.
- The number of decimals is per currency. JPY, HUF and TWD do not accept
  decimals at all: "If you pass a decimal amount, an error occurs." SPEC
  (currency codes reference).
- **INR is not on PayPal's currency list.** SPEC (currency codes reference,
  read 2026-10-20). PACT's catalog is priced in paise, so this rail cannot
  settle the catalog as it stands; it refuses INR at construction rather than
  sending rupee amounts labelled as dollars.
- PACT keeps integer minor units everywhere. The conversion to PayPal's string
  happens in one function, `to_value`, and back in `from_value`, which refuses
  a value with more decimals than the currency has instead of rounding it.

## Paying without a buyer in the room: a vaulted wallet

An agent cannot click PayPal's approve button. The flow that needs no buyer
interaction is a **vaulted PayPal wallet**: the account holder approves once,
PayPal returns a payment token, and later orders name it.

- One time, with the human present: `POST /v3/vault/setup-tokens`, buyer
  approves at the returned link, then `POST /v3/vault/payment-tokens` turns the
  setup token into a payment token. Its `id` is the `vault_id`. SPEC (paths and
  request shapes). **UNVERIFIED** that a sandbox app has vaulting enabled by
  default — the app's settings page has a checkbox for it. **UNVERIFIED** that
  the payment token response carries `payment_source.paypal.email_address`;
  the merchant shows it masked when it does and nothing when it does not.
- Undo: `DELETE /v3/vault/payment-tokens/{id}`. SPEC (path). The client treats
  a 404 as already gone.
- PACT's side of it is `merchant/wallets.py`: the principal signs the request
  to connect or disconnect, the gate confirms the signature, and the vault id
  never leaves the merchant's database.
- Every purchase after that: `POST /v2/checkout/orders` with
  `intent: AUTHORIZE` and `payment_source.paypal.vault_id`, plus
  `stored_credential: {payment_initiator: MERCHANT, usage: SUBSEQUENT,
  usage_pattern: UNSCHEDULED_POSTPAID}`. The published example comes back
  **201 with status `COMPLETED`** and an authorization in
  `purchase_units[0].payments.authorizations[0]` — created and authorised in
  one call. SPEC (the "single shot" example in the create-order spec).
- `payment_initiator` is `CUSTOMER` or `MERCHANT`; MERCHANT is "initiated by
  merchant on behalf of the customer without the active engagement" of them,
  which is what an agent purchase is. `UNSCHEDULED_POSTPAID` is the
  card-on-file pattern for variable amounts on no schedule. SPEC (enums).
- `COMPLETED` does **not** mean paid. The spec says so in as many words: check
  the payment's own status, because a completed order "can indicate a payment
  was authorized, an authorized payment was captured, or a payment was
  declined." So the client reads the authorization's status (`CREATED`,
  `CAPTURED`, `DENIED`, `PARTIALLY_CAPTURED`, `VOIDED`, `PENDING`), never the
  order's. SPEC.

This is why the adapter uses AUTHORIZE rather than CAPTURE: it gives the saga a
separate capture step, which is the shape `RailAdapter` already has, and a hold
that can be voided instead of a charge that has to be refunded.

## `Prefer: return=minimal` is the default

- Without `Prefer: return=representation`, create, authorize, capture and
  refund return only `id`, `status` and links — no authorization id, no
  amounts. SPEC (`Prefer` header, default `return=minimal`). Every write in the
  client sends `return=representation`.

## Idempotency: a header, scoped per call type, and it replays the present

- `PayPal-Request-Id` is accepted on create order, authorize, capture, refund,
  void and the vault token calls. 1–10000 characters. SPEC.
- It "must be unique for both each request and an API call type", authorize
  and capture given as the example. SPEC (idempotency reference). **The saga
  passes one key to both `create_intent` and `capture`**, so the client
  derives a request id per operation. Reusing the caller's key verbatim would
  hand PayPal the same id for an order and its capture.
- A repeat returns "the latest status ... at the current time and not the
  status of the original request." SPEC. So a capture retried after a refund
  comes back `REFUNDED`, which reads as a failed capture. **This is why the
  client keeps the local idempotency table as well as sending the header**: a
  replay is answered from what happened, not from what is true now.
- Retention: the reference says only "for a period of time". UNVERIFIED.
- Two simultaneous requests with one id: the second "may fail". SPEC. The
  refund endpoint's 409 is "a previous call for the given resource is in
  progress", issue `PREVIOUS_REQUEST_IN_PROGRESS`. The client treats 409 as
  retryable.

## Capture

- `POST /v2/payments/authorizations/{authorization_id}/capture`, body
  `{"amount": money, "final_capture": true}`. Omit `amount` and the full
  authorised amount is captured. 201 on success. SPEC.
- Capture status: `COMPLETED`, `DECLINED`, `PARTIALLY_REFUNDED`, `PENDING`,
  `REFUNDED`, `FAILED`. SPEC. **`PENDING` is real** — the published example of
  an empty capture request comes back `PENDING` — and means the funds are not
  yet credited to the payee. See the open question below.
- Errors seen in the spec for this call: `AUTHORIZATION_EXPIRED`,
  `MAX_CAPTURE_AMOUNT_EXCEEDED`, `PREVIOUS_REQUEST_IN_PROGRESS`. SPEC. A second
  capture of a captured authorization with a **new** request id is UNVERIFIED
  in its exact issue code; the client does not match on it. On any 422 it reads
  the authorization back, and if it is `CAPTURED` it finds the capture through
  the order (`supplementary_data.related_ids.order_id`) and converges, as the
  Razorpay client does on its 400.
- An authorization is "best captured within three days" and capturable for up
  to 29. SPEC. The saga captures in the same breath, so this never binds.

## Refund

- `POST /v2/payments/captures/{capture_id}/refund`. Empty body refunds what is
  left; a body with `amount` refunds part. SPEC. The client always sends the
  amount, so a retry can never refund "whatever is left" twice over.
- Refund status: `COMPLETED`, `PENDING`, `CANCELLED`, `FAILED`. SPEC. As with
  Razorpay, `PENDING` is not money returned; the adapter reports it as such.
- Issues: `REFUND_AMOUNT_EXCEEDED`, `TRANSACTION_DISPUTED`,
  `PREVIOUS_REQUEST_IN_PROGRESS`. SPEC.

## Status, for the reconciler

- `GET /v2/checkout/orders/{id}` returns `purchase_units[].payments` with
  `authorizations`, `captures` and `refunds`. SPEC. The adapter reports
  `captured` only for a `COMPLETED` capture — the one value the reconciler acts
  on — and names everything else for what it is.

## Errors

- Body: `name`, `message`, `debug_id`, and `details[]` with `issue` and
  `description`. SPEC. The client surfaces the first `issue` as the code and
  keeps `debug_id`, which is what PayPal support asks for.

## Webhooks

- Five headers: `PAYPAL-TRANSMISSION-ID`, `PAYPAL-TRANSMISSION-TIME`,
  `PAYPAL-TRANSMISSION-SIG`, `PAYPAL-CERT-URL`, `PAYPAL-AUTH-ALGO`. SPEC (the
  verify request's field descriptions name each header).
- Verification by postback: `POST /v1/notifications/verify-webhook-signature`
  with those five, the `webhook_id` from the app's settings, and
  `webhook_event` — "the full event, exactly as received". Response
  `{"verification_status": "SUCCESS" | "FAILURE"}`. SPEC. The client splices the
  **raw** body into the request rather than parsing and re-serialising it.
- `RailAdapter.verify_callback(body, signature)` takes one string, and PayPal
  signs with five headers. The PayPal adapter takes them as a JSON object in
  that string. It is a wart, and it is a smaller one than changing a protocol
  every rail implements.
- Postback is not supported for mock events from the dashboard simulator. SPEC.
  So the dashboard's "simulate event" button cannot be used to test this
  receiver; a real sandbox capture can.
- The receiver (`rails/paypal/webhooks.py`, mounted by the single-port build on
  a PayPal instance at `/api/webhooks/rail`) acts on `PAYMENT.CAPTURE.COMPLETED`
  and `PAYMENT.CAPTURE.DENIED` for an order left `PAYMENT_PENDING`, and
  acknowledges everything else with a 200, since a non-2xx is retried for three
  days. It dedupes on the event's `id`, which PayPal keeps across redeliveries,
  and releases its claim if applying fails so a redelivery is not mistaken for a
  duplicate. Event names are from PayPal's event reference. UNVERIFIED: the
  exact `resource` shape of a delivered capture event — the receiver reads only
  `resource.id`.
- The listener must be HTTPS on 443. Register it in the sandbox app's settings
  against the deployed URL; `PAYPAL_WEBHOOK_ID` is the id PayPal gives it.

## A `PENDING` capture, and a declined wallet

Both were open when this file was first written; both are handled as of
2026-10-21.

- **`PENDING`:** the saga commits the budget — the payer's money is committed,
  so for the principal's ceiling it is spent — and parks the order in
  `PAYMENT_PENDING` without fulfilling, as PayPal advises ("check the payment
  status ... before fulfilling"). The reconciler fulfils it when the capture
  reads `COMPLETED`, or releases the budget and the stock when it reads
  `DECLINED` or `FAILED`; nothing was taken, so there is nothing to refund.
  In the sandbox a vaulted-wallet capture is expected to complete immediately
  (UNVERIFIED), so this path may never run there. `PAYMENT.CAPTURE.COMPLETED`
  resolves it at once through the webhook receiver; the reconciler is the
  backstop, not the path.
- **Declined wallet:** order and authorisation are one call, so a decline
  fails `create_intent`, not `capture`. The saga rolls back with
  `RAIL_AUTHORIZATION_FAILED`, stock and budget released, no refund.

## To close the gap

```bash
export PAYPAL_CLIENT_ID=… PAYPAL_CLIENT_SECRET=… PACT_RAIL=paypal
python3 scripts/paypal_sandbox.py      # vault a sandbox buyer, then one purchase, capture, refund
```

`PAYPAL_VAULT_ID` comes from the vaulting step. Diff what comes back against
this file and correct whichever is wrong.
