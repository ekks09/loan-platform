# Resolution matrix

Disposition of every finding in `docs/ISSUE_REGISTER.csv` after the remediation
branch. Status meanings:

| Status | Meaning |
| --- | --- |
| **Resolved** | Implemented and covered by the automated suite (where testable). |
| **Partial** | Material improvement shipped; a named residual remains. |
| **Blocked** | Requires an external decision, credential, contract or approval. |
| **Deferred** | Deliberately outside this remediation; tracked for a later phase. |

The test evidence column refers to the suite in `Backend/*/tests/` (237 tests after the obsidian-and-gold pass; 235 after the earlier refinement; 224 at remediation).
The `Status` column in `docs/ISSUE_REGISTER.csv` is retained as the audit
baseline (every row was `Open` when it was written). **This document is the
authoritative disposition.**

External actions are listed again in `docs/REMEDIATION_REPORT.md`.

## P0 — critical

| ID | Status | Resolution / evidence |
| --- | --- | --- |
| P0-01 | Resolved | Migration packages and hand-written `0002`/`0003` chains; fresh DB creates 23 tables; `makemigrations --check` clean. Test: `core.tests.test_health_and_pages.SystemCheckTests`. |
| P0-02 | Resolved | Webhook routed, raw-body HMAC-SHA512 with the secret key, persisted events, charge/transfer/refund handling, replay command. Tests: `WebhookTests`, `TransferWebhookTests`. |
| P0-03 | Resolved | Hosts/CSRF/CORS from env; `RENDER_EXTERNAL_HOSTNAME` fallback; pages call relative `/api`; no frontend API host constant. |
| P0-04 | Resolved | `render.yaml` uses `generateValue`/`sync:false`; boot rejects placeholders and short keys. Tests: `SecretValidationTests`. Deployment action: rotate any key previously exposed. |
| P0-05 | Resolved | Registration accepts optional documents, multipart upload, staging UUID paths and a clear storage-unavailable fallback; dashboard supports later upload. Tests: registration and document API tests. |
| P0-06 | Resolved | Contiguous 100–60,000 six-band schedule (KES 45 minimum), explicit `InvalidLoanAmount`, no silent default; boundary tests. |
| P0-07 | Resolved | `/api/loans/active/` returns `has_active_loan`, `loan`, `latest`, `status_label`; dashboard consumes it. Tests: `ActiveLoanContractTests`, page-contract test. |
| P0-08 | Resolved (code) | Paths only in Postgres, private bucket, 300 s signed URLs, consent records, staff access log. External: bucket policy/DPA/region confirmation. |
| P0-09 | Resolved (code) | Fee only after approval **and** accepted offer; collection disabled by default; refunds and reversal entries implemented. External: pricing/legal approval. |
| P0-10 | Resolved | Accepted request is `PENDING_PROVIDER`; `SUCCESS` only on confirmation; retries reconcile first; max-attempt and late-success recovery. Tests: `DisbursementTests`. |
| P0-11 | Resolved | No licence claim in UI or config; empty licence fields; terms/privacy marked draft. Test: public page tests. |

## P1 — high

| ID | Status | Resolution / evidence |
| --- | --- | --- |
| P1-01 | Resolved | Retry service/command, exponential backoff field, `UNKNOWN` reconciliation, reversal, recovery from `FAILED`. |
| P1-02 | Resolved | `run_reconciliation`, ops API, command and history model; dry-run default; tests. |
| P1-03 | Partial | Refunds, reversals and compensating ledger entries are implemented and tested. Chargeback/dispute workflow is not; handle operationally until approved. |
| P1-04 | Resolved | Status, reference, currency and exact amount validated before fulfilment; tests. |
| P1-05 | Resolved | Unique `idempotency_key`/reference per payment, transfer, refund and webhook digest; repeat-call tests. |
| P1-06 | Partial | Provider email uses configurable `INTERNAL_EMAIL_DOMAIN`; SMS receipts sent on disbursement/failure. Formal PDF/email receipts are not implemented. |
| P1-07 | Blocked | Treasury/settlement policy is a finance decision. Ledger and reconciliation provide the technical controls. |
| P1-08 | Resolved | Immutable double-entry ledger, balanced-journal enforcement, compensating entries; tests. |
| P1-09 | Resolved | KES only, whole-shilling integers, explicit minor-unit conversion; tests. |
| P1-10 | Partial | 30-minute JWT, strict issuer/audience/type/expiry validation, no server-side revocation list or refresh flow. Documented residual. |
| P1-11 | Resolved (code) | Scoped throttles on login/register/reset/payments/documents/quote. Multi-instance deployments need a shared cache; documented. |
| P1-12 | Resolved | Generic login and reset responses with a dummy hash for timing; tests. |
| P1-13 | Resolved | Hashed single-use reset codes, 10-minute TTL, attempt limit, Django password validators; tests. |
| P1-14 | Blocked | No SMS OTP ownership check. Requires an approved SMS vendor, template and product decision. |
| P1-15 | Partial | Staff-only queue, Django admin permissions and document access logs. Two-factor authentication is **not** implemented and is a launch blocker. |
| P1-16 | Resolved | Debug endpoint removed; health responses expose only status/environment. |
| P1-17 | Resolved | Phones masked in logs/model `__str__`; provider bodies not logged; no national ID in routine logs. |
| P1-18 | Partial | Paystack script is loaded only when the user starts a payment. No SRI pin or CSP yet; CSP is tracked as residual hardening. |
| P1-19 | Resolved | Unique constraint on `national_id`; duplicate registration test. |
| P1-20 | Resolved | Apply requires consent + documents + verification; disbursement re-checks verification. Tests. |
| P1-21 | Blocked | Liveness/face-match is a vendor and biometric-data decision; manual review remains. |
| P1-22 | Resolved | Foreign keys with `PROTECT` for users/loans/payments; no integer IDs as relationships. |
| P1-23 | Resolved | Explicit state machines and append-only events; `REVERSED` added for provider reversals; illegal transitions tested. |
| P1-24 | Resolved | Partial unique constraint `loans_one_open_per_user` plus atomic create; race test via constraint behaviour. |
| P1-25 | Resolved | One open application per user, enforced in service and database. |
| P1-26 | Resolved | Validation on create/save boundaries; no `full_clean` on every update; tests. |
| P1-27 | Resolved | Single `users.phone` normalizer used by model, API, loans and tests. |
| P1-28 | Resolved | Migration chain and models aligned; drift check in tests and CI. |
| P1-29 | Resolved | Payment reference is unique and non-null; idempotency key prevents duplicate creation. |
| P1-30 | Resolved | Loan/payment/transfer events, webhook events, consent and document-access logs; immutability tests. |
| P1-31 | Resolved | All core models registered in Django admin with appropriate read-only fields/actions. |
| P1-32 | Partial | Queue is paginated, filterable, searchable and audits each document view. Bulk actions and very large queues are not load-tested. |
| P1-33 | Resolved | Rejection stores notes, resets on re-upload, and allows a new application after a closed loan. |
| P1-34 | Resolved (code) | SMS abstraction with console/ Africa's Talking/none; sends deferred to commit. External: sender ID and vendor approval. |
| P1-35 | Resolved | 231 automated tests plus `.github/workflows/backend-tests.yml`. No browser/E2E or load tests. |
| P1-36 | Resolved | README, `.gitignore`, `.env.example`, Windows launcher and Unix quick start. |
| P1-37 | Partial | Liveness/readiness endpoints and alert guidance. External monitoring, scheduled jobs and backup automation must be configured in the hosting account. |
| P1-38 | Blocked | Free-tier reliability/plan choice is an owner decision; the blueprint uses `plan: free`. |
| P1-39 | Resolved | Migrations run in the start command; build only installs and collects static. |
| P1-40 | Resolved | Camera capture (getUserMedia) and file upload both submit through the same multipart API. |
| P1-41 | Partial | HEIC is rejected with a clear message; no client-side compression. |
| P1-42 | Partial | Support phone/email fields render where configured; no ticketing/SLA integration. |

## P2 — medium

| ID | Status | Resolution / evidence |
| --- | --- | --- |
| P2-01 | Resolved | Dead configuration removed; `REDIS_URL` without a backend now fails fast; settings are consumed. |
| P2-02 | Resolved | All dependencies pinned in `Backend/requirements.txt` (and mirror root file). |
| P2-03 | Resolved | `BASE_DIR` anchored to `Backend/`; deploy commands do not assume the repo root. |
| P2-04 | Resolved | WhiteNoise + `collectstatic`; no duplicate frontend server. |
| P2-05 | Resolved | `.gitignore` added (secrets, DBs, static, caches). |
| P2-06 | Resolved | Uniform `{"error":{code,message,details,request_id}}` envelope; tests. |
| P2-07 | Resolved | Views raise domain errors; ad-hoc response dicts removed from touched endpoints. |
| P2-08 | Partial | Dead code in rewritten modules removed; a few legacy compatibility fields remain intentionally and are documented. |
| P2-09 | Deferred | No URL versioning or OpenAPI schema. Add before third-party integrations. |
| P2-10 | Deferred | Copy is consistent on rebuilt pages; logo/brand asset unchanged. |
| P2-11 | Deferred | New components reuse existing tokens; a full visual redesign was intentionally out of scope. |
| P2-12 | Resolved | Full fee schedule and how-it-works are public before signup. |
| P2-13 | Resolved | Free amount entry with live quote across 1,000–60,000. |
| P2-14 | Resolved | Fee shown in quote, offer, acceptance and payment steps. |
| P2-15 | Resolved | Explicit offer acceptance precedes any payment; payment itself is a separate confirmed action. |
| P2-16 | Resolved | Registration requires password confirmation. |
| P2-17 | Deferred | Logo remains ~1.4 MB; compress before launch. |
| P2-18 | Partial | New components are restrained; the base theme still carries heavy glow/float animations. |
| P2-19 | Partial | Labels, `aria-live`, focus styles and semantic tables added; no formal WCAG audit. |
| P2-20 | Deferred | No analytics or funnel instrumentation (deliberate until a privacy basis exists). |
| P2-21 | Deferred | English-only; localisation is Phase 3. |
| P2-22 | Resolved | Blueprint documents and defaults to a staging posture with payments/legal off. |
| P2-23 | Resolved | Payment, fee, legal and storage feature flags; unsafe combinations fail at boot. |
| P2-24 | Partial | HSTS is off by default and opt-in; CSP is not configured. |
| P2-25 | Partial | `deletion_requested_at` and privacy contact exist; no automated DSAR/erasure workflow. |
| P2-26 | Blocked | DPAs, sub-processor list and region confirmation are legal/procurement tasks. |
| P2-27 | Partial | Dependencies are pinned; no automated vulnerability/secret scanner in CI. |
| P2-28 | Partial | Backup/restore runbook documented; Supabase backup/PITR configuration is account-level and not yet evidenced. |
| P2-29 | Deferred | No status page or incident-comms channel. |
| P2-30 | Resolved | Verification queue is integrated with staff auth and document access audit. |

## P3 — low

| ID | Status | Resolution / evidence |
| --- | --- | --- |
| P3-01 | Partial | Debug comments/dead code removed in rewritten modules; a full sweep was not performed. |
| P3-02 | Partial | Status/currency constants centralised in enums and settings; some display strings remain. |
| P3-03 | Deferred | No ruff/mypy configuration or type gate yet. |
| P3-04 | Resolved | Windows launcher rewritten; Unix commands documented in README. |
| P3-05 | Blocked | Repository licence is a legal/business choice. |
| P3-06 | Resolved | Domain errors return actionable messages; unexpected errors stay generic with a request id. |
| P3-07 | Partial | Upload tiles still use emoji; no icon set. |
| P3-08 | Deferred | No changelog/release tags. |
| P3-09 | Resolved | Queue counts and filters now use the same document requirement; staff-only accounts no longer appear as review rows. |
| P3-10 | Deferred | No offline/low-data mode. |

## Summary

| Status | Count |
| --- | --- |
| Resolved | 58 |
| Partial | 19 |
| Blocked | 6 |
| Deferred | 10 |
| **Total** | **93** |
