# Payment verification and reconciliation runbook

This document describes how money is verified, what the server trusts, and how
to investigate a payment or disbursement that does not look right. It is not a
substitute for Paystack's own API documentation.

> Payments are disabled by default. With `PAYMENTS_ENABLED=false`, payment and
> disbursement endpoints return `payments_disabled` and the webhook returns 503.
> That is the intended staging posture.

## 1. Trust model

The browser is **never** authoritative. A payment becomes `SUCCEEDED` only when:

1. a `charge.success` webhook arrives with a valid signature, **and**
2. the server has stored the event exactly once, **and**
3. the server re-verifies the transaction with Paystack (default), **and**
4. status, reference, currency and amount match the local `Payment` exactly.

The dashboard's "verify payment" call is a convenience for the user. It runs
the same idempotent service and cannot bypass these checks.

A disbursement becomes `SUCCESS` only after a provider confirmation
(webhook or authoritative lookup). A 2xx from the transfer-creation endpoint
means "accepted", not "paid".

## 2. Keys and modes

| Setting | Test mode | Live mode |
| --- | --- | --- |
| `PAYSTACK_SECRET_KEY` | `sk_test_...` | `sk_live_...` |
| `PAYSTACK_PUBLIC_KEY` | `pk_test_...` | `pk_live_...` |
| `PAYMENTS_LIVE_MODE` | `false` | `true` |
| `LEGAL_TERMS_APPROVED` | any | must be `true` |

Boot-time rules enforced by `core/settings.py`:

- `PAYMENTS_ENABLED=false` + any key present → refuse to start.
- `PAYMENTS_LIVE_MODE=false` + `sk_live_` key → refuse to start.
- `PAYMENTS_LIVE_MODE=true` + non-live key → refuse to start.
- `PAYMENTS_LIVE_MODE=true` + `LEGAL_TERMS_APPROVED=false` → refuse to start.

Changing a key requires a redeploy (settings are read once at startup).

## 3. Webhook setup

1. In the Paystack dashboard, set the webhook URL to:

   ```
   https://<your-host>/api/webhooks/paystack/
   ```

2. Use the **same secret key** that the API uses. Paystack computes
   `x-paystack-signature` as the hex HMAC-SHA512 of the **raw request body**
   keyed with the secret key. `PAYSTACK_WEBHOOK_SECRET` is not used; there is no
   separate webhook secret in this design.
3. The endpoint:
   - rejects bodies over 256 KB (413);
   - rejects a missing/invalid signature before parsing (401);
   - rejects malformed JSON (400);
   - stores every accepted delivery in `WebhookEvent` keyed by
     `sha256(raw_body)`, which is stable across provider retries;
   - processes known events idempotently and returns `processed`, `ignored` or
     `duplicate`;
   - returns 500 on processing failure so Paystack retries, and leaves the event
     `FAILED` for `manage.py replay_webhooks`.

Acknowledgement target is within 30 seconds. Provider retries can continue for
up to 72 hours. Because processing is keyed on the body digest, a retry cannot
double-credit a payment.

Paystack publishes source IPs for webhooks; restricting ingress to those
addresses at the proxy is optional defence in depth. The signature check is the
authoritative control and must never be disabled.

Events handled:

| Event | Effect |
| --- | --- |
| `charge.success` | Verify, validate, mark payment paid, post collection journal |
| `transfer.success` | Confirm disbursement, mark loan `DISBURSED`, post principal journal |
| `transfer.failed` | Record failure; retry remains possible until attempts are exhausted |
| `transfer.reversed` | Reverse transfer; loan `REVERSED`; post compensating journal |
| `refund.processed` | Complete refund; payment `REFUNDED`; post compensating journal |
| `refund.failed` | Mark refund failed; payment returns to `SUCCEEDED` |
| anything else | Stored as `IGNORED` for audit |

## 4. Verification checks

`_validate_charge` refuses, with the payment left untouched:

- provider status other than `success`;
- provider reference different from `Payment.reference`;
- currency other than the payment currency (KES);
- amount (converted from minor units) not exactly equal to `Payment.amount_kes`;
- fractional minor units (`from_minor` rejects them).

Rejected events are retried by the provider and recorded as `FAILED` for manual
investigation. Never "fix" an amount mismatch by editing the payment. Refund or
investigate.

## 5. Idempotency

- `Payment.reference` is unique and stable (`LPF_<loan>_<hex>`).
- `Payment.idempotency_key` is unique (`fee:loan:<id>`), so one fee per loan.
- `Transfer` is one-to-one with a loan; the reference is `LNT_<loan>`.
- `Refund.reference` is unique (`RFD_<payment>_<hex>`).
- Webhook deliveries are deduplicated by body digest.
- `mark_payment_succeeded`, `confirm_transfer_success` and `complete_refund` are
  safe to call repeatedly.

## 6. Daily reconciliation

Run the dry-run first:

```bash
python Backend/manage.py reconcile_payments --json
```

It reports:

- payments marked paid on loans still `APPROVED` (`paid_not_disbursed`);
- in-flight/unknown/failed transfers and their provider state;
- ledger groups whose debits and credits do not match;
- more than one successful fee payment for the same loan.

Apply only safe, provider-confirmed corrections:

```bash
python Backend/manage.py reconcile_payments --apply
```

`--apply` can confirm a provider-successful transfer and mark a provider-failed
transfer failed. It never creates a new transfer and never blindly retries an
unknown one.

The same operation is available to staff at
`POST /api/payments/ops/reconcile/` with `{"dry_run": true}` (default) and run
history at `GET /api/payments/ops/reconciliations/`.

## 7. Stuck or unknown disbursement

1. Inspect the transfer: `GET /api/payments/ops/transfers/?status=UNKNOWN`
   (staff only), or the Django admin.
2. Run `reconcile_payments` (dry-run). If the provider reports success, the
   transfer will show `transfer_should_be_success`.
3. Apply reconciliation, or use the admin "Refresh selected transfers from
   Paystack" action.
4. Only if the provider has no record and the transfer is definitively failed
   should you retry: `POST /api/loans/ops/<id>/retry-disbursement/` or
   `manage.py retry_disbursements --apply` for due retries.
5. If attempts are exhausted, the loan becomes `FAILED`. A later confirmed
   success still recovers it to `DISBURSED`; a refund of the fee remains a staff
   decision.

Never send a second transfer while one is `INITIATING`, `PENDING_PROVIDER` or
`UNKNOWN`. The service refuses to do so.

## 8. Refunds

Staff endpoint:

```
POST /api/payments/ops/refund/
{"payment_reference": "LPF_...", "amount_kes": 1100, "reason": "..."}
```

- Only a `SUCCEEDED` payment can be refunded.
- Amount must be 1..payment amount; omitted means full refund.
- The refund is recorded before the provider call. A provider rejection leaves
  it `FAILED` and the payment `SUCCEEDED`.
- A provider-pending refund leaves the payment `REFUND_PENDING`; completion is
  driven by `refund.processed`.
- Completing a refund posts a balanced compensating journal. Payments and
  ledger entries are never edited.
- A loan whose fee payment is `REFUNDED` cannot be disbursed.

Chargebacks/disputes are **not** implemented as a separate workflow. They must
be handled operationally with Paystack and legal until a dispute process is
approved.

## 9. Staging test checklist (test keys only)

1. Set `PAYMENTS_ENABLED=true`, `PAYMENTS_LIVE_MODE=false`, test keys, and
   (only if testing fees) `FEE_COLLECTION_MODE=upfront` +
   `FEE_COLLECTION_APPROVED=true` with business sign-off.
2. Approve a test loan, accept the offer, click "Pay with M-Pesa".
3. Confirm:
   - the checkout opens and the reference matches `LPF_...`;
   - closing the window leaves the payment `INITIALIZED`, not `SUCCEEDED`;
   - a successful test charge flips the dashboard only after the webhook (or
     explicit verify);
   - `WebhookEvent` shows `PROCESSED`;
   - the ledger has one balanced collection group.
4. Replay the same webhook body → `duplicate`, no new ledger rows.
5. Send an invalid signature → 401, no stored event.
6. Force a transfer timeout (provider sandbox or unit test) → `UNKNOWN`; verify
   reconciliation queries the provider before any retry.
7. Confirm success → loan `DISBURSED` with a balanced principal journal.
8. Reverse the transfer in the provider sandbox → loan `REVERSED`, balanced
   compensating journal.
9. Refund the fee → payment `REFUNDED`, balanced compensating journal.
10. Run `reconcile_payments --json` and confirm no unexpected issues.

Do not use real customer data or live keys in staging.

## 10. Go-live payment checklist

- [ ] Live account approved by Paystack; live keys stored in the secret manager
- [ ] `LEGAL_TERMS_APPROVED=true` with a signed legal approval on file
- [ ] Fee collection decision approved (or remains `disabled`)
- [ ] Webhook URL reachable over HTTPS; signature verified in staging
- [ ] Reconciliation scheduled daily; alert on failures
- [ ] Refund and reversal runbooks rehearsed
- [ ] Treasury float funded to cover expected disbursements
- [ ] Monitoring and on-call rota active
