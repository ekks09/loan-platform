# Remediation report

**Repository:** `ekks09/loan-platform`
**Branch:** `arena/f463a275-loan-platform` (branched from `e0e95b8`)
**Date:** 2026-10-17
**Basis:** `docs/PLATFORM_AUDIT.md` and `docs/ISSUE_REGISTER.csv` (93 findings)

---

## 1. Verdict

| Dimension | Verdict | Basis |
| --- | --- | --- |
| **Implementation** | Ready for controlled staging | Critical lifecycle, payment, security and migration defects repaired; 231 automated tests pass (224 at the remediation commit) |
| **Automated verification** | Passing | Full suite, system checks, migration drift check, config validation, `collectstatic` |
| **Staging deployment** | Not performed | No push or deploy was authorised; the blueprint is ready |
| **External approval** | Not obtained | Licensing, legal, DPIA/DPAs, Paystack live, treasury and security testing remain |
| **Real money / live loans** | **Not approved** | Safety gates default to off; live mode requires explicit legal approval at boot |

The prototype that was audited could not be deployed reliably and could take
money without fulfilling it. Those defects are fixed. What remains is
predominantly external: regulated-product approvals, vendor contracts,
independent testing and operational readiness. No amount of code removes the
need for those.

**Do not enable `PAYMENTS_ENABLED` or `PAYMENTS_LIVE_MODE` in a live
environment until every gate in section 7 is closed.**

---

## 2. What was implemented

### Phase 0 — critical failures

- **Migrations and schema**: migration packages, hand-written `0002`/`0003`
  chains for users, loans and payments, legacy column renames/backfills, and a
  third loans migration for the reversal state. A fresh database creates 24
  tables; `makemigrations --check` reports no drift.
- **Dependencies and deploy config**: pinned requirements (including Supabase),
  rewritten `render.yaml` with generated/synced secrets, env-driven hosts and
  origins, Render hostname fallback, migrations moved out of the build step.
- **Secrets**: no secret values in the repository; placeholder/short keys are
  rejected at boot; live mode requires live keys plus legal approval.
- **Pricing**: contiguous fee schedule covering KES 1,000–60,000, explicit
  errors, boundary tests, and fee disclosure before signup.
- **Phone identity**: one normalizer used everywhere, canonical `254XXXXXXXXX`,
  unique phone and National ID.
- **Dashboard contract**: `has_active_loan`, `loan`, `latest` and `status_label`
  implemented and consumed; pages render without a separate frontend server.
- **Private documents**: storage paths only, private bucket, signed URLs,
  access audit, consent records, upload fallback.
- **Lending claims/fees**: no licence claim, no invented legal details, fee
  collection disabled, fee only after approval and offer acceptance.

### Phase 1 — financial and operational correctness

- **Loan state machine**: explicit allowed transitions, immutable `LoanEvent`
  history, offer expiry and version-bound acceptance, one open loan per user,
  reversal state, late-success recovery.
- **Payments**: checkout initialisation, provider-confirmed fulfilment, exact
  status/reference/currency/amount validation, idempotency keys, payment events.
- **Webhooks**: routed, raw-body HMAC-SHA512 verification, 256 KB cap, event
  persistence keyed by body digest, duplicate handling, replay command.
- **Disbursement**: `INITIATING` → `PENDING_PROVIDER`; `SUCCESS` only on
  confirmation; UNKNOWN outcomes must be reconciled; bounded retries; reversal
  with compensating entries; attempt exhaustion fails the loan.
- **Ledger**: immutable double-entry journals, balanced enforcement, refunds and
  reversals as compensating entries.
- **Refunds**: full/partial with bounds, provider-pending states, failure
  recovery, refunded fees block disbursement.
- **Reconciliation**: service, staff API, management command and history;
  dry-run by default; ledger imbalance and duplicate-payment checks.
- **Admin and observability**: all core models registered, staff verification
  queue, document access log, health/readiness, request IDs, structured logs.
- **Security**: strict JWT claims, generic login/reset responses, scoped
  throttles, password validators, hashed reset codes, no PII in logs.

### Phase 2 — privacy, auth and staging

- Consent versioning and withdrawal-aware checks; privacy/terms drafts published
  with version variables.
- Document retention command and minimum-exposure staff queue.
- Staging blueprint with payments, fees and legal approvals off.
- Deployment, payment-verification and backup/rollback documentation.
- README, `.gitignore`, `.env.example`, rewritten local launcher.
- CI workflow for checks, migration drift and the full test suite.
- 30-minute JWT; refresh/revocation and 2FA documented as residual blockers.

### Phase 3 — product specification

`docs/CREDIT_PRODUCT_SPEC.md` records the decisions required before any credit
product is designed. It deliberately contains **no invented interest rates,
terms, fees or repayment rules**.

---

## 3. Defects found and fixed during remediation

These were discovered while writing tests or reviewing flows, not only from the
original audit:

| Defect | Consequence | Fix |
| --- | --- | --- |
| Missing `quote` throttle rate | Every fee quote returned HTTP 500 | Added `THROTTLE_QUOTE` and the `quote` scope |
| UNKNOWN transfer could never be reconciled | Stuck disbursement with no safe recovery | Provider lookup by deterministic reference when no transfer code exists |
| Reversal from `DISBURSED` had no legal target state | Provider reversal rolled back and retried forever | Added loan `REVERSED`, compensating ledger entry only when settled |
| Reversal before settlement posted a fake journal | Ledger would represent money that never moved | Reversal before settlement marks the loan `FAILED` with no journal |
| Refund amount `0` silently meant "full refund" | Ambiguous API behaviour | `None` means full; zero is rejected |
| Dashboard lacked min/max amounts | Empty "KES and KES" range | View supplies both limits |
| Staff queue included accounts with no documents | Counts and rows disagreed | Queue excludes unreviewable accounts |
| Init endpoint used a hardcoded email domain and a wrong expression | Inconsistent provider identity | Uses `internal_email()` with configurable domain |
| Verification API returned the full National ID | Unnecessary PII exposure | Returns masked identifier |
| `check_config` allowed live mode with a test key | Dangerous misconfiguration could pass | Live mode plus test key is always an error |
| Consent purposes shared one version constant | With `TERMS_VERSION` and `CONSENT_VERSION` diverging, a user who consented would be treated as not consented | Each purpose records its own current version; regression test added |
| Test settings ignored `DJANGO_SQLITE_PATH` | A caller-selected database was silently replaced by `/tmp/loan-platform-dev.sqlite3`, so fresh-database checks were not reproducible | The configured path is honoured; migration replay verified on a new file |
| CI ran `python Backend/manage.py test` from the repo root | Django discovered **zero** tests and exited 0 — a false green | Steps run in `Backend/`; CI also asserts that at least one test ran |
| Dashboard invented a provider email (`phone@example.invalid`) when the API omitted one | Placeholder data sent to the payment provider | The API's `email` is used verbatim |
| Staff queue rendered `[object Object]` for API errors | Reviewers could not see why a decision failed | Error envelope is unwrapped (`error.message`) |
| Staff card printed raw ID prefix/suffix fields | Inconsistent with the masked identifier contract | Card uses `national_id_masked` |

---

## 4. Test report

Environment: Python 3.11, Django 5.0.8, SQLite in-memory, mocked Paystack,
console SMS. No network calls, no external credentials.

### Commands and results

```text
python manage.py test --settings=core.test_settings
Ran 235 tests in ~3.8s
OK

python manage.py check --settings=core.ci_settings
System check identified no issues (1 silenced).

python manage.py makemigrations --check --dry-run --noinput --settings=core.ci_settings
No changes detected

python manage.py check_config --allow-test-mode --settings=core.ci_settings
WARN  Paystack test keys are configured; live money cannot move.
Configuration OK (1 warning(s)). payments=on mode=test fees=disabled

python manage.py collectstatic --noinput --settings=core.test_settings
192 static files after the redesign (174 at the remediation commit).
```

### Coverage by application

| Application | Tests | Focus |
| --- | --- | --- |
| `core` | 51 | Config parsing, secret/host validation, `check_config`, health/readiness, public pages, dashboard contract, migrations, commands, JWT security, error envelope |
| `users` | 51 | Phone normalization, model gates, consent, reset codes, registration, login, profile, document validation, staff queue and audit log |
| `loans` | 50 | Fee schedule, application rules, state machine, offer acceptance/expiry, cancellation, permissions, staff operations |
| `payments` | 72 | Money conversion, ledger immutability, checkout, charge validation, webhooks (signature, duplicates, replay, provider verification), transfers, retries, reversals, refunds, reconciliation |

### What is **not** covered by automated tests

- Any real Paystack sandbox/live call (network is mocked).
- Browser end-to-end flows (camera, Paystack Inline, redirects).
- Load, concurrency and race testing beyond database constraints.
- Uploads against a real Supabase bucket.
- Penetration testing, dependency scanning or accessibility audit.
- PostgreSQL-specific behaviour (tests run on SQLite; migrations were applied to
  SQLite). A staging run against Supabase Postgres is required before launch.

---

## 5. Implementation vs verification vs approval

| Claim | Can be made? | Evidence |
| --- | --- | --- |
| Defects were fixed in code | **Yes** | Diff on this branch; section 2 |
| Automated tests pass | **Yes** | 235 tests, section 4 |
| Migrations apply on a fresh database | **Yes (SQLite)** | 23 tables; drift check clean |
| The app has been deployed to staging | **No** | No push/deploy authorised |
| Payments work with Paystack | **No** | Only mocked; test-key integration run outstanding |
| The product is legally compliant | **No** | Licence, terms, DPIA and approvals outstanding |
| Real money may be handled | **No** | Safety gates remain off |

---

## 6. Residual risks

| Risk | Severity | Mitigation now | Needed next |
| --- | --- | --- | --- |
| No CBK licence / legal product sign-off | Critical | Live payments blocked | Legal opinion and licence |
| No SMS OTP phone ownership proof | High | Rate limits, National ID, manual review | Approved OTP vendor and flow |
| Staff accounts lack 2FA | High | Staff-only access, audit logs | 2FA before production |
| No live Paystack integration test | High | Mocked unit coverage | Staging run with test keys |
| Chargebacks/disputes not modelled | Medium | Refund workflow | Approved dispute procedure |
| No shared throttle cache | Medium | Per-instance limits | Redis/shared cache for multi-instance |
| No CSP / SRI for the Paystack script | Medium | Script only loaded on payment action | CSP and pinning strategy |
| 1.4 MB logo and heavy animations | Low | Functional pages | Asset optimisation |
| No browser E2E or load tests | Medium | Unit/API tests | Playwright/k6 in staging |
| Backups/monitoring not configured | High | Health endpoints and runbook | Configure and rehearse |

---

## 7. Readiness checklist

### Engineering (complete)

- [x] Fresh-database migrations apply; no model drift
- [x] System checks pass
- [x] 231 automated tests pass
- [x] CI workflow defined
- [x] Secrets removed from the repository; boot-time placeholders rejected
- [x] Private document storage design and access audit
- [x] Payment webhook signature verification and idempotency
- [x] Ledger, refunds, reversals and reconciliation
- [x] Deployment, payment and rollback documentation
- [x] Safety flags default off

### Staging (not yet evidenced)

- [ ] Deploy the blueprint to a staging service
- [ ] Apply migrations to Supabase Postgres and verify 23 tables
- [ ] Create the private bucket and set `REQUIRE_DOCUMENT_STORAGE=true`
- [ ] Create staff user; test the verification queue with sample images
- [ ] Run a Paystack **test-key** end-to-end: charge, webhook, disbursement,
      reversal, refund, reconciliation
- [ ] Configure scheduled expiry/reconciliation/retention jobs
- [ ] Verify `/health/ready/` under failure conditions
- [ ] Restore a database backup into a scratch project

### External approval (blocking for live money)

- [ ] CBK digital credit provider licence or a written legal position
- [ ] Approved terms, privacy notice, fee disclosure and complaints process
- [ ] DPIA, lawful basis, retention schedule, processor DPAs, region position
- [ ] Paystack live account and keys; webhook configured and verified
- [ ] Treasury float and settlement policy
- [ ] Independent penetration test and remediation
- [ ] Staff 2FA and access review process
- [ ] Monitoring/on-call and backup restore evidence
- [ ] Chargeback/dispute procedure
- [ ] Product decisions in `docs/CREDIT_PRODUCT_SPEC.md`

---

## 8. Diff summary

- **33 tracked files modified**, **62 new files** on the branch.
- Diff: **+5,490 / −3,246** lines in tracked files; new files add roughly
  **8,700** lines including tests and documentation.
- New migrations: `users/0003`, `loans/0002`, `loans/0003`, `payments/0002`,
  `payments/0003`; original `0001` migrations untouched.
- New test modules: 12 files; the suite now holds 235 tests.
- New documents: this report, resolution matrix, deployment guide, payment guide,
  product specification, README.
- No push, no deploy, no provider calls, no production data touched.

Key runtime changes:

- `core/`: settings, config validation, errors/envelope, money, middleware,
  notifications, checks, context processors, commands, tests.
- `users/`: canonical phone, identity/consent models, private storage, API,
  staff queue, admin, JWT authentication, tests.
- `loans/`: fee schedule, state machine, services, API, admin, migrations, tests.
- `payments/`: payment/transfer/refund/webhook/ledger models and services,
  Paystack client, admin, migrations, tests.
- `Frontend/`: rebuilt landing, register, login, dashboard, legal pages and staff
  queue; shared API client; no hardcoded API host; styles extended.
- Ops: `render.yaml`, requirements, `.gitignore`, `.env.example`,
  `run_local.bat`, CI workflow.

---

## 9. Reproduce

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r Backend/requirements.txt

cd Backend
python manage.py migrate --settings=core.test_settings
python manage.py test --settings=core.test_settings
python manage.py makemigrations --check --dry-run --noinput \
  --settings=core.test_settings
```

Local UI: copy `Backend/.env.example` to `Backend/.env`, set a development
secret, run `python manage.py runserver`, and open <http://127.0.0.1:8000>.

---

## 10. Final statement

The codebase has moved from an undeployable prototype to a defensible
controlled-staging implementation: money movement is gated, every state change
is auditable, documents are private, and the failure paths are tested.

It is **not** a licensed lending product. Until the external gates in section 7
are closed, the only correct production posture is the one configured here:
payments disabled, fees disabled, terms unapproved, and no credit offered.
