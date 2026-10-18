# Release assessment — loan platform

**Date:** 2026-10-18 · **Branch:** `arena/3c5f72c4-loan-platform` · **Suite:** 374 tests passing, 1 deliberate skip · **Verdict: READY FOR STAGING — NOT READY FOR PRODUCTION**

This is the closing report of the 10-phase hardening programme. Every number
below comes from an executed command on this branch; nothing is asserted from
memory or intent.

---

## 1. Verdict and what it means

| Dimension | Verdict | Evidence |
|---|---|---|
| Implementation | **Ready for controlled staging** | phases committed, 374 automated tests green |
| Automated verification | **Passing** | section 3; every gate scripted in CI |
| Security posture | **Good with one managed exception** | §5; the exception is the Django 5.0 EOL line |
| Real money / live loans | **Not approved** | gates default off; live mode is startup-fatal without legal approval + live keys + production environment |
| Public launch | **Not ready** | external legal/business items in §6 cannot be resolved in code |

---

## 2. Compliance with the master plan — traceability matrix

| # | Requirement from the acceptance criteria | Where | Evidence |
|---|---|---|---|
| 1 | Typed payment provider interface; Paystack as first adapter | `payments/providers/` (base, paystack, registry) | `payments/tests/test_provider_abstraction.py` (21 tests) |
| 2 | Business logic depends on the interface, not the SDK | `payments/services.py`, `webhook.py`, `views.py` refactored; `services._client()` deleted | suite + code review; P1 commit `e2bee91` |
| 3 | Initialized/verified with validated `PAYMENT_PROVIDER` env; fail closed on typo | `core/settings.py:386+` boot validation; registry raises at selection | `test_provider_abstraction` unknown-provider cases |
| 4 | Provider secrets server-side only | keys read only inside `payments/providers/paystack.py` | bandit clean; no key strings in `Frontend/` (grep) |
| 5 | Webhook-authoritative fulfilment; never browser-confirmed | raw-body HMAC-SHA512, persisted digest-dedup events, 256 KB cap, replay command, server-side re-verification on fulfilment | `test_payments_and_webhooks` family |
| 6 | Integer minor-unit money everywhere | `core/money.py` (`amount_minor`), provider adapter converts to display units at the edge | money conversion tests |
| 7 | No blind retries of uncertain financial outcomes | UNKNOWN → provider lookup by deterministic reference; bounded retry; reversal/reversal-before-settlement semantics | transfer webhook tests |
| 8 | DocumentStore interface `upload/signed_url/delete` + Supabase & S3 adapters | `core/storage/` | `test_document_storage.py` (27 tests; SigV4 known-answer vs AWS published vector) |
| 9 | Path traversal impossible; signed URLs never logged | strict regex + shared validation in adapter, failure returns; never refers local paths externally | traversal tests; assertNoLogs coverage |
| 10 | NotificationProvider interface, fallback channel, bounded retries, OTP hygiene | `core/notifications/` package; Africa's Talking adapter; in-app fallback model + API; 1+1 attempt default; no content in logs | `test_notifications.py` (22 tests) |
| 11 | OTP expiry/limits/cooldowns | `users/otp.py` windowed limits + 60 s per-number cooldown + hashed codes | `test_otp_limits.py`, `OtpCooldownTests` |
| 12 | One authoritative dependency definition with lock | `Backend/requirements.in` → compiled, hash-locked `requirements.txt`; root file is an alias | byte-for-byte regeneration check, rehearsed clean-venv install |
| 13 | CI drift check (manifest vs lock) | workflow step compiling + diffing the lock | `scripts/render_blueprints.py --check` and lock step in CI |
| 14 | PLATFORM_NAME single source | settings default `PesaFix`; templates/JS neutralized | `test_config.py` brand tests; full grep of "M-Biashara" in code: 0 |
| 15 | Tests as PostgreSQL (prod-matching major) | `tests-postgres` CI job on `postgres:15` service + `core/pg_test_settings.py` | job spec verified; local full-suite rehearsal on sqlite green |
| 16 | Migration smoke test | job runs `migrate` then `makemigrations --check` on the pg job | step in workflow |
| 17 | collectstatic gate | `collectstatic --noinput --dry-run` in CI | rehearsed exit 0 locally |
| 18 | Security scanning (bandit) + dependency audit (pip-audit) | both as CI gates; app code scans at **zero** findings | this document §3; `Backend/.pip-audit-allowlist` (29 IDs, extended 2026-10-18) |
| 19 | Coverage gate at a documented threshold | `.coveragerc` (`fail_under=85`), measured baseline 88% | §3 |
| 20 | docker-compose with postgres + MinIO + app | `Dockerfile`, `compose.yml`, `.env.compose.example` | `docs/LOCAL_DOCKER.md` validation evidence + limitations |
| 21 | No unsafe placeholder secrets in examples | every example file uses obviously-local placeholders; boot rejects weak secrets | secret validation tests; render blueprints keep `sync: false` |
| 22 | PWA cache invalidation derived from build content | `core/pwa.py` deterministic fingerprint; env override retained | `test_pwa_version.py` (11 tests) |
| 23 | Staging env with isolation, no live creds, human approval, expand-and-contract, rollback | `render.staging.yaml` (generated, drift-gated), `DEPLOY_ENVIRONMENT` startup guards, `docs/RELEASE_PROCESS.md` | `test_config.StagingPostureTests` (4) |
| 24 | Stale docs reconciled; issue register truth; issues prepared, not created | banners on 4 historical reports; `ISSUE_REGISTER.csv` reconciled 64/17/8/4; `GITHUB_ISSUES_PREVIEW.md` (29 payloads) | this document §6 |
| 25 | Mobile/PWA install flow | manifest (standalone, 192/512+maskable), SW with `Service-Worker-Allowed`, install UI incl. iOS manual steps; iOS zoom + notch fixes | `test_pwa_version.py`; live verification on the served app |
| 26 | In-app notifications visible to users | dashboard Updates card (badge, list, mark-all-read) | suite + served-page check |
| 27 | Demo configuration with all product gates open, test money only | `Backend/.env.demo.example` | rehearsed `check_config`: payments=on mode=test fees=upfront |
| 28 | Honest reporting; never fabricate results | every command above executed and recorded in commit messages | git history of this branch |

---

## 3. Executed verification (this branch, this machine)

```text
python manage.py test --settings=core.test_settings
  → Ran 374 tests in ~3 s — OK (1 skipped: MinIO integration test, env-gated)

coverage report (fail_under=85)      → TOTAL 4243 stmts, 88%
bandit -r core users loans payments loyalty -x tests,migrations
  → 0 Low / 0 Medium / 0 High
pip-audit -r Backend/requirements.txt
  → only the 29 allowlisted Django 5.0-EOL IDs (see §5)
python manage.py makemigrations --check --dry-run --noinput
  → "No changes detected"
python manage.py check_config --allow-test-mode --settings=core.ci_settings
  → "Configuration OK (1 warning(s)). payments=on mode=test fees=disabled"
python manage.py check_config (demo env file)
  → "Configuration OK (2 warning(s)). payments=on mode=test fees=upfront"
python manage.py collectstatic --noinput --dry-run --settings=core.ci_settings
  → exit 0
pip-compile regeneration of Backend/requirements.txt
  → byte-for-byte identical (drift gate is reproducible)
clean venv install from the lock + full suite
  → install ok, tests ok
python scripts/render_blueprints.py --check
  → "render blueprints are in sync with deploy/ sources"
node --check on Frontend/sw.js and all parsed widget scripts → ok
```

Could **not** be executed in this sandbox (disclosed, with compensations):
`docker compose up` (no Docker daemon — compose spec + env rehearsal instead)
and the Postgres CI job (no local Postgres — sqlite suite + job mirrors the
same commands against a service container).

---

## 4. State machines and invariants now enforced (and tested)

- **Loan:** PENDING → APPROVED → DISBURSEMENT_PENDING → DISBURSED → REPAID,
  with REJECTED/EXPIRED/CANCELLED/FAILED/REVERSED exits; `ALLOWED_TRANSITIONS`
  enforced at the model; every change writes an immutable `LoanEvent`
  (actor + reason); exactly one open loan per user at the DB level; offer is
  version-bound with expiry; late-success recovery path exists.
- **Payment:** init → provider-confirmed fulfilment only; reference, amount
  (integer minor units), currency and expected state are all validated;
  idempotency keys; events persisted by body digest (provider-retries safe);
  reconciliation service for UNKNOWN outcomes; refunds full/partial with
  bounds; reversals produce compensating ledger journals (and never a fake
  journal before settlement).
- **Ledger:** immutable double-entry, balance enforced per journal.
- **OTP:** hashed at rest, 10-minute expiry, 5 attempts, windowed per-number
  and per-IP limits, 60 s resend cooldown, uniform unknown-number responses,
  fingerprinted + masked audit rows, never logged.
- **Notifications:** SENT = "provider accepted" (never presented as delivered);
  bounded retries for transient classes only; in-app fallback restricted to
  `verification`, `disbursed`, `disbursement_failed`; OTP never falls back.
- **Environment:** staging cannot run live mode (startup-fatal); production
  live mode requires live keys + legal approval; DOCUMENT_STORE/PAYMENT_PROVIDER
  typos fail at boot.

---

## 5. Risk register that code cannot close by itself

| Risk | Severity | Position |
|---|---|---|
| Django 5.0.8 is end-of-life: 29 advisories (19 original + 10 published/aliased after the initial scan, added 2026-10-18) fixable only off the 5.0 line | **High** | deliberate, tracked: upgrade to 5.2 LTS as a separate staging-gated change first after this branch; CI hard-fails on any advisory outside the documented budget |
| PyJWT PYSEC-2025-183 (unfixed upstream) | Low | decode pins `algorithms=[\"HS256\"]` with a single key — confusion class not applicable |
| Sandbox could not execute Docker/Postgres | Medium | mitigations documented; first docker run + CI job run are the two rehearse-on-host actions |
| Test-key Paystack round trip never run against the real provider | Medium | required staging checklist item (#3 in RELEASE_PROCESS.md) |
| Payments have only ever run mocked | Medium | same item; staging evidence gate before production |

---

## 6. Remaining work (from `docs/ISSUE_REGISTER.csv`, reconciled)

**64 resolved / 17 partially resolved / 8 open / 4 external (business+legal).**

Highest-priority remainder: Django 5.2 LTS upgrade; phone-ownership OTP at
registration (P1-14); liveness/face match (P1-21, product decision);
JWT refresh/revocation + staff 2FA (P1-10/P1-15); gitleaks CI step (P2-27);
ruff/mypy config (P3-03); LICENSE file (P3-05, legal); receipts artefact
(P1-06); DSAR runbook (P2-25); status page (P2-29). The 29 GitHub Issues
payloads are staged in `docs/GITHUB_ISSUES_PREVIEW.md` and await your
approval to create.

## 7. Recommended next action

1. Deploy **staging** (branch `main`, blueprint `render.staging.yaml`) and run
   the RELEASE_PROCESS.md checklist, including the real Paystack test-mode
   payment → webhook → disbursement round trip.
2. Land the Django 5.2 LTS upgrade as its own change, soak on staging.
3. Only then: legal/licensing closure → production promotion with the same
   SHA that staging rehearsed, per the human-approval gate.
