# PesaFix / Loan Platform — Full Platform Review

**Review date:** 17 October 2026  
**Repository:** `ekks09/loan-platform` @ `e0e95b8` (single commit, "Update register.html", 30 Dec 2025)  
**Scope:** every tracked file — Django backend (`users`, `loans`, `payments`, `core`), static frontend, migrations, deployment config, and local-run scripts  
**Method:** full source read, runtime probes in an isolated environment, migration replay on a clean database, API/contract tracing, and regulatory review against Kenyan requirements  
**Companion document:** [`ISSUE_REGISTER.csv`](./ISSUE_REGISTER.csv) — every finding with severity, evidence, fix, effort and phase

> **Superseded for status purposes.** This document is the audit baseline.
> What was repaired, what remains partial, and what is externally blocked is
> recorded in [`REMEDIATION_REPORT.md`](./REMEDIATION_REPORT.md) and
> [`RESOLUTION_MATRIX.md`](./RESOLUTION_MATRIX.md).

> Findings describe the repository **as committed**. Some failures depend on live configuration (Render service name, Supabase bucket policy, dashboard settings); where that is the case, the report says "as written" and explains both branches. No live production system was inspected.

> **Verdict in one sentence:** this is a competent-looking prototype that is **not deployable and must not take real money** — the loan and payment tables are never created by migrations, payment fulfilment depends on the customer's browser, identity documents are handled in a way that is unsafe and likely unlawful, and the product as built is an advance-fee scheme with no loan terms, no repayment, and no licence.

---

## 1. Executive summary

### 1.1 What the platform is

PesaFix ("Loan Platform" in the app) is a Kenyan micro-lending web app built with Django 5 + Django REST Framework on the backend and server-rendered HTML/CSS/vanilla JS on the frontend. A user:

1. Registers with a **Kenyan phone number, national ID number, password, and three photos** (ID front, ID back, selfie).
2. Waits for a staff member to approve the identity documents in a custom admin dashboard.
3. Selects a loan amount between **KES 1,000 and KES 60,000**.
4. Pays a **"service fee"** (KES 200–6,000, i.e. **10%–22.7% of principal**) through Paystack **before any approval or disbursement**. There is no refund path in the code.
5. The backend verifies the fee and immediately initiates a Paystack transfer of the full principal to the user's M-Pesa number.

There is **no interest, no term, no due date, no repayment schedule, no repayment endpoint, no credit assessment, no loan agreement, and no collections process**. The "loan" ends at disbursement. The business, as coded, earns a fee for moving money to the user; it does not earn a return on a loan.

### 1.2 Overall assessment

| Dimension | Rating | Summary |
|---|---|---|
| Product clarity | 2 / 5 | Flow is understandable, but the product is an advance fee, not credit |
| Architecture | 2 / 5 | Sensible app split, but no service layer, no ledger, no job queue |
| Code quality | 2.5 / 5 | Readable and consistent in places; duplicated business rules; dead config |
| Security | 1.5 / 5 | JWT in localStorage, no rate limits, no OTP, known secret placeholders |
| Payment correctness | 1 / 5 | No success webhook, no idempotency, no reconciliation, lying statuses |
| Data protection | 1 / 5 | ID photos on public URLs, no consent, no retention, no access audit |
| Regulatory readiness | 0.5 / 5 | No DCP licence, no DPA registration, false "licensed" claim |
| Reliability / operations | 1 / 5 | Free tier, no monitoring, no backups, no admin for loans/payments |
| UX / UI (surface) | 3 / 5 | Attractive mobile UI; several flows are dead ends |
| UX / UI (journey) | 2 / 5 | Broken dashboard contract, no password reset, no notifications |
| Testing / CI | 0 / 5 | Zero tests, zero CI, no linting, no staging |
| Documentation | 0.5 / 5 | No README, no `.env.example`, no runbook, no API docs |
| **Production readiness** | **1.6 / 5** | **Prototype only — do not launch** |

### 1.3 The eleven blockers

Any one of these is sufficient to say **do not accept real money in this build**.

| ID | Blocker | Evidence |
|---|---|---|
| **P0-01** | `loans` and `payments` migrations are invisible, so the tables are never created; even if they were, both apps' migrations are missing fields | No `migrations/__init__.py` in either app; a clean `migrate` creates only `users_*`, `auth_*`, `admin`, `sessions`; `Loan.objects.count()` → `no such table: loans_loan` |
| **P0-02** | Payments are never fulfilled unless the customer's browser calls `/verify/`; the webhook is unrouted, handles no `charge.success`, and uses the wrong signing secret | `paystack_webhook` exists only in `payments/webhook.py`; absent from `core/urls.py`; Paystack signs with the **secret key**, not a separate webhook secret |
| **P0-03** | Host/origin/API configuration is internally inconsistent and largely ignored by Django | `render.yaml` service `loan-platform-backend`; `ALLOWED_HOSTS` only `loan-platform.onrender.com`; `CORS_ALLOWED_ORIGINS`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DEBUG` env vars never read; frontend hardcodes `https://loan-platform.onrender.com/api` |
| **P0-04** | Secrets are committed as literal placeholders that are truthy; if deployed unchanged, JWT signing is publicly known | `render.yaml:13` `DJANGO_SECRET_KEY: "<YOUR_DJANGO_SECRET_KEY>"`; `settings.py:19-21` accepts any non-empty string |
| **P0-05** | Photo registration cannot work in the deployed build | `Backend/requirements.txt` omits `supabase` (only root requirements has it); `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` are not declared in `render.yaml`; UI requires all three photos |
| **P0-06** | 13.5% of valid amounts crash the fee calculator; 01X numbers register but cannot borrow | 7,992 of 59,001 amounts (1,001–1,999, 2,001–2,999, 5,001–5,999, …) have no tier; `compute_service_fee` raises `ValidationError` but the view catches `ValueError`; `loans.normalize_ke_phone` rejects `2541…` |
| **P0-07** | The dashboard can never display a loan | API returns `{has_active_loan, loan:{...}}`; dashboard checks `loan.has_loan` and reads `loan.amount`; disbursed loans are excluded from `/active/` entirely |
| **P0-08** | Identity documents are stored and exposed unsafely, without consent | Public URLs (`get_public_url`), national ID in plaintext, no consent checkbox, no privacy notice, no retention/deletion, no access audit, no DPA registration |
| **P0-09** | The fee is taken before approval and there is no refund path; there are no loan terms | `ApplyLoanView` charges immediately; `APPROVED` simply means "fee paid"; no refund model, endpoint, or policy; no agreement, APR, term, or repayment |
| **P0-10** | Disbursement is marked `DISBURSED` when Paystack merely accepts the transfer; failures cannot be retried | `payments/views.py:264-278` sets `initiated=True` before calling transfer and sets `DISBURSED` immediately; a failure leaves `initiated=True` and all future retries skip; no webhook path to correct it |
| **P0-11** | The public site claims a licence it cannot evidence | `index.html:58` "Licensed and regulated in accordance with Kenyan financial regulations"; no entity name, registration number, licence number, or address anywhere |

### 1.4 What is genuinely good

Credit where it is due — this is not a careless build:

- **Clean Django app separation** (`users`, `loans`, `payments`) and a **custom user model** keyed on phone, which is the right call for Kenya.
- **JWT implementation is careful**: pinned `HS256`, issuer and audience validation, explicit expiry, `type` check, active-user check. The problem is storage and lifecycle, not the token code.
- **Server-side payment verification**: the client callback is not trusted; the backend calls Paystack and checks the amount. That is the correct instinct.
- **Constant-time HMAC comparison** in the webhook (`hmac.compare_digest`) — correct primitive, wrong key source and wrong routing.
- **File validation and cleanup**: MIME allow-list, 5 MB cap, deletion of orphaned uploads on failure.
- **Atomic registration** with `transaction.atomic()`.
- **Verification workflow is staff-only**, CSRF-protected, and has approve/reject with notes.
- **Thoughtful UI craft**: mobile-first layout, drag-and-drop, camera capture, upload progress, previews, fee hints, accessibility basics (`aria-live`, labels).
- **Sensible security defaults**: HSTS, secure cookies, `nosniff`, same-origin referrer, clickjacking middleware, no debug page in production.
- **No real secrets committed** — only placeholders (though placeholders are themselves a risk).

The gap is not effort or surface quality. It is **integration, financial correctness, and compliance**.

---

## 2. What the platform actually is

### 2.1 Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.x, Django 5.0.8, DRF 3.15.2 |
| Auth | Custom `User` (phone as username), hand-rolled HS256 JWT (`PyJWT`) |
| Database | Supabase Postgres via `psycopg2-binary` |
| File storage | Supabase Storage (`identity-documents` bucket), service-role key |
| Payments | Paystack: transaction initialise, verify, transfer recipient, transfer |
| Frontend | Django templates + static HTML/CSS/vanilla JS, Paystack Inline v1 |
| Hosting | Render free web service (API + static site in one process), WhiteNoise |
| Email | None sent; fake `user-<phone>@example.com` addresses used for Paystack |

### 2.2 Component map

```
Browser (HTML/CSS/JS)
  ├─ localStorage JWT ──────────────┐
  ├─ fetch/XHR ─────────────────────┼──► Django (Render)
  └─ Paystack Inline (CDN)          │      ├─ /api/users/*      register, login, me, verification
                                    │      ├─ /api/loans/*      apply, current, active
                                    │      ├─ /api/payments/*   init, verify
                                    │      ├─ /admin/*          Django admin (empty)
                                    │      ├─ /api/users/admin/verification/  custom review UI
                                    │      └─ /debug-env/       public config probe
                                    │
                                    ├──► Supabase Postgres (metadata)
                                    ├──► Supabase Storage (ID photos, public URLs)
                                    └──► Paystack API (charge + transfer)
```

### 2.3 Data model (as coded)

```
User
  phone (unique), national_id (not unique), password, is_active, is_staff
  id_front_url/path, id_back_url/path, selfie_url/path
  verification_status (pending|verified|rejected), notes, verified_at, verified_by

Loan
  user FK, amount, service_fee, mpesa_phone
  status (PENDING|APPROVED|DISBURSED)
  service_fee_paid, paystack_reference, paystack_authorization_url/access_code
  last_event, created_at, updated_at

Payment
  loan_id INT (not FK), user_id INT (not FK), reference (unique), amount_kes
  verified BOOL, paystack_transaction_id, paid_at, authorization_url, access_code

Transfer
  loan_id INT unique (not FK), reference (unique), recipient_code
  initiated BOOL, status, raw_last_event, timestamps
```

**What is missing for money:** currency, purpose, status lifecycle, gateway response, fees, attempts, refunds, disputes, ledger entries, term, due date, principal outstanding, interest, repayment schedule, and an immutable event history.

### 2.4 API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/users/register/` | Public | JSON or multipart registration |
| POST | `/api/users/login/` | Public | Returns 30-min access token |
| GET | `/api/users/me/` | JWT | Profile |
| GET | `/api/users/verification-status/` | JWT | Identity status |
| GET | `/api/users/admin/verification/` | Staff session | HTML review queue |
| GET | `/api/users/admin/verification/api/` | Staff session | JSON queue |
| POST | `/api/users/admin/verify/<id>/` | Staff session + CSRF | Approve/reject |
| POST | `/api/loans/apply/` | JWT | Create loan + init Paystack |
| GET | `/api/loans/current/` | JWT | Latest pending/approved |
| GET | `/api/loans/active/` | JWT | Latest non-disbursed |
| POST | `/api/payments/init/` | JWT | Get/reuse checkout URL |
| POST | `/api/payments/verify/` | JWT | Verify charge **and disburse** |
| POST | `/webhook/paystack/` | — | **Not routed — dead code** |

No versioning, no OpenAPI schema, no pagination, no consistent response envelope.

### 2.5 Deploy topology (as written)

- One Render web service named `loan-platform-backend`, free plan.
- Build runs `pip install -r Backend/requirements.txt`, `migrate`, `collectstatic`.
- Start runs `gunicorn core.wsgi --chdir Backend`.
- Frontend HTML is served by the same Django process from `Backend/Frontend`.
- Supabase provides Postgres and object storage; Paystack provides collections and payouts.

---

## 3. Blocking defects (P0) — detail

### P0-01 — Migrations are invisible and wrong; the product's tables do not exist

**Evidence**

- `Backend/loans/migrations/` and `Backend/payments/migrations/` contain `0001_initial.py` but **no `__init__.py`**. Django only recognises a migrations package when that file is present.
- On a clean database, `manage.py migrate` applied `users`, `auth`, `admin`, `sessions` — and **nothing else**.
- `Loan.objects.count()` → `OperationalError: no such table: loans_loan`.
- Even after adding `__init__.py`, `makemigrations --check` reports a second migration is required for **Loan** (`paystack_access_code`, `paystack_authorization_url`, changed indexes, options, field options) and **Payment** (`authorization_url`, `access_code`).
- `manage.py check` passes. That is why this class of defect reaches production.

**Impact:** every loan and payment endpoint fails on a fresh deploy. If a live database already has tables from manual creation, the migration ledger still does not know about them, so the next deploy is unpredictable. Running `makemigrations` without `__init__.py` silently **rewrites** `0001_initial.py` — I reproduced this — which can destroy migration history.

**Fix**

1. Add `__init__.py` to both `migrations/` packages **first**.
2. Run `makemigrations loans payments` and review the generated `0002_*` files.
3. Never edit an applied migration; never run `makemigrations` against production.
4. Add a CI gate: `makemigrations --check --dry-run` must exit 0.
5. Add a startup/liveness check that verifies expected tables exist (`/healthz/` doing `SELECT 1` plus a model query).

### P0-02 — Payment fulfilment has no reliable server-side path

**Evidence**

- `payments/webhook.py` defines `paystack_webhook`, but no URL routes to it. `grep` finds zero references outside the file.
- Even if routed, it handles only `transfer.*` events. There is **no `charge.success` handler**, so a successful service-fee payment is ignored.
- It verifies the signature with `settings.PAYSTACK_WEBHOOK_SECRET`. Paystack signs the **raw body with your secret key** (`PAYSTACK_SECRET_KEY`) using HMAC-SHA512; a separate secret will reject every genuine event unless it is deliberately set equal to the secret key.
- The only fulfilment path is the customer's browser calling `POST /api/payments/verify/` after the Paystack callback.

**Impact:** if the user closes the tab, loses network, or the app is killed after paying, the money is taken and the loan is never approved or disbursed. There is no reconciliation job to find these. This is the single most damaging business bug in the repository.

**Fix**

1. Route `POST /api/v1/webhooks/paystack/` (CSRF-exempt), verify HMAC-SHA512 over the raw body with `PAYSTACK_SECRET_KEY`.
2. Persist every event in a `WebhookEvent` table keyed by a unique provider event ID, return `200` within ~2 seconds, and process asynchronously.
3. Handle `charge.success` (mark payment paid, enqueue disbursement) and `transfer.success|failed|reversed` (update state, alert, retry/refund).
4. Keep `/verify/` as a user-facing "check now" convenience that calls the same idempotent service, not as the source of truth.
5. Add a reconciliation job that queries Paystack for pending references older than N minutes.

### P0-03 — Production host, origin and API-base configuration contradict each other

**Evidence**

- `render.yaml` names the service `loan-platform-backend` (URL `https://loan-platform-backend.onrender.com`).
- `settings.py:25-33` allows only `loan-platform.onrender.com` and trusts only that origin.
- `render.yaml` declares `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, and `DJANGO_DEBUG` — **none are read by `settings.py`**.
- `django-cors-headers` is installed and in middleware, but no `CORS_ALLOWED_ORIGINS` is ever set, so cross-origin requests receive no CORS headers.
- `auth.js:6-10` hardcodes `https://loan-platform.onrender.com/api` on any `onrender.com` host, otherwise falls back to `http://127.0.0.1:8000/api` or a value from `localStorage`.

**Impact (as written):** every request to the Render service returns `400 DisallowedHost`. If the service is actually named `loan-platform`, the frontend calls the right host, but any other deployment (staging, preview, custom domain) breaks or is blocked by CORS. The API base in `localStorage` is also an injection/phishing vector.

**Fix:** drive `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` and `DEBUG` from environment; use **relative `/api`** everywhere; remove the `localStorage` override; add a staging host and test it in CI.

### P0-04 — Committed secret placeholders are truthy and publicly known

**Evidence:** `render.yaml:12-13` sets `DJANGO_SECRET_KEY` to the literal `"<YOUR_DJANGO_SECRET_KEY>"`. `settings.py` only checks that it is non-empty. The same pattern applies to database and Paystack keys.

**Impact:** if the blueprint is deployed unchanged, anyone reading the public repository knows the JWT/database signing key and can forge a token for any user, including staff (`sub` is the user ID). Paystack calls fail, but the auth bypass is real.

**Fix:** use `sync: false` / `generateValue: true` in `render.yaml`; store secrets in the platform secret manager; validate minimum entropy at boot; reject known placeholder strings; rotate everything that has ever been in the file; add a secret scanner (gitleaks) to CI.

### P0-05 — Registration is impossible in the deployed build

**Evidence**

- `Backend/requirements.txt` (the file Render installs) **does not include `supabase`**; only the root `requirements.txt` does.
- `users/storage.py` catches the import failure and disables uploads with a warning.
- `render.yaml` does not declare `SUPABASE_URL` or `SUPABASE_SERVICE_KEY`.
- `register.html` requires all three photos and the JS refuses to submit without them.

**Impact:** `SupabaseStorage.get_client()` raises `StorageError("Supabase client not available")`, the view returns 500, and **no one can sign up**. The only working registration path is the JSON endpoint with no photos, which the UI never uses.

**Fix:** add pinned `supabase` to the backend requirements; declare the env vars; add a startup readiness check; make the UI degrade gracefully to "upload later" if storage is down; add an end-to-end registration test in CI.

### P0-06 — Fee calculation and phone normalization fail for valid inputs

**Evidence**

- The allowed API range is 1,000–60,000, but the tier table has gaps. A loop over all 59,001 amounts finds **7,992 amounts with no fee (13.5%)**: 1,001–1,999, 2,001–2,999, 5,001–5,999, 11,001–11,999, 22,001–22,999, 32,001–32,999, 42,001–42,999, 52,001–52,999.
- `compute_service_fee` raises `django.core.exceptions.ValidationError`; `ApplyLoanView` catches `ValueError` (`loans/views.py:47-50`), so the exception escapes as a 500.
- Registration accepts `2541…` (Airtel) numbers; `loans/models.py:27` accepts only `2547…`. A user with an 01X number can register, then gets a 500 when applying because `Loan.save()` calls `full_clean()`.

**Impact:** any API client or future UI that submits a custom amount gets a server error; 01X users cannot borrow despite being told at registration that they can.

**Fix:** make the fee schedule contiguous and derive validation from it; expose `GET /api/v1/loans/quote?amount=`; use one normalization function shared by users, loans, and the frontend; raise and catch a single domain error; add property-based tests over the full amount range.

### P0-07 — The dashboard/API contract is broken

**Evidence**

- `/api/loans/active/` returns `{"has_active_loan": bool, "loan": {...}}` (`loans/views.py:188-207`).
- `dashboard.html:163` checks `if (!loan || !loan.has_loan)` and then reads `loan.amount`, `loan.service_fee`, `loan.status` — all undefined.
- `/api/loans/active/` excludes `DISBURSED`, so once the money is sent the loan disappears from the UI with no history.
- `/api/loans/current/` uses `has_loan` but is never called.

**Impact:** every user sees "No active loan" regardless of state; after payment they see no confirmation; after disbursement there is no record. This alone makes the product feel fraudulent even when it works.

**Fix:** define one response schema, generate or share types, add contract tests (Pact/OpenAPI), return a flat `loan` object or make the client read the nested one, and add `GET /api/v1/loans/` history with statuses and receipts.

### P0-08 — Identity data handling is unsafe and likely unlawful

**Evidence**

- ID front/back and selfie are uploaded to a Supabase bucket and **public URLs** are generated with `get_public_url` and stored permanently in the database.
- The code assumes a public bucket. If the bucket is public, any leaked URL exposes the document forever; if it is private, the admin review page will show broken images. There is **no correct configuration** of this design.
- The bucket is accessed with the **service-role key**, which bypasses row-level security.
- National ID numbers are stored in plaintext with **no unique constraint**.
- There is no consent checkbox, no privacy notice, no purpose statement, no retention period, no deletion/export endpoint, and no access log for who viewed documents.
- The registration page collects the most sensitive data before the user knows whether they qualify or what happens to it.

**Impact:** a leaked URL (logs, screenshots, referrers, support tickets, browser history) exposes a national ID and selfie forever. This conflicts with Kenya's Data Protection Act 2019 (lawful basis, minimisation, storage limitation, security, data-subject rights) and with the DCP licensing requirement for data-protection policies. It also creates a serious fraud/identity-theft surface.

**Fix**

1. Private bucket; store **paths only**; issue short-lived signed URLs to staff on demand.
2. Encrypt at rest and in transit; restrict bucket policy; never use the service key in request paths that do not need it.
3. Add explicit consent, purpose, retention (e.g. delete raw documents 90 days after decision, keep audit metadata), and access logging.
4. Add data export/delete endpoints and a DPA/ODPC registration workstream.
5. Strip EXIF, re-encode with Pillow, validate magic bytes, scan for malware, and cap dimensions.
6. Make national ID unique (with a documented exception process).

### P0-09 — Fee before approval, no refunds, no loan terms

**Evidence**

- `ApplyLoanView` creates the loan and immediately initialises the service-fee charge. `APPROVED` is set only when the fee is paid (`payments/views.py:247-251`). There is no underwriting step between application and charge.
- There is no refund model, refund endpoint, dispute handling, or policy text.
- There is no term, due date, interest, APR, repayment schedule, loan agreement, acceptance record, or collections process.
- Verification is not checked before applying or paying, so a user can pay and then be rejected.

**Impact:** a customer can pay a fee and receive nothing, with no contractual basis and no refund path. That is a consumer-protection failure and, at scale, the classic advance-fee pattern regulators act on. It also makes the "loan" unenforceable because there is nothing to enforce.

**Fix (minimum):** do not collect any fee until identity verification and approval are complete; deduct the fee from the disbursed principal rather than charging upfront; add a written offer/acceptance step with versioned terms, APR, term, total repayable, and cancellation/refund rights; automate refunds for any paid-but-not-disbursed case; keep an immutable acceptance record (timestamp, IP, terms version).

### P0-10 — Disbursement state is unsafe and unrecoverable

**Evidence (`payments/views.py:255-288`)**

```
transfer.initiated = True          # set BEFORE the transfer call
transfer.status = "initiated"
transfer.save()
client.initiate_transfer(...)      # network call inside transaction.atomic()
loan.status = DISBURSED            # set on API acceptance, not on success
loan.save()
```

- Paystack accepting a transfer means "queued", not "paid". The loan is marked `DISBURSED` immediately.
- If `initiate_transfer` fails after `initiated=True`, every future call sees `if not transfer.initiated` and **skips the retry forever**. The customer has paid, the loan sits at `APPROVED`, and there is no admin action to fix it.
- Multiple Paystack calls (verify, create recipient, initiate transfer) run **inside a database transaction holding row locks** for up to 30 seconds each. A rollback after a successful transfer would leave money sent but the database unchanged.
- `payment.verified` short-circuits future attempts with `already_verified`, so a failed disbursement is never retried even by the verify endpoint.
- The `transfer.failed` webhook, if it ever ran, only writes a `last_event` string; it does not refund or recover.

**Impact:** permanent stuck loans, double-payout risk, and a status that lies to the customer and to operations.

**Fix:** introduce explicit states (`DISBURSEMENT_PENDING`, `DISBURSED`, `DISBURSEMENT_FAILED`, `REVERSED`), write the intent and idempotency key **before** the call, commit, then call Paystack **outside** the DB transaction, then update from the webhook. Add a retry job with bounded attempts and an ops alert. Never mark `DISBURSED` until `transfer.success` (or a verified transfer lookup) confirms it.

### P0-11 — The public site claims a licence it cannot evidence

**Evidence:** `index.html:58` states "Licensed and regulated in accordance with Kenyan financial regulations." There is no company name, registration number, physical address, licence number, regulator reference, or policy page anywhere in the repository.

**Impact:** a false or unsupported regulatory claim is itself a consumer-protection and misrepresentation risk, and it destroys trust the moment a user checks. Digital credit business in Kenya requires a CBK Digital Credit Provider licence (CBK (Digital Credit Providers) Regulations, 2022; unlicensed operation is an offence). The repo contains no evidence of an application, licence, or exemption.

**Fix:** remove the claim immediately; publish the legal entity, registered address, licence status and number only when true; add Terms, Privacy, Complaints and Fee Disclosure pages; have Kenyan counsel confirm the licensing position before launch.

---

## 4. High-severity findings (P1)

### 4.1 Payments and money movement

| ID | Finding | Why it matters | Fix |
|---|---|---|---|
| P1-01 | Failed disbursement is permanently stuck (`initiated=True` before the call; verify returns `already_verified`) | Customer paid, no loan, no recovery | State machine + retry job + ops action |
| P1-02 | No reconciliation with Paystack; no handling of abandoned/closed browsers | Paid-but-unfulfilled is invisible | Daily job: verify all pending references, alert on mismatch |
| P1-03 | No refund, dispute, or `transfer.reversed` recovery | Regulator/consumer harm; money loss | Refund service, reversal handling, reserve |
| P1-04 | Verification checks only amount, not currency, reference, metadata, or customer | Wrong-currency or replayed transactions could pass | Check all fields against the Payment record |
| P1-05 | No idempotency keys; duplicate reference after a lost response can strand the user | Retry fails with "duplicate reference" | Persist idempotency key; fetch existing transaction on retry |
| P1-06 | Paystack email is `user-<phone>@example.com`; no receipts reach users | No proof of payment; support load | Collect a real email or send SMS receipts via a provider |
| P1-07 | Immediate payout from Paystack balance may precede settlement; transfer fees erode the fee | Failed payouts at launch; negative unit economics | Treasury buffer, T+1 payout, model Paystack fees |
| P1-08 | No ledger; `Payment`/`Transfer` are mutable status rows, not accounting entries | Cannot audit, reconcile, or report | Double-entry journal + immutable events |
| P1-09 | Amounts are integers with no currency field; transfer currency hardcoded `KES` | Inflexible and error-prone | `Decimal`, currency field, one money module |

### 4.2 Authentication, access and security

| ID | Finding | Why it matters | Fix |
|---|---|---|---|
| P1-10 | JWT in `localStorage`; 30-minute token; no refresh, revocation, or logout-all | XSS steals a bearer token; users logged out mid-flow | httpOnly secure cookies or refresh rotation + token version |
| P1-11 | No rate limiting/CAPTCHA; `THROTTLE_*` env vars are dead | Credential stuffing, registration/storage abuse, Paystack spam | DRF throttles + CAPTCHA + per-IP/phone quotas |
| P1-12 | Login reveals "No account found" vs "Incorrect password" | Account enumeration on a lending site | Generic "Invalid phone or password", constant-time path |
| P1-13 | No password reset, change-password, OTP, or Django password validators | Users locked out forever; weak passwords | SMS OTP reset, `AUTH_PASSWORD_VALIDATORS`, Argon2 |
| P1-14 | Phone ownership is never verified; phone is the unique identity | Pre-hijacking, SIM swap, impersonation | OTP at registration and on sensitive changes |
| P1-15 | `/admin/` exposed with no 2FA, throttling, or IP restriction | Staff account takeover = full data breach | 2FA, allow-list, separate admin host, alerts |
| P1-16 | Public `/debug-env/` reveals which secrets are configured | Reconnaissance | Remove entirely |
| P1-17 | Phone numbers and Paystack errors logged at INFO | PII in logs, retention exposure | Redact/structured logging, retention policy |
| P1-18 | Paystack script from CDN without SRI; no CSP; no redirect fallback | Supply-chain XSS; payment fails if CDN blocked | SRI, CSP, fallback to `authorization_url` |
| P1-19 | National ID not unique; no duplicate detection | One identity, many accounts; fraud rings | Unique constraint + duplicate review |
| P1-20 | Verification is decorative: loans never check `is_verified` | Unverified users can borrow | Gate apply/pay on verified status |
| P1-21 | No liveness/face match; a photo of a photo passes | Identity spoofing | Liveness vendor or manual video call |

### 4.3 Data model and correctness

| ID | Finding | Why it matters | Fix |
|---|---|---|---|
| P1-22 | `Payment.loan_id`/`user_id` and `Transfer.loan_id` are plain integers | No referential integrity; orphan rows; broken joins | Foreign keys with `PROTECT`/`SET_NULL` |
| P1-23 | Status model has only `PENDING/APPROVED/DISBURSED`; `last_event` is overwritten | No rejected/expired/failed/repaid; no audit trail | Full state machine + append-only `LoanEvent` |
| P1-24 | Race: `user_has_active_loan` check then create; no DB constraint | Two loans, two fees, double payout | Partial unique index + `select_for_update` |
| P1-25 | Unlimited `PENDING` applications; no expiry | Multiple Paystack references and fees per user | One open application per user; expire abandoned |
| P1-26 | `Loan.save()` calls `full_clean()` on every save | Extra queries; update-only saves can fail on unrelated fields | Validate in serializers/services, not in `save()` |
| P1-27 | Phone normalization implemented four times with divergent rules | 01X users break; impossible to reason about | One utility + tests |
| P1-28 | `Payment`/`Loan` migrations drift from models (unique vs `db_index`, missing fields) | Schema and code disagree | Generated migrations + CI check |
| P1-29 | `ensure_payment_record_created` can be called with a null reference | Integrity errors/duplicates | Guard and make reference non-null |
| P1-30 | No event/audit table for approvals, disbursements, refunds, or admin actions | Cannot investigate or defend decisions | Append-only audit log |

### 4.4 Operations and delivery

| ID | Finding | Why it matters | Fix |
|---|---|---|---|
| P1-31 | No admin registration for `User`, `Loan`, `Payment`, `Transfer` | Ops are blind; cannot support customers | Register models; build ops views |
| P1-32 | Verification queue capped at 50, no pagination/search/bulk/SLA | Cannot scale reviews | Paginated, filterable, assigned queue |
| P1-33 | Rejected users cannot re-upload and never see the reason in the UI | Permanent dead end; support burden | Re-submission flow + reason display + notification |
| P1-34 | No SMS/email notifications for approval, rejection, payment, disbursement | Users must poll; feels like a scam | Transactional SMS/email with templates |
| P1-35 | No tests, CI, linting, type checking, or staging | Every change is a gamble | pytest + factories + coverage gate + GitHub Actions |
| P1-36 | No `README`, `.gitignore`, `.env.example`, or runbook; local run is broken | Onboarding impossible; secrets/artifacts risk | Document setup; fix `run_local`; add ignores |
| P1-37 | No health check, Sentry, metrics, alerts, or backups | Failures are discovered by customers | `/healthz/`, Sentry, dashboards, alerts, PITR |
| P1-38 | Render free plan sleeps; no SLA; Supabase free may pause | Webhook timeouts; cold starts; data risk | Paid tier or alternative; keep-warm; backups |
| P1-39 | Migrations run at build time, not release time | Concurrent/aborted builds; bad rollback story | Release command + backup + migration plan |
| P1-40 | Registration camera and drag-and-drop are blocked by native `required` file inputs | Two advertised upload paths cannot submit | Set `input.files` from `DataTransfer` or drop `required` |
| P1-41 | HEIC/HEIF rejected; 5 MB originals uploaded uncompressed | iPhone users blocked; costly on mobile data | Accept/convert HEIC; resize client-side |
| P1-42 | No support channel, FAQ, contact, or complaint process | Consumers have nowhere to go | WhatsApp/SMS support + complaints SLA |

---

## 5. Medium and low findings (P2/P3)

### 5.1 Code and configuration quality

- **Dead configuration:** `SIMPLE_JWT` (SimpleJWT is not installed), `THROTTLE_ANON/USER`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `SUPABASE_DB_SSLMODE`, `APP_COUNTRY`, `DEFAULT_FROM_EMAIL` are declared or defined but never used.
- **Dependency drift:** root `requirements.txt` and `Backend/requirements.txt` differ; `supabase` is only in the root; `psycopg2-binary`, `django-cors-headers`, `supabase` are unpinned; no lockfile; no Python version pin.
- **Base directory coupling:** `BASE_DIR` is three parents up from `core/settings.py`, which assumes the repo layout; copying the backend (or containerising it) breaks template/static discovery.
- **Static pipeline:** `STATICFILES_STORAGE` is deprecated in Django 5; use `STORAGES`; use `CompressedManifestStaticFilesStorage` for cache-busting; HTML pages are also copied into `staticfiles` (duplication); default cache headers are weak.
- **No `.gitignore`:** running any command creates `__pycache__` and `staticfiles/` in the repo; these are untracked today but one `git add .` commits them.
- **Error envelope:** `core/exceptions.py` wraps errors as `{"error": {...}}`, while the frontend expects `data.error` to be a string; dict errors render as `[object Object]`.
- **Ad-hoc responses:** endpoints return hand-built dicts instead of serializers, which is how the `has_loan`/`has_active_loan` break happened.
- **Unused/dead code:** `CurrentLoanView`, `SIMPLE_JWT`, `loading-shimmer` CSS, `verification_api` (UI renders server-side), commented "FIX" breadcrumbs.
- **No API versioning, schema, or docs:** integration and regression testing are harder than necessary.
- **Admin template outside admin:** the review page extends `admin/base_site.html` but is served from `/api/...`; it is not linked from the admin index.

### 5.2 UI, brand and conversion

- **Two brands:** "PesaFix" on the landing page, "Loan Platform" everywhere else; page titles are generic. Pick one name and use it consistently.
- **Trust design mismatch:** a dark neon "cyber" theme with animated glow orbs is a poor fit for a financial product in a market where trust is the primary conversion barrier. The registration page mixes light photo cards with the dark theme; the admin is default Django light. Three visual languages.
- **No fee calculator before signup:** users must upload an ID and selfie before they can see the cost. Show pricing and a quote first.
- **Fixed amount grid:** only 8 amounts are clickable, while the API accepts 1,000–60,000; the grid hides the fee cliffs and gaps.
- **Fee presentation:** a flat "service fee" with no percentage, APR, total cost, or repayment expectation is misleading by omission. The 3,000–5,000 band charges the same 680, so 5,000 is strictly better; nothing explains this.
- **No review/confirmation step** before launching Paystack; one click starts a payment.
- **No receipt, history, or status timeline**; after disbursement the dashboard says "No active loan".
- **No "forgot password"**; a typo at registration locks the account permanently.
- **No password confirmation field**; higher typo risk.
- **Consent and trust copy:** no "why we need your ID", no privacy link, no data-retention statement, no terms checkbox.
- **Brand assets:** `logo.png` is **1.4 MB** — very heavy for a Kenyan mobile audience; ship SVG/WebP under ~50 KB.
- **Performance:** fixed blurred orbs, grid overlay, backdrop filters, and multiple animations run continuously; test on a low-end Android over 3G.
- **Accessibility:** modals lack `role="dialog"`, focus trap, and `aria-modal`; icon-only buttons lack labels; error text is not tied via `aria-describedby`; emoji are used as icons; contrast needs checking.
- **No meta description/OG tags, sitemap, robots.txt, or analytics.** No funnel instrumentation, so conversion cannot be measured.
- **No Swahili/localisation**, despite the target market.

### 5.3 Documentation and project hygiene

- No README, architecture note, API docs, `.env.example`, contribution guide, security policy, licence, or changelog.
- The local runner (`run_local.bat`) activates `env\Scripts\activate` (a venv that is not documented), then serves `./Frontend` — **a directory that does not exist** (it is `Backend/Frontend`). It also starts Django with `DEBUG=False`, so `runserver` does not serve static files (I verified `/static/css/style.css` → 404 without `collectstatic`), and CORS is not configured for `:8001` → `:8000`.
- The repository is a single grafted commit whose message is "Update register.html" containing the entire app. There is no reviewable history, no issue trail, and no release tag.
- No staging environment, no feature flags, no maintenance mode, no data seeding.

---

## 6. Data protection and regulatory review (Kenya)

> This is an engineering risk assessment, not legal advice. Engage Kenyan counsel and a data-protection advisor before launch.

### 6.1 Kenya Data Protection Act 2019 (DPA)

| Requirement | Status in the build | Action |
|---|---|---|
| Registration as data controller/processor with ODPC | No evidence | Register before processing |
| Lawful basis and purpose limitation | None stated | Define basis (consent/contract), state purpose |
| Privacy notice at collection | None | Add layered notice next to uploads |
| Explicit consent for sensitive data (ID, biometric-like selfie) | No checkbox | Add granular consent + records |
| Data minimisation | Collects ID front, back, selfie, ID number, phone for everyone before qualification | Collect only after eligibility; consider vendor verification |
| Storage limitation / retention | None | Define retention, automate deletion |
| Security safeguards | Public URLs, plaintext ID, service-role key | Private bucket, encryption, signed URLs, access logs |
| Data-subject rights (access, correction, deletion, objection) | No endpoints/process | Build export/delete + SLA |
| Breach notification | No process | Incident runbook + ODPC notification |
| Cross-border transfer safeguards | Supabase region not specified | Confirm region, DPAs, transfer basis |
| Data protection impact assessment | None | DPIA for ID/biometric processing |

### 6.2 CBK Digital Credit Providers (DCP) regime

- The CBK (Digital Credit Providers) Regulations, 2022 require anyone carrying on **digital credit business in Kenya** to be licensed; operating without a licence is an offence under the CBK Act, with penalties including imprisonment and fines (reported as up to 3 years and KES 5 million under the Amendment Act). Licensing requires, among other things, data-protection policies, consumer-redress mechanisms, and evidence of funding.
- The regime has been revised since 2022; confirm the **current** application requirements, fees, and capital thresholds with CBK/counsel.
- The site's claim of being "licensed and regulated" is unsupported by anything in the repository. Remove it now.
- The DCP regime also implies conduct requirements: transparent pricing, fair collection practices, complaints handling, and credit-information reporting. None exist here.

### 6.3 The advance-fee problem

Described neutrally: the only money the platform collects is a **non-trivial upfront fee**, charged before approval, for a product with **no repayment obligation**. The customer's entire risk is borne before any service is delivered, and the platform bears no credit risk or repayment risk. That is the structure of an **advance-fee** product, not a loan.

Why this matters:

1. **Consumer harm:** a rejected or failed applicant loses money with no refund path and no contract.
2. **Regulatory exposure:** unlicensed digital credit and unfair/abusive fee practices are active enforcement areas.
3. **Business model:** a one-time fee with no repeat repayment revenue, no credit data, and no customer lifetime value is not a lending business; it is fee-for-disbursement, with adverse selection (the people most willing to pay 10–22.7% upfront are the least creditworthy).
4. **Reputation:** the layout, the "licensed" claim, the upfront fee, and the fake email domain are the same signals users are warned to associate with loan scams.

**If the intent is legitimate lending:** approve first, charge nothing upfront, deduct a disclosed fee from the principal, set a term and APR, collect repayment, report to CRB, and build collections. **If the intent is fee-for-processing:** the fee must be contingent on successful disbursement, refundable, disclosed as a percentage, and reviewed by counsel; the word "loan" should not be used.

### 6.4 AML/CFT and KYC

- A phone number, a national ID number, and three photos are not adequate customer due diligence for a money-movement product.
- There is no name, date of birth, address, income, employment, source of funds, sanctions/PEP screening, or transaction monitoring.
- Disbursing to **any** M-Pesa number supplied by the applicant (not necessarily the account holder) is a money-laundering/cash-out vector.
- Required: risk-based CDD, sanctions/PEP screening, velocity and mule-account detection, record-keeping, and suspicious-transaction reporting.

### 6.5 Consumer protection and contract law

- No terms of service, privacy policy, fee disclosure, cooling-off, complaints process, or ombudsman reference.
- No loan agreement or acceptance record; the "loan" has no term, rate, or repayment, so there is no enforceable obligation.
- No refund/cancellation policy for fees.
- No 18+ age verification.
- No physical address, company registration, or contact details.

---

## 7. Product and business-model critique

### 7.1 Pricing

| Range (KES) | Fee (KES) | Fee at low end | Fee at high end |
|---|---|---|---|
| 1,000–1,000 | 200 | 20.0% | 20.0% |
| 2,000–2,000 | 290 | 14.5% | 14.5% |
| 3,000–5,000 | 680 | 22.7% | 13.6% |
| 6,000–11,000 | 1,200 | 20.0% | 10.9% |
| 12,000–22,000 | 2,200 | 18.3% | 10.0% |
| 23,000–32,000 | 3,200 | 13.9% | 10.0% |
| 33,000–42,000 | 4,200 | 12.7% | 10.0% |
| 43,000–52,000 | 5,200 | 12.1% | 10.0% |
| 53,000–60,000 | 6,000 | 11.3% | 10.0% |

Observations:

- **Cliffs, not a curve.** The fee jumps between bands and is flat within them, so a user is incentivised to choose the top of each band (e.g. 5,000 over 3,000 for the same fee). The gaps mean 13.5% of amounts have no price at all.
- **Extremely expensive for short money.** 10–22.7% for a fee that is paid days before any money moves would be an APR in the hundreds or thousands of percent if a term existed. There is no term, so it cannot legally be priced as credit.
- **Negative economics at the low end.** After Paystack collection and payout fees, a KES 200 fee on a KES 1,000 loan may be marginal or negative. Model the current Paystack Kenya schedule before scaling.
- **No incentive alignment.** The platform earns whether or not the borrower repays, because there is no repayment. This selects for customers who cannot repay and for fraud.

### 7.2 Credit product gaps

- No income, employment, business, or affordability data.
- No credit score, bureau pull, or decision engine.
- No limit sizing or repeat-customer behaviour.
- No term, interest, APR, total repayable, due date, or schedule.
- No disbursement-to-repayment lifecycle, no partial payments, no late fees, no grace period.
- No CRB reporting (Metropol/TransUnion), which is both a regulatory expectation and the main lever for repayment.
- No collections workflow, hardship policy, or write-off process.
- No fraud controls (device/IP, velocity, mule detection, duplicate identity).

### 7.3 Trust and conversion

The first screen asks for a national ID and a selfie, then a payment, from an unknown brand on an `onrender.com` domain, with no address, no licence number, no team, no terms, and a 1.4 MB logo. Conversion will be low and fraud high. A trustworthy financial product needs the opposite order: transparent pricing → eligibility → verified identity → approved offer → disbursement → repayment.

---

## 8. Recommended target architecture

### 8.1 Logical architecture

```
Client (web / future app)
   │  HTTPS, httpOnly session or short-lived JWT + refresh
   ▼
Django API (versioned /api/v1)
   ├─ Auth & OTP service
   ├─ KYC service (vendor: ID + liveness + sanctions)
   ├─ Loan service (eligibility → offer → accept → repay)
   ├─ Payment service (idempotent, state machine)
   ├─ Ledger service (double-entry, immutable)
   └─ Admin/ops (RBAC, audit)
   │
   ├── PostgreSQL (primary + read replica, PITR backups)
   ├── Redis + worker (Celery/RQ): Paystack calls, webhooks, SMS, reconciliation
   ├── Object storage (private bucket, signed URLs, lifecycle rules)
   └── Observability (Sentry, structured logs, metrics, alerts)
```

**Rule:** no external network call inside a database transaction that holds locks. Persist intent → commit → call → update from webhook/response.

### 8.2 Loan state machine

```
DRAFT
  → SUBMITTED
  → KYC_PENDING → KYC_REJECTED (terminal, re-submit allowed)
              → KYC_VERIFIED
  → UNDER_REVIEW → DECLINED (terminal, reason)
                → OFFERED → OFFER_EXPIRED
                          → OFFER_ACCEPTED
  → FEE_PENDING (optional, deducted from principal) → FEE_PAID
  → DISBURSEMENT_PENDING → DISBURSED → REPAYMENT_DUE
                                     → PARTIALLY_REPAID
                                     → REPAID (terminal)
                                     → OVERDUE → DEFAULTED → WRITTEN_OFF
  → CANCELLED / FAILED / REVERSED (with recovery path)
```

Every transition: actor, timestamp, reason, idempotency key, and previous state. Append-only `LoanEvent`.

### 8.3 Payment processing contract

1. **Collect:** create `Payment` with `reference`, `amount`, `currency`, `purpose`, `status=PENDING`, `idempotency_key` (unique).
2. **Initialize:** call Paystack outside any transaction; store `authorization_url`, `access_code`, gateway response.
3. **Webhook:** `POST /api/v1/webhooks/paystack/`
   - verify HMAC-SHA512 over **raw body** with `PAYSTACK_SECRET_KEY`;
   - insert `WebhookEvent(provider_event_id UNIQUE, type, payload, received_at)`;
   - return `200` immediately;
   - worker processes idempotently: `charge.success` → verify → mark paid → enqueue disbursement; `transfer.success/failed/reversed` → update Transfer + Loan, alert on failure, refund/recover.
4. **Verify endpoint:** user convenience; calls the same service, never the source of truth.
5. **Reconcile:** cron every 15 minutes for pending payments, every hour for pending transfers; daily settlement report.
6. **Ledger:** every state change writes balanced debit/credit entries; the sum must be zero per transaction.

### 8.4 Security baseline

| Area | Target |
|---|---|
| Session | httpOnly `Secure` `SameSite=Lax` cookies + CSRF, or 15-min access token in memory + rotating refresh cookie |
| Passwords | Argon2, Django validators, breach-list check, OTP reset |
| Phone | OTP at signup and before changing payout number |
| Rate limits | Login 5/min/IP + 10/hour/phone; register 3/hour/IP; payment 10/hour/user; global DRF throttles |
| Admin | 2FA, IP allow-list/VPN, separate host, alert on new device |
| Secrets | Secret manager, rotation, no placeholders, gitleaks in CI |
| Headers | CSP with nonces, HSTS (only on domains you control), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` |
| Files | Private bucket, magic-byte validation, re-encode, EXIF strip, malware scan, size/dimension caps, signed URLs (5 min) |
| Logging | Structured JSON, request IDs, PII redaction, 30–90 day retention |
| Dependencies | Pinned, lockfile, Dependabot, `pip-audit`, SBOM |

### 8.5 Observability and operations

- **Metrics:** payment success rate, disbursement success rate, time-to-disburse, webhook lag, stuck loans, verification backlog and age, registration→verification→apply→pay→disburse funnel, error rate, p95 latency, Paystack balance.
- **Alerts:** any `transfer.failed`, any payment paid but not disbursed in 10 min, webhook 5xx, migration drift, verification backlog > 24h, error rate > 1%, DB connections > 80%.
- **Runbooks:** stuck payout, duplicate payout, refund, Paystack outage, Supabase outage, secret rotation, restore from backup, data-subject request, breach.
- **Backups:** daily automated + PITR; restore drill quarterly; documented RPO/RTO.

### 8.6 Delivery and management model

- **Issue register as the source of truth** (this CSV), with severity SLAs: P0 24h, P1 7 days, P2 30 days, P3 90 days.
- **Branch protection:** no direct pushes to main; PR review; required CI (lint, type, tests, migrations, security scan).
- **Definition of Done:** tests, migrations, docs, observability, security review, rollback plan.
- **Environments:** local → staging (production-like) → production; separate credentials; seeded demo data.
- **Release process:** backup → migrate (release command) → deploy → smoke test → monitor 30 min; feature flags for risky changes.
- **Weekly risk review:** payments, fraud, verification SLA, incidents, open P0/P1.
- **Monthly access review:** staff accounts, admin rights, API keys.
- **Quarterly:** DR restore test, dependency audit, DPIA review.
- **Vendor management:** written DPAs with Paystack, Supabase, Render, SMS provider; confirm data regions; monitor status pages.

---

## 9. Prioritized remediation roadmap

### Phase 0 — Stop the bleeding (24–48 hours)

**Do not accept real payments until these are done.**

1. Add `migrations/__init__.py` to `loans` and `payments`; generate and review `0002` migrations; migrate a clean database; verify tables and columns.
2. Remove the "Licensed and regulated" claim; add "demo/not live" banner if the site stays up.
3. Replace all `render.yaml` placeholders with `sync: false`; rotate any exposed keys; make settings read env for hosts/CSRF/CORS/DEBUG.
4. Use relative `/api` in the frontend; delete the `localStorage` API override.
5. Add `supabase` to `Backend/requirements.txt` and declare storage env vars; test registration end to end.
6. Fix the fee schedule gaps and the `ValidationError`/`ValueError` mismatch; unify phone normalization.
7. Fix the dashboard response contract; show verification status and the current loan.
8. Route and secure the Paystack webhook; verify with `PAYSTACK_SECRET_KEY`; handle `charge.success` and transfer events.
9. Stop marking loans `DISBURSED` on initiation; add `DISBURSEMENT_PENDING` and a retry path.
10. Make the bucket private and stop storing public URLs.

### Phase 1 — Make money safe (1–2 weeks)

- Payment/loan state machines + append-only events.
- Idempotency keys; no external calls inside transactions; worker queue.
- Reconciliation jobs and daily settlement report.
- Ledger (double-entry) and ops dashboards.
- Refund/reversal/dispute flows.
- Rate limits, CAPTCHA, password reset/OTP, generic auth errors.
- Private storage with signed URLs, EXIF strip, re-encode, access audit.
- Tests: fee ranges, phone formats, payment lifecycle, webhook signature, permissions; CI gates.
- Fix camera/drag-drop upload, HEIC, client-side compression.
- Admin registrations and a real verification queue with pagination/SLA.

### Phase 2 — Make it lawful and operable (3–4 weeks)

- Kenyan counsel: DCP licensing position, terms, privacy, fee disclosure, refunds.
- ODPC registration, DPIA, privacy notice, consent, retention, DSAR process.
- KYC vendor integration: phone OTP, ID authenticity, liveness, face match, sanctions/PEP.
- AML/CFT policy, monitoring, reporting.
- SMS/email notifications and receipts.
- Staging environment, monitoring, alerts, backups, restore drill.
- Security review/pen test; fix findings.
- Support channel and complaint SLA.

### Phase 3 — Make it a real credit product (60–90 days)

- Term, APR, total cost, repayment schedule, agreement and acceptance audit.
- Affordability/credit scoring; bureau integration (pull and report).
- Repayment via M-Pesa, partial payments, late fees, grace, hardship.
- Collections workflow and write-off policy.
- Repeat customers, limit increases, referrals.
- Unit-economics model, treasury buffer, loss provisioning.
- Analytics, experimentation, and cohort monitoring.

---

## 10. Launch go/no-go checklist

All must be **yes** before real money is accepted.

- [ ] Migrations apply cleanly to an empty database and to a production clone; `makemigrations --check` is clean.
- [ ] `loans_*` and `payments_*` tables and all model columns exist in production.
- [ ] Payment success is fulfilled by a verified webhook, not the browser; reconciliation runs.
- [ ] Disbursement is marked complete only on `transfer.success`; failures retry and alert.
- [ ] No secret placeholders; all keys rotated; secret scanning in CI.
- [ ] Hosts, CSRF, CORS, and API base are environment-driven and tested on staging.
- [ ] Registration works end to end with photos on the deployed build.
- [ ] Fee schedule has no gaps; quote endpoint; 01X numbers work; fee/APR disclosed.
- [ ] Dashboard shows current loan, history, receipts, and verification status.
- [ ] Identity documents are private, access-logged, consented, and retention-bound.
- [ ] No fee is charged before approval; refund path exists and is tested.
- [ ] Terms, privacy, fee disclosure, complaints, and contact pages are live.
- [ ] DCP licensing position confirmed by counsel; no unsupported regulatory claim.
- [ ] ODPC registration/DPIA complete; DSAR process tested.
- [ ] KYC/AML controls: OTP, duplicate ID, sanctions/PEP, monitoring.
- [ ] Rate limits, CAPTCHA, password reset, generic auth errors, admin 2FA.
- [ ] Backups + restore drill; monitoring + alerts + on-call; health check.
- [ ] CI green: tests, lint, types, migrations, dependency scan.
- [ ] Staging smoke test and rollback plan signed off.
- [ ] Support channel staffed; incident runbook published.

---

## Appendix A — Fee schedule gaps (verified)

```
Amounts tested: 1,000–60,000 inclusive (59,001)
Amounts with no configured fee: 7,992 (13.5%)
Gap ranges: (1,001–1,999) (2,001–2,999) (5,001–5,999) (11,001–11,999)
            (22,001–22,999) (32,001–32,999) (42,001–42,999) (52,001–52,999)
Exception raised: django.core.exceptions.ValidationError
Caught by view: except ValueError  → not caught → HTTP 500
```

The eight amounts offered in the dashboard (1,000 / 2,000 / 3,000 / 5,000 / 10,000 / 20,000 / 35,000 / 50,000) all happen to be valid, which masks the bug in the UI.

## Appendix B — Data model gaps

| Entity | Missing |
|---|---|
| `User` | Real name, DOB, email, address, consent records, OTP state, failed-login counters, last login/IP, unique national ID, verification version/history, deletion state |
| `Loan` | Product, term, interest, APR, total repayable, due date, principal/interest/fees outstanding, repayment schedule, approved/declined/disbursed timestamps, decision reason, agreement version, acceptance record, collections state |
| `Payment` | Currency, purpose, status lifecycle, channel, gateway response, fees, refund/dispute links, idempotency key, attempt count, customer reference |
| `Transfer` | Amount, currency, recipient name, failure reason, attempt count, provider status history |
| New | `LoanEvent`, `PaymentEvent`, `WebhookEvent`, `LedgerEntry`, `Refund`, `Dispute`, `AuditLog`, `ConsentRecord`, `DocumentAccessLog`, `Repayment`, `RepaymentSchedule`, `Notification` |

## Appendix C — Verification evidence

| Check | Result |
|---|---|
| `python manage.py check` (with dummy env) | "System check identified no issues" — does not catch migrations |
| Clean `migrate` | Only `users_*`, `auth_*`, `admin`, `sessions` created |
| `Loan.objects.count()` | `OperationalError: no such table: loans_loan` |
| `makemigrations --check` with migrations package added | `loans.0002` (2 fields + index/option changes), `payments.0002` (2 fields + index renames), `users.0003` (index renames) |
| `makemigrations` without `__init__.py` | Silently rewrote `0001_initial.py` — dangerous |
| Fee loop 1,000–60,000 | 7,992 gaps; `ValidationError` not a `ValueError` |
| `GET /static/css/style.css` with `DEBUG=False`, no collectstatic | 404 |
| `GET /debug-env/` | 200, reveals configured-secret booleans |
| `GET /api/loans/active/` unauthenticated | 401 (correct) |
| `GET /api/loans/active/` response keys | `has_active_loan`, `loan`; UI expects `has_loan` and flat fields |
| Env vars declared but never read | `CORS_ALLOWED_ORIGINS`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_DEBUG`, `SUPABASE_DB_SSLMODE`, `THROTTLE_ANON`, `THROTTLE_USER` |
| Env vars read but never declared | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` |
| Tests / CI / README / `.gitignore` | None found |

## Appendix D — References

- Paystack, *Webhooks* — signature is HMAC-SHA512 of the raw body using your secret key: <https://paystack.com/docs/payments/webhooks/>
- Central Bank of Kenya, *Digital Credit Providers Regulations, 2022*: <https://www.centralbank.go.ke/wp-content/uploads/2022/03/L-.N.-No.-46-Central-Bank-of-Kenya-Digital-Credit-Providers-Regulations-2022.pdf>
- CBK, *Regulation of Digital Credit Providers*: <https://www.centralbank.go.ke/2022/03/21/central-bank-of-kenya-digital-credit-providers-regulations-2022/>
- Kenya Law, *CBK (Digital Credit Providers) Regulations*: <https://new.kenyalaw.org/akn/ke/act/ln/2022/46/eng@2022-04-22>
- Kenya *Data Protection Act, 2019* and ODPC guidance: <https://www.odpc.go.ke/>
- Kenya *Consumer Protection Act, 2012*; *Proceeds of Crime and Anti-Money Laundering Act, 2009* (as amended)

---

### Closing assessment

The author can clearly build a clean Django application and a polished mobile interface. What is missing is the part that matters in financial software: **schema discipline, payment lifecycle correctness, lawful data handling, and a real credit product**. Fix the eleven blockers before anyone pays a shilling through this system; then treat Phase 1 and Phase 2 as the minimum bar for a public launch.

The fastest path to a credible product is not to add features. It is to **remove the upfront fee**, **approve before charging**, **deduct fees from disbursement**, **add a term and repayment**, and **make identity data private and consensual**. Everything else in this report supports those five decisions.
