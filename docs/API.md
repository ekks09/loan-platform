# API reference

The browser and any future client talk to the same Django service that renders
the pages. Nothing here is a separate microservice, and no URL contains a host.

## 1. Base URL

| Environment | `API_BASE_URL` | Result |
| --- | --- | --- |
| Local, staging, Render (same origin) | *(default)* | `/api/v1` — relative to the page |
| Split frontend on another origin | `https://<api-host>/api/v1` | absolute; the API service must also allow the frontend origin |

The value is a deployment setting, not a JavaScript constant. It is rendered
into every page as:

```html
<meta name="api-base" content="/api/v1" />
```

`js/auth.js` reads that tag. A `window.API_BASE_URL` override still exists for
local experiments but is ignored unless it is same-origin — a token in
localStorage must never be sent to a host chosen by page script.

### Versioning

`/api/v1/...` is the documented surface. The unversioned `/api/...` paths are
aliases kept because the bundled frontend and an already-configured Paystack
webhook URL use them. Both resolve to the same view and return identical
bodies; `core/tests/test_api_contract.py` asserts it.

## 2. Authentication

`Authorization: Bearer <jwt>`. Tokens are issued by `POST /api/v1/users/login/`
and carry `iat`; changing the password invalidates every token issued before
`password_changed_at`, so a reset logs other devices out.

There are no cookies. CORS is therefore configured **without** credentials:

- `CORS_ALLOWED_ORIGINS` — exact origins, comma separated;
- `CORS_ALLOW_CREDENTIALS = False`;
- CORS applies only to `/api/*`; the HTML pages are not a cross-origin resource.

## 3. Endpoints

All paths below are relative to `API_BASE_URL`.

### Users — `/users/`

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `register/` | — | Create an account (Kenyan or Congolese number) |
| POST | `login/` | — | Exchange phone + password for a JWT |
| GET | `me/` | bearer | Profile and verification state |
| GET | `verification-status/` | bearer | Reviewer decision |
| POST | `documents/` | bearer | Upload identity documents |
| POST | `consent/` | bearer | Record consent (versioned) |
| POST | `password-reset/request/` | — | Send a 6-digit code by SMS |
| POST | `password-reset/confirm/` | — | Consume the code, set a new password |

Password reset rules: the number must belong to an **active account** — the
SMS itself proves the person can receive messages on that line, which is the
only "verified number" claim the platform can honestly make (identity
verification is separate and is not required to regain access). At most **3
requests per number in 15 minutes** (10 per IP); the code is 6 digits,
single-use, and expires after 10 minutes; the response is identical whether or
not the account exists.

### Loans — `/loans/`

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `quote/` | — | Published fee for an amount |
| POST | `apply/` | bearer | Create an application (one open loan per user) |
| GET | `active/` | bearer | The open application, if any |
| GET | `current/` | bearer | Most recent loan |
| GET | `history/` | bearer | Paginated loan history |
| GET | `<id>/` | bearer | One loan; 404 for someone else's |
| POST | `<id>/accept-offer/` | bearer | Accept the issued offer |
| POST | `<id>/cancel/` | bearer | Cancel a pending or approved loan |
| GET | `ops/` | staff | Review queue |
| POST | `ops/<id>/approve/` | staff | Issue an offer |
| POST | `ops/<id>/reject/` | staff | Reject with a reason |
| POST | `ops/<id>/disburse/` | staff | Start a provider transfer |
| POST | `ops/<id>/retry-disbursement/` | staff | Retry a failed transfer |

Application amounts are capped per borrower: KES 20,000 at Base/Silver and
KES 60,000 at Gold. The cap is enforced in `create_application`, not only in
the form.

### Payments — `/payments/` and `/paystack/`

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `payments/init/` | bearer | Initialise a Paystack M-Pesa charge |
| POST | `payments/verify/` | bearer | Re-query the provider for one payment |
| GET | `payments/history/` | bearer | The caller's payments only |
| POST | `payments/ops/refund/` | staff | Refund |
| GET | `payments/ops/transfers/` | staff | Disbursement queue |
| POST | `payments/ops/reconcile/` | staff | Reconcile a provider reference |
| GET | `payments/ops/reconciliations/` | staff | Reconciliation runs |
| GET | `paystack/verify/<reference>/` | bearer | Live status for the STK modal |
| POST | `webhooks/paystack/` | signature | Provider webhook (server-to-server) |

The webhook is authenticated by `x-paystack-signature`, a hex HMAC-SHA512 of
the **raw** body keyed with the secret key. Any other caller gets 401. It
answers 2xx quickly and retries are handled by `payments_webhookevent`.

### Loyalty — `/loyalty/`

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `summary/` | bearer | Tier, streak, cash-back, vault, credit score |
| POST | `vault/` | bearer | `{ "direction": "lock" \| "release", "amount_kes": 100 }` |

`summary/` returns `credit.portable = false` and a disclosure sentence. The
score is computed from this platform's own records and is not a credit bureau
score; it cannot be transferred to another provider.

The vault moves **cash-back that has already been earned**. It does not accept
loan principal, does not pay a yield in this build (`yield_bps: 0`) and is not
lent out. Both directions are atomic and refuse to overdraw.

## 4. Errors

Errors share one shape:

```json
{ "error": { "code": "amount_above_tier_limit", "message": "…", "details": {} } }
```

| Status | Meaning |
| --- | --- |
| 400 | Validation or domain rule (e.g. above tier limit) |
| 401 | Missing, expired or revoked token |
| 403 | Authenticated but not allowed (staff-only route) |
| 404 | Not found **or** someone else's object — the two are indistinguishable on purpose |
| 409 | Illegal state transition, duplicate application, payments disabled |
| 429 | Throttled; `Retry-After` is set |
| 503 | Provider unavailable; safe to retry |

## 5. Idempotency

`Payment.reference`, `Transfer.reference` and `Transfer.idempotency_key` are
unique. Repeating an initialise call with the same key returns the existing
payment rather than creating a second charge. Webhook deliveries are stored by
`event_id` before processing, so a redelivery is a no-op.
