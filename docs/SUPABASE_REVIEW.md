# Supabase review — tenancy, RLS and indexes

**Status: review + reference SQL. Nothing in this document has been applied to a
database.** No Supabase project was reachable from the review environment, and
no schema change runs automatically. The SQL in `docs/supabase_rls.sql` is a
proposal for a human to read, adapt and apply deliberately.

## 1. What the request asked for, and what is true today

| Asked for | Where it stands |
| --- | --- |
| Tables `users`, `loans`, `payments`, `loyalty_rewards`, `sms_audit_logs` | Four of the five exist under Django's default names. There is no single `loyalty_rewards` table; loyalty is split into an account, repayments, an append-only event ledger and a vault ledger (§2). |
| Users only see their own rows | Enforced **server-side** in every queryset (`filter(user=request.user)`), and covered by tests. It is *not* enforced by Postgres RLS today (§3). |
| RLS policies | Not enabled. Reference policies are in `docs/supabase_rls.sql` (§4). |
| Index Paystack references | `loans.paystack_reference` is `UNIQUE` (already indexed). `payments.reference` is `UNIQUE`. Missing composite indexes on `payments(provider, provider_reference)` and `payments(provider_transaction_id)` were **added** in migration `payments.0004_provider_reference_indexes` (§5). |
| Index M-Pesa receipts | **No M-Pesa receipt number is stored anywhere in the schema.** See §5.3 before adding one. |
| Index audit timestamps | `sms_audit_logs` already indexes `(phone_hash, created_at)`, `(ip_hash, created_at)` and `(purpose, created_at)`. `users_user.created_at`, `webhook_events.received_at`, `loyalty_events.created_at` and `vault_entries.created_at` are indexed (§5). |

## 2. Table map

Django's `app_label_modelname` convention is used throughout; there are no
`db_table` overrides, so the names below are the real table names in Postgres.

| Requested name | Actual table(s) | Rows per user | Notes |
| --- | --- | --- | --- |
| `users` | `users_user` | 1 | Phone (unique, indexed), national ID (unique), password hash, `verification_status`, `password_changed_at`. |
| `users` (audit) | `users_smsauditlog` | 0..n | Append-only SMS log. Stores an HMAC of the phone number, never the message body and never the OTP. |
| `users` (OTP) | `users_passwordresetcode` | 0..n | Hashed 6-digit code, `expires_at`, `attempts`, `used_at`. Never the plaintext code. |
| `loans` | `loans_loan` | 0..n | `paystack_reference` (unique), `mpesa_phone`, status, fee fields. |
| `loans` (history) | `loans_loanevent` | 0..n | Immutable status history. |
| `payments` | `payments_payment` | 0..n | `reference` (unique), `provider_reference`, `provider_transaction_id`. |
| `payments` (ledger) | `payments_ledgerentry`, `payments_paymentevent`, `payments_transferevent`, `payments_refund` | 0..n | Double-entry ledger and immutable event rows. |
| `payments` (webhooks) | `payments_webhookevent` | shared | Provider deliveries; contains every user's payloads. |
| `loyalty_rewards` | `loyalty_loyaltyaccount` (balances/tier), `loyalty_loyaltyevent` (reward ledger), `loyalty_repaymentrecord` (provider-confirmed repayments), `loyalty_vaultentry` (vault moves), `loyalty_creditsnapshot` (score history) | 1 account, many rows | Four tables instead of one because balances must never be computed by replaying a mutable row, and the ledger must be append-only. |
| `sms_audit_logs` | `users_smsauditlog` | 0..n | See above. |

Deliberately absent: a `credit_badges` table. The badge is a *projection* of
`loyalty_creditsnapshot` served as `credit.portable = false`; it is not a
transferable asset, so there is no row to share, port or sell.

## 3. Tenancy today, and why RLS is not the current control

Every API view scopes its queryset by the authenticated user and returns 404
rather than 403 for another user's object, so the existence of an ID is not
disclosed. `loyalty` endpoints do the same through `request.user`, and the
vault service re-reads the account inside `transaction.atomic()` with
`select_for_update()`. Tenancy is covered by tests (`test_loyalty.py`,
`test_payments_and_webhooks.py`, `test_api_contract.py`).

Two facts stop RLS from being the enforcement layer **as the project is
configured**:

1. **The connection role owns the tables.** `SUPABASE_DB_USER` defaults to
   `postgres`, the project owner. In Postgres, a table owner bypasses its own
   RLS policies unless `FORCE ROW LEVEL SECURITY` is set. So enabling policies
   without changing the role would create the *appearance* of protection with
   none of the effect.
2. **There is no Supabase identity to key on.** The API authenticates with a
   custom JWT against `users_user.id` (a `bigint`). Supabase policies normally
   compare against `auth.uid()`, a `uuid` from Supabase Auth. The two are not
   the same identity store, so `auth.uid() = user_id` cannot work without a
   bridge.

RLS is still worth having, as **defence in depth** against a future mistake:
a Supabase client library added to the frontend, a leaked anon key, or a
reporting tool pointed at the database. But it protects a path that does not
exist yet, so it is a deployment task, not a code task:

- create a dedicated login role `mb_app` with `SELECT/INSERT/UPDATE/DELETE`
  only, and use it for the web service;
- keep a separate owner role for `manage.py migrate`;
- enable and `FORCE` RLS on the tables listed below;
- keep the `supabase` service key and `postgres` password server-side only.

That role split touches deployment configuration and the database connection,
so it is **not** changed here — it needs an explicit go-ahead.

## 4. RLS design (reference SQL in `docs/supabase_rls.sql`)

Because the app's identity is a bigint and Supabase's is a uuid, the proposal
adds one small bridge table rather than rewriting every foreign key:

```sql
create table public.mb_user_identity (
  django_user_id bigint primary key references public.users_user(id) on delete cascade,
  supabase_uid   uuid   unique not null references auth.users(id) on delete cascade
);
```

Policies then resolve `auth.uid()` through that table. If the project never
adopts Supabase Auth, the alternative is a per-transaction
`SET LOCAL app.user_id = …` set by Django and a policy reading
`current_setting('app.user_id', true)`. That variant is documented in the SQL
file but not wired into the request path, because it changes database
authorization behaviour mid-request and needs approval.

Policy summary:

| Table | `SELECT` for the signed-in user | Writes |
| --- | --- | --- |
| `users_user` | own row only, via a **view** that drops `password` | own profile fields only; verification columns are staff-only |
| `loans_loan` | own rows | no client writes; state changes are server-side |
| `payments_payment` | own rows | no client writes |
| `loyalty_*` | own rows (the vault and event ledgers are read-only) | no client writes; vault moves go through the API |
| `users_smsauditlog` | **no rows** | no rows; staff/service role only |

The SMS audit log is intentionally not user-readable: it contains delivery
metadata the user has no need for, and exposing it would widen the blast
radius of a stolen token.

## 5. Index review

### 5.1 Present before this change

| Table | Index | Serves |
| --- | --- | --- |
| `loans_loan` | `UNIQUE (paystack_reference)` | fee-payment reconciliation |
| `payments_payment` | `UNIQUE (reference)` | webhook → payment lookup |
| `payments_payment` | `(status, created_at)` | dashboards, reconciliation sweeps |
| `payments_webhookevent` | `(reference)`, `(status)` | replay and retry workers |
| `users_smsauditlog` | `(phone_hash, created_at)`, `(ip_hash, created_at)`, `(purpose, created_at)` | OTP rate limits (3 per 15 min) and audits |
| `users_user` | `(verification_status)`, `(created_at)` | reviewer queue |
| `loyalty_loyaltyevent` | `(account, created_at desc)` | activity feed |
| `loyalty_vaultentry` | `(account, created_at desc)` | vault history |
| `loyalty_creditsnapshot` | `(user, created_at desc)` | score trend |
| `loyalty_repaymentrecord` | `(user, paid_at desc)`, `(on_time)` | streak and cash-back stats |

### 5.2 Added by migration `payments.0004_provider_reference_indexes`

| Index | Why |
| --- | --- |
| `payments_provider_ref_idx` on `payments(provider, provider_reference)` | `transfer.*`/`charge.*` webhook handling resolves a payment by the provider's reference. Before this, that path was a sequential scan on a table that grows with every payment, while the webhook must answer inside Paystack's 30-second budget. |
| `payments_provider_txn_idx` on `payments(provider_transaction_id)` | reconciliation and support lookups by the provider's transaction id |
| `transfers_provider_ref_idx` on `payments_transfer(provider_reference)` | the `_dispatch_event` filter in `payments/services.py` |
| `transfers_provider_code_idx` on `payments_transfer(provider_transfer_code)` | the same dispatcher's transfer-code lookup |

These are `CREATE INDEX`, not `CREATE INDEX CONCURRENTLY`: on a large
production table the migration should be split so the index is built outside
the deploy transaction. At the current data volume that is not necessary, and
doing it here would break SQLite (used by the test settings).

### 5.3 M-Pesa receipts — read this before adding a column

Paystack's documented webhook payload identifies a charge by `reference`,
`id` and `authorization`; it does **not** document an M-Pesa receipt number
(the `SJK…` code a customer sees in their SMS). Inventing a
`mpesa_receipt` column and populating it from an undocumented field would
create a column that looks authoritative and is always empty — worse than not
having it.

What exists today and is sufficient to trace a payment end-to-end:

- `payments.reference` — our idempotency key, unique;
- `payments.provider_reference` and `payments.provider_transaction_id` — what
  Paystack echoes back;
- `payments_webhookevent.payload` — the raw delivery, retained for replay, so
  any receipt code that *is* present in a future payload stays recoverable
  without a migration.

If a receipt number becomes a hard requirement, the safe order is: confirm the
exact JSON path against a real test-mode M-Pesa charge, add a nullable
`mpesa_receipt` column, backfill from `webhookevent.payload`, then add a
non-unique index (receipt numbers are unique per paybill, not across
providers — a unique constraint would fail on retries and reversals).

### 5.4 Audit timestamps

Every append-only log already carries `created_at` and is indexed by it either
directly or as the trailing column of a composite index. The one gap is
`payments_webhookevent.processed_at`, used by the retry worker; it is indexed
only through `(status)`. At current volumes `(status, received_at)` is the
useful pair, and that is already close to the existing `(status)` index, so no
change is proposed.

## 6. What was and was not done

**Done in this change**

- added four provider-reference indexes (migration + model metadata + tests
  still green);
- made the API base a deployment setting instead of a hardcoded string
  (`API_BASE_URL`, default `/api/v1`), with `/api/v1/...` routes and the
  legacy `/api/...` paths kept as aliases;
- pinned the CORS policy: explicit origin allowlist from
  `CORS_ALLOWED_ORIGINS`, no credentials, API paths only;
- documented the RLS design and shipped the reference SQL.

**Not done, needs a decision from the owner**

1. Create the `mb_app` / owner role split and enable RLS. This changes the
   database connection configuration.
2. Add a `psycopg` runtime or a Supabase client to the frontend. Nothing in
   this change adds one, and the review environment had no database.
3. Add `mpesa_receipt`. See §5.3 for the evidence needed first.
4. Add a `loyalty_rewards` table. The four existing tables already carry the
   balances, the ledger and the score history; a fifth table would duplicate
   them.
