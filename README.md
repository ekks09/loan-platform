# M-Biashara — Kenyan loan application platform

A Django 5 / DRF application that serves both the JSON API and the
server-rendered M-Biashara interface for a Kenyan short-term loan application
flow. The original prototype's critical defects have been repaired, the
interface has been rebuilt as an installable PWA, state changes are auditable,
and unsafe financial operations stay switched off by default.

> **Status:** controlled-staging ready with payments and fee collection
> disabled. The fee schedule is published as a **proposal** (`2026-10-draft`)
> and is not yet approved for collection. **Not approved** to take real money or
> issue live loans. Licensing,
> legal review, data-protection sign-off, treasury funding and independent
> security testing are still required. See `docs/REMEDIATION_REPORT.md`.

## What the application does

1. A visitor sees the full processing-fee schedule before registering.
2. Registration requires a Kenyan mobile number, National ID and explicit
   consent. Identity documents are optional at signup and can be uploaded later.
3. Staff review documents in a queue that issues short-lived signed links and
   logs every view.
4. A verified borrower applies for KES 100–60,000; the fee is computed from a
   contiguous, tested six-band schedule with a KES 45 minimum.
5. Staff issue a written offer (48-hour default). The borrower must accept the
   exact terms version before anything else happens.
6. If fee collection is ever approved, payment is confirmed by the Paystack
   webhook, not by the browser.
7. Disbursement is only `SUCCESS` after the provider confirms the transfer.
   Failures are retried or reconciled; a reversal posts a compensating ledger
   entry.

Repayment, interest and loan terms are deliberately **not implemented** — those
are business, product and legal decisions. See `docs/CREDIT_PRODUCT_SPEC.md`.

## Quick start (Linux/macOS)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r Backend/requirements.txt

cp Backend/.env.example Backend/.env
# edit Backend/.env — at minimum set DJANGO_SECRET_KEY

cd Backend
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000>. Windows: run `run_local.bat` from the repository
root.

Create a staff account for the verification queue and Django admin:

```bash
cd Backend
python manage.py createsuperuser
```

The staff queue is at `/api/users/admin/verification/` and the Django admin at
`/admin/`.

The JSON API is served under `/api/v1/` (the unversioned `/api/` paths remain
as aliases). The base is a deployment setting, `API_BASE_URL`, and is rendered
into each page — no hostname is compiled into the frontend. See
`docs/API.md` for endpoints and `docs/SUPABASE_REVIEW.md` for the database
tenancy review.

## Tests and checks

```bash
cd Backend

# 289 tests; SQLite in-memory, no network or credentials required
python manage.py test --settings=core.test_settings

# No model/migration drift
python manage.py makemigrations --check --dry-run --noinput \
  --settings=core.test_settings

# Deployment validation (CI variant enables secure cookies)
python manage.py check_config --allow-test-mode --settings=core.ci_settings
```

CI runs the same commands in `.github/workflows/backend-tests.yml`.

## Layout

| Path | Purpose |
| --- | --- |
| `Backend/core/` | Settings, error envelope, middleware, money, checks, management commands |
| `Backend/users/` | Phone identity, consent, private document storage, staff verification queue |
| `Backend/loans/` | Fee schedule, loan state machine, offers, staff operations |
| `Backend/payments/` | Payments, transfers, refunds, webhooks, double-entry ledger, reconciliation |
| `Backend/Frontend/` | Server-rendered pages, design system, JS API client, service worker, icons |
| `docs/` | Audit, resolution matrix, deployment and payment guides, product spec, refinement report |
| `render.yaml` | Render blueprint (secrets are never committed) |

## Safety gates (all off by default)

| Variable | Default | Effect |
| --- | --- | --- |
| `PAYMENTS_ENABLED` | `false` | Payment and disbursement endpoints refuse requests |
| `PAYMENTS_LIVE_MODE` | `false` | Live keys are rejected unless explicitly enabled |
| `FEE_COLLECTION_MODE` | `disabled` | No upfront fee can be collected |
| `FEE_COLLECTION_APPROVED` | `false` | Required before `upfront` mode can boot |
| `LEGAL_TERMS_APPROVED` | `false` | Required before live payments can boot |
| `REQUIRE_DOCUMENT_STORAGE` | `true` outside DEBUG | Readiness fails if the private bucket is missing |
| `PLATFORM_TESTIMONIALS_ENABLED` | `true` | `false` removes the landing-page stories block (shipped quotes are placeholders) |
| `PLATFORM_SOCIAL_PROOF` | *(empty)* | Landing-page social-proof badge; empty falls back to a non-numeric line |

## Documentation

- `docs/PLATFORM_AUDIT.md` — original audit (what was wrong and why)
- `docs/ISSUE_REGISTER.csv` — 93 tracked findings
- `docs/RESOLUTION_MATRIX.md` — finding-by-finding disposition
- `docs/DEPLOYMENT.md` — migration, Render, storage, backup and rollback guide
- `docs/RENDER_STAGING_RECOVERY.md` — recreate the staging service and pick one payment posture
- `docs/PAYMENT_VERIFICATION.md` — Paystack test/live, webhook and reconciliation runbook
- `docs/CREDIT_PRODUCT_SPEC.md` — Phase 3 product decisions (no invented terms)
- `docs/REMEDIATION_REPORT.md` — remediation verdict, test report, blockers, readiness
- `docs/M-BIASHARA_REDESIGN_REPORT.md` — redesign, PWA and fee-repricing report (A–G)
- `docs/FRONTEND_REFINEMENT_REPORT.md` — theming, navigation, motion, illustration and mobile refinement report
- `docs/LANDING_PAGE_COPY.md` — public landing-page copy system, marketing settings and claim guardrails
