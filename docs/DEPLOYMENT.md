# Deployment and migration guide

This guide describes a controlled staging deployment. It does **not** approve a
production launch; see `docs/REMEDIATION_REPORT.md` for the external gates.

## 1. Architecture

- One Django service serves the HTML pages, the versioned JSON API under
  `/api/v1/` (with `/api/` kept as an alias), the Paystack webhook and the
  health endpoints.
- PostgreSQL (Supabase) stores all state. Identity images live in a **private**
  Supabase Storage bucket; only storage paths are stored in Postgres.
- Paystack is the only payment provider. All provider calls are server-side.
- Static files are collected by `collectstatic` and served by WhiteNoise. No
  separate frontend server or CDN is required.

```
Browser ──HTTPS──> Django (Render) ──SQL──> Supabase Postgres
                        │
                        ├── private object storage ──> Supabase Storage
                        └── HTTPS ──> Paystack (transactions, transfers, refunds)
```

## 2. Prerequisites

| Item | Requirement |
| --- | --- |
| Python | 3.11.x |
| Database | PostgreSQL 14+ (Supabase project) |
| Object storage | Supabase Storage, **private** bucket |
| Payments (optional) | Paystack account with **test** keys |
| SMS (optional) | Africa's Talking account, or `SMS_BACKEND=none` |
| Hosting | Render web service (the included blueprint targets it) |

Do not enable payments until the gates in section 10 are met.

## 3. Environment variables

Secrets must be set in the platform's secret manager, never committed. The
`.env.example` file lists every variable with safe local values.

### Required in every non-debug deployment

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY` | ≥32 chars, random. Render blueprint uses `generateValue` |
| `DJANGO_ALLOWED_HOSTS` | Optional when Render provides `RENDER_EXTERNAL_HOSTNAME` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Optional when derived from the Render hostname |
| `SUPABASE_DB_HOST` / `_NAME` / `_USER` / `_PASSWORD` | Postgres connection |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Private document storage |

### API surface

| Variable | Default | Notes |
| --- | --- | --- |
| `API_BASE_URL` | `/api/v1` | Relative is correct for this single-service deployment. Set an absolute `https://…/api/v1` only for a split frontend; the browser reads it from a meta tag, so no rebuild is needed. |
| `CORS_ALLOWED_ORIGINS` | *(empty)* | Exact origins, comma separated. Only needed for a split frontend. Credentials are always off; the API uses bearer tokens. Wildcards are rejected. |

### Safety flags

| Variable | Staging value | Meaning |
| --- | --- | --- |
| `PAYMENTS_ENABLED` | `false` | No payment or disbursement operations |
| `PAYMENTS_LIVE_MODE` | `false` | Test keys only if payments are enabled |
| `FEE_COLLECTION_MODE` | `disabled` | No upfront fee |
| `FEE_COLLECTION_APPROVED` | `false` | Must be true together with `upfront` |
| `LEGAL_TERMS_APPROVED` | `false` | Blocks live mode at boot |
| `REQUIRE_DOCUMENT_STORAGE` | `false` initially | Set `true` once the bucket exists |
| `SMS_BACKEND` | `none` | Set `africastalking` only with approved sender ID |
| `DJANGO_HSTS_SECONDS` | `0` | Raise only after the final domain is stable |

Notes:

- `PAYMENTS_ENABLED=false` with any Paystack key present is refused at boot.
  Remove the keys or enable payments; ambiguity is treated as unsafe.
- `PAYMENTS_LIVE_MODE=true` requires an `sk_live_` key **and**
  `LEGAL_TERMS_APPROVED=true`; otherwise the process refuses to start.
- `FEE_COLLECTION_MODE=upfront` requires `FEE_COLLECTION_APPROVED=true`.
- `DJANGO_SECURE_SSL=true` is expected behind Render's TLS terminator. The app
  trusts `X-Forwarded-Proto`; HSTS stays opt-in.

## 4. First deployment (Render)

1. Create the Supabase project, database and a **private** storage bucket
   (default name `identity-documents`).
2. In Render, create a Blueprint from this repository. `render.yaml` builds
   with:
   - `pip install -r Backend/requirements.txt`
   - `python Backend/manage.py collectstatic --noinput`
3. Fill every `sync: false` variable in the Render dashboard. There are no
   secret values in the repository.
4. Deploy. The start command is:

   ```bash
   python Backend/manage.py migrate --noinput
   gunicorn core.wsgi:application --chdir Backend \
     --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60
   ```

   Migrations run at start, not during the build, so a failed migration stops
   the service instead of leaving a half-deployed instance.
5. Confirm:
   - `GET /health/` → `{"status":"ok"}`
   - `GET /health/ready/` → 200 with `database: ok`
   - `GET /login/` and `/` render correctly
6. Create the first staff user from the Render shell:

   ```bash
   python Backend/manage.py createsuperuser
   ```

7. Open `/api/users/admin/verification/` as that staff user. It redirects to
   the Django admin login.

### Hostname handling

If `DJANGO_ALLOWED_HOSTS` is empty, settings use
`RENDER_EXTERNAL_HOSTNAME`. Likewise, CSRF origins default to
`https://RENDER_EXTERNAL_HOSTNAME`. Set the explicit variables when using a
custom domain; wildcards are rejected outside DEBUG.

## 5. Row Level Security (not enabled)

Django connects as the Supabase project owner, and an owner bypasses RLS. The
API's tenancy is therefore enforced in the ORM, not in Postgres. Reference
policies and the role split needed to make RLS effective are in
`docs/SUPABASE_REVIEW.md` and `docs/supabase_rls.sql`; **nothing there is
applied automatically**, and enabling it changes the database connection, so it
is a separate, approved migration step.

## 6. Storage bucket

The bucket must be **private**. The service-role key stays server-side and is
used only for uploads and short-lived signed URLs (`DOCUMENT_SIGNED_URL_TTL`,
default 300 seconds). Public bucket policies, public URLs or client-side
Supabase keys are not part of this design.

After provisioning, set `REQUIRE_DOCUMENT_STORAGE=true` and redeploy so
readiness fails if storage is later misconfigured.

## 7. Migrations

Current migration chain:

| App | Migrations |
| --- | --- |
| `users` | `0001`, `0002` (original), `0003_identity_consent_and_security`, `0004_password_reset_codes`, `0005_sms_audit_log` |
| `loans` | `0001_initial`, `0002_loan_lifecycle`, `0003_alter_loan_status` |
| `payments` | `0001_initial`, `0002_payment_state_and_ledger`, `0003_remove_legacy_transaction_id`, `0004_provider_reference_indexes` |
| `loyalty` | `0001_initial` |

The original `0001` files are preserved. Repairs are additive migrations that
rename legacy columns, backfill keys and add state/ledger tables. On a fresh
database the chain creates 25 tables, five of them for loyalty and credit
health. `0004_provider_reference_indexes` adds only indexes; it does not
rewrite rows.

### Pre-deploy checklist

```bash
cd Backend
python manage.py showmigrations
python manage.py makemigrations --check --dry-run --noinput \
  --settings=core.test_settings
```

`makemigrations --check` must print **No changes detected**. If it does not,
stop: generating a migration against an unknown database state is how schema
drift happens.

### Applying

```bash
python manage.py migrate --noinput
```

Take a database backup/snapshot first for any migration that alters existing
columns. All current migrations are additive or rename-with-backfill and have
been applied to a fresh database and to the development copy.

### Rolling back

Forward-fix is preferred. If a rollback is unavoidable:

1. Stop traffic (scale the service to zero or disable the deploy).
2. Snapshot the database.
3. `python manage.py migrate <app> <previous_migration>` for the affected app.
4. Redeploy the matching code version.

`payments/0002` and `0003` drop legacy columns; rolling them back restores the
columns but **not** their data. Restore from the snapshot when data matters.

## 8. Backups and restore

- Enable Supabase automated backups / point-in-time recovery on the production
  database. This is an account-level setting, not something this repository can
  configure.
- Test a restore into a scratch project at least quarterly.
- Record actual RPO/RTO once the plan and backup tier are chosen. They are
  business decisions and are intentionally not invented here.
- Object storage: document retention is enforced by
  `manage.py purge_documents` (dry-run by default). Storage backups follow the
  Supabase plan; confirm coverage with the provider.

## 9. Monitoring and operations

| Signal | Where |
| --- | --- |
| Liveness | `GET /health/` |
| Readiness (DB, storage, legal/payment gates) | `GET /health/ready/` |
| Request correlation | `X-Request-ID` on every response; also in logs |
| Webhook failures | `WebhookEvent.status=FAILED`; replay with `manage.py replay_webhooks` |
| Stuck disbursements | `/api/payments/ops/transfers/`, then reconciliation |
| Reconciliation history | `/api/payments/ops/reconciliations/`, `ReconciliationRun` |

Recommended alerts (configure in the hosting/monitoring provider):

- readiness non-200 for 2 minutes;
- 5xx rate above baseline;
- any `WebhookEvent` FAILED for more than 15 minutes;
- transfers in `UNKNOWN` or `INITIATING` for more than 30 minutes;
- reconciliation run failed.

Scheduled jobs to add (Render cron or an external scheduler):

```bash
python Backend/manage.py expire_offers --apply
python Backend/manage.py reconcile_payments          # dry-run, alert on issues
python Backend/manage.py retry_disbursements         # dry-run, inspect first
python Backend/manage.py purge_documents             # dry-run
```

Only pass `--apply` after reviewing the dry-run output. Never blind-retry an
`UNKNOWN` transfer; reconciliation must run first.

## 10. Go-live gates (not satisfied by this repository)

Do not set `PAYMENTS_ENABLED=true` in a live environment until all of these are
signed off outside engineering:

1. Valid CBK digital credit provider licence (or a written position from
   counsel that the product does not require one).
2. Approved terms, privacy notice, fee disclosure and complaints process.
3. Data-protection impact assessment, lawful basis, retention schedule,
   processor DPAs and cross-border transfer position.
4. Treasury funding and settlement/float policy.
5. Paystack live account approval, live keys and webhook secret rotation.
6. Independent penetration test and remediation of findings.
7. Two-factor authentication for staff/admin accounts and an access-review
   process.
8. Backup restore test, monitoring/alerting, incident and refund runbooks.
9. Successful end-to-end staging run with test keys, including failure,
   reversal and refund paths.

## 11. Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Boot fails: `DJANGO_ALLOWED_HOSTS` | No hosts and no `RENDER_EXTERNAL_HOSTNAME` | Set hosts explicitly |
| Boot fails: placeholder secret | Secret still `<...>` or too short | Set a real ≥32-char key |
| Boot fails: payments configuration | Keys present while disabled, or live/test mismatch | Make the flags and keys consistent |
| 503 on `/health/ready/` | DB, storage or legal gate failing | Read the `checks` object |
| `DisallowedHost` | Proxy host not allowlisted | Add the host; do not use `*` |
| Webhook 401 | Signature computed with the wrong key or body altered | Use the Paystack secret key and raw body |
| Webhook 503 | `PAYMENTS_ENABLED=false` | Expected in staging; enable only after gates |
| Documents fail with 503 | Storage not configured or bucket missing | Set Supabase URL/key/bucket |
