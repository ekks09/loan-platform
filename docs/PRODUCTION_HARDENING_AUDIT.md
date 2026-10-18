# Production hardening audit (Phase 0 baseline)

**Repository:** `ekks09/loan-platform`
**Branch:** `arena/3c5f72c4-loan-platform` (branched from `main` @ `bc3f812`)
**Date:** 2026-10-18
**Scope:** payment-provider abstraction (Paystack first), storage abstraction,
notification provider + OTP reliability, dependency hygiene, CI gates,
Docker Compose, PWA cache, staging promotion, documentation.

This document is the verified baseline. Every claim below was checked against
the actual tree; where it contradicts an earlier assumption, the code wins.

---

## 1. Verified baseline

| Check | Command | Result |
| --- | --- | --- |
| Test suite | `manage.py test --settings=core.test_settings` | **290 tests, all pass** (SQLite) |
| System checks | `manage.py check --settings=core.test_settings` | no issues (2 intentionally silenced) |
| Migration drift | `manage.py makemigrations --check --dry-run` | no drift |
| Config validation | `manage.py check_config --allow-test-mode --settings=core.ci_settings` | OK (1 expected test-key warning) |
| Static collection | `manage.py collectstatic --noinput --dry-run` | 217 files |
| Working tree | `git status` | clean, single commit on branch |
| Sandbox tooling | — | Python 3.11.2, Node 22, **no Docker daemon, no local PostgreSQL, no outbound access to paystack.co/supabase.io** |

Consequences of the sandbox limits, stated up front:

- PostgreSQL-backed tests are delivered as a CI job (service container) and a
  docker-compose environment; they cannot be executed in this sandbox.
  SQLite remains the local fast suite. PostgreSQL-specific integration tests
  skip cleanly when no database is reachable.
- MinIO integration tests are written to run automatically when
  `S3_TEST_*` endpoints are reachable and skip otherwise.
- Paystack endpoint shapes were **not** re-verified against live documentation
  from this sandbox (network policy). The adapter therefore preserves the
  endpoint paths, signing algorithm (HMAC-SHA512 with the secret key over the
  raw body), and status vocabulary already implemented and tested in
  `payments/paystack.py` + `payments/webhook.py`; nothing is invented. Any
  provider change must still be re-checked against official Paystack docs at
  deploy time (noted in `docs/PAYMENT_VERIFICATION.md`).

## 2. Architecture today (verified)

- **Django 5.0 monolith**, apps: `core`, `users`, `loans`, `payments`, `loyalty`.
  Static frontend in `Backend/Frontend`, served by the same process. Same-origin
  API at `/api/v1/` with unversioned aliases.
- **Payments**: `payments/services.py` business logic calls
  `payments/paystack.py::PaystackClient` **directly**. Paystack-specific
  response parsing (`data["status"] == "success"`, `authorization_url`,
  `access_code`, transfer status strings, refund status strings) lives in the
  business-service layer. The frontend loads Paystack Inline JS from
  `https://js.paystack.co/v1/inline.js` (`page-dashboard.js::loadPaystack`,
  wrapper `Frontend/js/paystack.js`) and uses `paystack_public_key` /
  `access_code` from the init response.
- **Webhooks**: `payments/webhook.py` verifies HMAC-SHA512 with
  `PAYSTACK_SECRET_KEY` over the raw body, constant-time compare, 256 KB cap,
  persists by body digest, idempotent dispatch, replay command. Sound — kept.
- **Ledger**: immutable balanced double-entry (`LedgerEntry`), compensating
  entries for refunds/reversals. Sound — kept.
- **Transfers**: claimed under `select_for_update`, `UNKNOWN` must be
  reconciled before retry, bounded attempts, no blind retry. Sound — kept.
- **Money**: whole-KES integers, minor units only at the provider boundary,
  `from_minor` rejects non-whole amounts. Sound — kept.
- **Storage**: `users/storage.py::SupabaseStorage` class used directly by
  `users/views.py` and the purge command. Magic-byte validation, private
  bucket, signed URLs, 5 MB cap. Single provider, no interface.
- **Notifications**: `core/notifications.py::send_sms` with
  console/africastalking/none backends. Single channel. OTP limits,
  fingerprinted audit log, hashed codes, 5 attempt cap already exist.
- **Deploy**: `render.yaml` single free web service, Frankfurt, branch `main`,
  `autoDeploy: false`, migrations at start, health check `/health/`.
- **CI**: one workflow (`backend-tests.yml`): tests, checks, migration drift,
  `check_config`. Uses `Backend/requirements.txt`.

## 3. Findings driving this work

| # | Finding | Risk | Evidence |
| --- | --- | --- | --- |
| F1 | Business services instantiate the Paystack client and parse Paystack payloads directly | Provider coupling; a second provider or an API change touches financial code | `payments/services.py` imports `PaystackClient`; `_validate_charge`, `refresh_transfer_from_provider`, `request_refund` parse provider dicts |
| F2 | Frontend loads Paystack Inline JS from a third-party CDN and depends on `PaystackPop` | Supply-chain/CSP exposure; checkout breaks when CDN blocked; not provider-neutral | `page-dashboard.js::loadPaystack`, `Frontend/js/paystack.js`, response fields `paystack_public_key`, `access_code` |
| F3 | No `PAYMENT_PROVIDER` selection or validation | Provider cannot be substituted; keys validated only for Paystack | `core/settings.py` payment section |
| F4 | Storage is a single Supabase class; no interface, no second adapter, no path-traversal guard on delete/sign | Provider lock-in; object operations trust caller paths | `users/storage.py`, `users/views.py`, purge command |
| F5 | Notifications: single SMS channel, no retry policy, no fallback for transactional messages | OTP-dependent flows fail silently when provider misconfigured (`SMS_BACKEND=none` in prod only warns) | `core/notifications.py`, `users/otp.py` |
| F6 | Two hand-maintained requirement files, no lock file, no hashes | Drift between root and Backend copies; non-reproducible deploys | `requirements.txt` vs `Backend/requirements.txt` (identical today, by convention only) |
| F7 | `PLATFORM_NAME` conflicting defaults: `render.yaml` = **PesaFix**, settings default = **M-Biashara**; loyalty text hardcodes M-Biashara | Brand depends on which environment variable happens to be set | `render.yaml`, `settings.py:406`, `loyalty/views.py:58` |
| F8 | `PWA_CACHE_VERSION` is a hand-edited date string | Stale app shell after a deploy unless someone remembers to bump | `settings.py:433`, sw.js `__CACHE_VERSION__` placeholder |
| F9 | CI lacks: Postgres tests, pip-audit, bandit, coverage gate, collectstatic check, lockfile drift check, JS syntax check | Regressions only caught manually | `.github/workflows/backend-tests.yml` |
| F10 | No local Postgres/MinIO parity environment | Provider adapters can't be exercised against realistic services locally | repo root (no compose file) |
| F11 | No staging service or documented promotion path | Production is the first environment a deploy touches | `render.yaml`, `docs/DEPLOYMENT.md` |
| F12 | 93-finding CSV register not tracked as issues; statuses stale after remediation | Audit trail lives only in a CSV that drifts | `docs/ISSUE_REGISTER.csv` |

Pre-existing test-suite behaviour retained: the suite mocks all provider HTTP;
no test performs real network, charge, transfer or refund operations. This is
asserted again by new contract tests (they patch `requests` at the adapter
boundary).

## 4. Planned changes (summary per phase)

1. **Payments**: typed provider protocol + normalized models in
   `payments/providers/`; Paystack adapter (HTTP moved verbatim where
   possible); `payments/paystack.py` becomes a compatibility re-export;
   `PAYMENT_PROVIDER` env validated at startup; business services depend on
   the interface; webhook signature verification moves into the adapter;
   frontend uses only `authorization_url` (redirect), Paystack JS removed.
2. **Storage**: `core.storage` package with `DocumentStore` protocol, Supabase
   adapter (existing logic ported), S3-compatible adapter (MinIO-testable,
   SigV4 implemented with stdlib + `requests` to avoid boto3 weight);
   `DOCUMENT_STORE` env; strict server-generated path policy; `users/storage.py`
   kept as a delegating shim (existing tests untouched).
3. **Notifications**: provider protocol + SMS adapters, bounded retry for
   transient failures, in-app fallback channel (new `core.InAppNotification`
   model) restricted to non-OTP transactional purposes; OTP resend cooldown
   added; no sensitive content logged.
4. **Dependencies**: `Backend/requirements.in` → hashed
   `Backend/requirements.txt` via pip-tools; root file becomes a `-r`
   include; CI drift check. `PLATFORM_NAME` default aligned to the deployed
   brand (PesaFix); hardcoded brand strings read `settings.PLATFORM_NAME`.
5. **CI**: add collectstatic dry-run, JS syntax check, bandit, pip-audit
   (reporting gate with documented classification), coverage gate from the
   measured baseline, lockfile drift, and a PostgreSQL service-container job.
6. **Compose**: root `docker-compose.yml` (Postgres 15 + MinIO + app),
   Dockerfile, entrypoint, safe `.env` examples. Not executable in this
   sandbox — validated by review and CI where possible.
7. **PWA**: cache version derived from `RENDER_GIT_COMMIT` (build identifier)
   with content-hash fallback; env override preserved.
8. **Staging**: second Render web service with isolated env (test keys,
   separate DB), documented promotion + rollback flow.
9. **Docs/issues**: docs synced to reality; issue-register dedupe + migration
   preview; **no bulk issue creation without explicit approval**.
10. **Final report**: traceability matrix + release assessment.

## 5. Acceptance tests (high level)

- Contract tests: every provider adapter satisfies the protocol (mocked HTTP).
- Financial invariants: duplicate webhook/verify calls, concurrent claim,
  amount/currency/reference mismatch rejection, ledger balance, UNKNOWN
  recovery — existing suite extended where gaps are found.
- Storage: path policy rejects traversal; both adapters pass the same
  contract tests (S3 with mocked transport; MinIO when reachable).
- Notifications: provider failure → bounded retry → failure classification;
  fallback only for allowed purposes; OTP cooldown enforced.
- Config: provider selection fails closed; secrets never logged.
- PWA: version changes with build identifier; placeholder always replaced.
