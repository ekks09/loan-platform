# GitHub Issues preview — NOT created

**Bulk creation requires explicit user approval**, per the master plan
(phase P9). This file is the exact payload that would be posted.
Approve it (or an edited version), and only then is
`gh issue create` run for these.

Scope: every register row not Resolved — 29 items (8 Open, 17 Partially resolved, 4 External/business).

Suggested labels to pre-create once: `sev:p0`, `sev:p1`, `sev:p2`, `sev:p3`,
`external`. Area labels can be added ad hoc (`payments`, `security`,
`ux`, `ops`, `docs`, `deploy`, `data-protection`, ...).

## [P1-03] No refunds reversals or disputes

labels: `sev:p1`, `payments`

```
**Work done so far.** Refunds/reversals implemented in code; disputes/chargebacks remain a vendor+business process — no webhook handling beyond UNKNOWN.

**Original audit evidence (2026-10 audit).** No refund model or endpoint; transfer.reversed only writes last_event

**Impact.** Consumer harm and unrecoverable money

**Suggested fix.** Refund service, reversal handling, reserves, policy

**Effort.** L | **Area.** Payments | **Register.** docs/ISSUE_REGISTER.csv (P1-03)
```

## [P1-06] Fake email domain and no receipts

labels: `sev:p1`, `payments`

```
**Work done so far.** internal_email() configurable; disbursed SMS + in-app notifications (P3) sent on disbursement — no formal receipt artefact yet.

**Original audit evidence (2026-10 audit).** Internal address user-phone@example.com; no email or SMS receipt

**Impact.** No proof of payment; support burden

**Suggested fix.** Collect real email or send SMS receipts

**Effort.** S | **Area.** Payments | **Register.** docs/ISSUE_REGISTER.csv (P1-06)
```

## [P1-07] Treasury and settlement risk

labels: `sev:p1`, `payments`, `external`

```
**Why this needs tracking.** Treasury/settlement is a finance-function process; code exposes the ledger + reconciliation to support it.

**Original audit evidence (2026-10 audit).** Transfer from balance immediately after collection; no buffer; payout and collection fees not modelled

**Impact.** Failed payouts at launch and negative unit economics

**Suggested fix.** Treasury buffer, T+1 payout, fee model, balance alerts

**Effort.** M | **Area.** Payments | **Register.** docs/ISSUE_REGISTER.csv (P1-07)
```

## [P1-10] JWT in localStorage with no refresh or revocation

labels: `sev:p1`, `security`

```
**Work done so far.** 30-min JWT + strict claims; localStorage retention and no refresh/revocation documented as residual (REMEDIATION §2 Phase 2).

**Original audit evidence (2026-10 audit).** auth.js stores access token in localStorage; 30-minute TTL; no refresh or logout-all

**Impact.** XSS token theft; session expiry mid-flow

**Suggested fix.** httpOnly cookies or refresh rotation with token versioning

**Effort.** M | **Area.** Security | **Register.** docs/ISSUE_REGISTER.csv (P1-10)
```

## [P1-14] Phone ownership never verified

labels: `sev:p1`, `security`

```
**Current position.** Registration still does not verify phone ownership. P3's NotificationProvider + in-app fallback are the enabling pieces; product must accept OTP signup friction.

**Original audit evidence (2026-10 audit).** Registration requires no OTP; phone is the unique identity

**Impact.** Pre-hijacking and SIM-swap impersonation

**Suggested fix.** OTP at signup and before payout-number changes

**Effort.** M | **Area.** Security | **Register.** docs/ISSUE_REGISTER.csv (P1-14)
```

## [P1-15] Admin exposed without 2FA or throttling

labels: `sev:p1`, `security`

```
**Work done so far.** Scoped throttles incl. admin endpoints; staff 2FA not implemented (REMEDIATION residual).

**Original audit evidence (2026-10 audit).** /admin/ public; no 2FA, IP restriction or login throttling

**Impact.** Staff takeover equals full data breach

**Suggested fix.** 2FA, allow-list, separate admin host, alerts

**Effort.** M | **Area.** Security | **Register.** docs/ISSUE_REGISTER.csv (P1-15)
```

## [P1-21] No liveness or face match

labels: `sev:p1`, `security`

```
**Current position.** No liveness/face-match. Product/legal decision; document scans are validated as real images only.

**Original audit evidence (2026-10 audit).** Selfie is a static upload; staff eyeball three images

**Impact.** Identity spoofing

**Suggested fix.** Liveness vendor or video verification

**Effort.** L | **Area.** Security | **Register.** docs/ISSUE_REGISTER.csv (P1-21)
```

## [P1-37] No monitoring backups or health checks

labels: `sev:p1`, `ops`

```
**Work done so far.** health/readiness endpoints + request IDs exist; monitoring/alerting and backup SCHEDULING are operational (see RELEASE_PROCESS tier-3).

**Original audit evidence (2026-10 audit).** No Sentry, metrics, alerts, /healthz or backup plan

**Impact.** Failures found by customers; data loss risk

**Suggested fix.** Observability stack, health check, PITR and restore drill

**Effort.** M | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P1-37)
```

## [P1-38] Free-tier reliability

labels: `sev:p1`, `ops`, `external`

```
**Why this needs tracking.** Free-tier reliability is a hosting plan decision; nothing in code can change it.

**Original audit evidence (2026-10 audit).** Render free sleeps; Supabase free may pause; no SLA

**Impact.** Cold-start webhook timeouts and availability risk

**Suggested fix.** Paid tier or alternative; keep-warm; backups

**Effort.** S | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P1-38)
```

## [P1-41] HEIC rejected and uploads uncompressed

labels: `sev:p1`, `frontend`

```
**Work done so far.** Server-side image validation accepts jpg/png/webp; HEIC and in-browser compression remain frontend work.

**Original audit evidence (2026-10 audit).** accept list excludes HEIC; 5 MB originals uploaded

**Impact.** iPhone users blocked; costly mobile data

**Suggested fix.** Accept/convert HEIC and resize client-side

**Effort.** M | **Area.** Frontend | **Register.** docs/ISSUE_REGISTER.csv (P1-41)
```

## [P1-42] No support channel

labels: `sev:p1`, `ops`, `external`

```
**Why this needs tracking.** Support channel is a business decision; SUPPORT_PHONE/EMAIL env hooks exist.

**Original audit evidence (2026-10 audit).** No contact page, WhatsApp, phone or complaint process

**Impact.** Consumers have nowhere to go

**Suggested fix.** Support channel and complaint SLA

**Effort.** S | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P1-42)
```

## [P2-03] BASE_DIR assumes repository layout

labels: `sev:p2`, `code-quality`

```
**Current position.** BASE_DIR still path-relative; acceptable while build contexts (Docker/Render) pin cwd; low value.

**Original audit evidence (2026-10 audit).** Templates and static depend on BASE_DIR/Backend/Frontend

**Impact.** Breaks when backend is containerised or relocated

**Suggested fix.** Set explicit paths via env/app config

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P2-03)
```

## [P2-04] Static pipeline dated and duplicative

labels: `sev:p2`, `code-quality`

```
**Work done so far.** collectstatic is CI-gated and P7 made cache invalidation deterministic; no bundler/manifest storage by design.

**Original audit evidence (2026-10 audit).** STATICFILES_STORAGE deprecated; HTML copied into staticfiles; no manifest hashing

**Impact.** Poor caching and duplication

**Suggested fix.** STORAGES with manifest storage; separate assets

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P2-04)
```

## [P2-08] Dead code

labels: `sev:p2`, `code-quality`

```
**Work done so far.** Deprecated facades users/storage.py + payments/paystack.py kept deliberately for one release (documented); fine to delete next cycle.

**Original audit evidence (2026-10 audit).** CurrentLoanView unused; unused CSS and imports

**Impact.** Maintenance noise

**Suggested fix.** Remove dead code

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P2-08)
```

## [P2-09] No API versioning or docs

labels: `sev:p2`, `code-quality`

```
**Work done so far.** /api/v1 default + docs/API.md; formal OpenAPI spec not produced.

**Original audit evidence (2026-10 audit).** Paths are /api/... with no version or schema

**Impact.** Hard to integrate and test

**Suggested fix.** /api/v1 and OpenAPI docs

**Effort.** M | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P2-09)
```

## [P2-18] Heavy animations and effects

labels: `sev:p2`, `ux`

```
**Work done so far.** Redesign reduced effects; prefers-reduced-motion handling in CSS (verify during frontend polish).

**Original audit evidence (2026-10 audit).** Fixed blurred orbs, grid overlay, backdrop filters and multiple animations

**Impact.** Battery and performance cost on low-end devices

**Suggested fix.** Reduce effects; respect prefers-reduced-motion

**Effort.** S | **Area.** UX | **Register.** docs/ISSUE_REGISTER.csv (P2-18)
```

## [P2-19] Accessibility gaps

labels: `sev:p2`, `ux`

```
**Work done so far.** aria labels + password toggles + semantic nav in redesign; no axe audit yet.

**Original audit evidence (2026-10 audit).** Modals lack focus trap and aria-modal; icon buttons unlabeled; emoji icons

**Impact.** Not usable with assistive tech

**Suggested fix.** ARIA, focus management, real icons, contrast audit

**Effort.** M | **Area.** UX | **Register.** docs/ISSUE_REGISTER.csv (P2-19)
```

## [P2-20] No SEO analytics or funnel instrumentation

labels: `sev:p2`, `ux`

```
**Work done so far.** SEO: manifest/robots/og tags exist; analytics intentionally none (privacy posture) — decide as product call.

**Original audit evidence (2026-10 audit).** No meta/OG tags, sitemap, robots, analytics

**Impact.** Cannot measure or acquire

**Suggested fix.** Add metadata and privacy-safe analytics

**Effort.** S | **Area.** UX | **Register.** docs/ISSUE_REGISTER.csv (P2-20)
```

## [P2-21] No localisation

labels: `sev:p2`, `ux`

```
**Current position.** Single-language (EN) UI remains.

**Original audit evidence (2026-10 audit).** English only

**Impact.** Limits reach in Kenya

**Suggested fix.** i18n with Swahili

**Effort.** M | **Area.** UX | **Register.** docs/ISSUE_REGISTER.csv (P2-21)
```

## [P2-25] No data-subject rights process

labels: `sev:p2`, `data-protection`

```
**Work done so far.** Consent versions, withdrawal-aware checks, retention purge command + audit; formal DSAR runbook not written.

**Original audit evidence (2026-10 audit).** No export, correction or delete endpoint

**Impact.** DPA non-compliance

**Suggested fix.** DSAR workflow and SLAs

**Effort.** M | **Area.** Data Protection | **Register.** docs/ISSUE_REGISTER.csv (P2-25)
```

## [P2-26] No vendor DPAs or region confirmation

labels: `sev:p2`, `ops`, `external`

```
**Why this needs tracking.** Vendor DPAs are contractual; code lists data flows (DEPLOYMENT/CREDIT_PRODUCT_SPEC) to support them.

**Original audit evidence (2026-10 audit).** No documentation for Paystack, Supabase or Render data handling

**Impact.** Cross-border and processor risk

**Suggested fix.** DPAs, region review, subprocessor register

**Effort.** S | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P2-26)
```

## [P2-27] No dependency or secret scanning

labels: `sev:p2`, `ops`

```
**Work done so far.** pip-audit gate with fail-closed allowlist (P5). Secret scanning: boot-time rejection + repo hygiene; a gitleaks CI step is still missing.

**Original audit evidence (2026-10 audit).** No Dependabot, pip-audit or gitleaks

**Impact.** Vulnerable or leaked dependencies

**Suggested fix.** Add scanners to CI

**Effort.** S | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P2-27)
```

## [P2-29] No status page or incident comms

labels: `sev:p2`, `ops`

```
**Current position.** No status page/incident comms plan.

**Original audit evidence (2026-10 audit).** No status page or customer communication plan

**Impact.** Outages become trust incidents

**Suggested fix.** Status page and templates

**Effort.** S | **Area.** Ops | **Register.** docs/ISSUE_REGISTER.csv (P2-29)
```

## [P3-01] Minor dead code and debug comments

labels: `sev:p3`, `code-quality`

```
**Work done so far.** Facade shims noted (P2-08); no taste-pass yet.

**Original audit evidence (2026-10 audit).** Unused imports; numbered emoji comments; FIX breadcrumbs

**Impact.** Noise

**Suggested fix.** Cleanup pass

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P3-01)
```

## [P3-02] Magic strings and inconsistent casing

labels: `sev:p3`, `code-quality`

```
**Work done so far.** Notification purposes/statuses centralized in core.notifications.base; rest unswept.

**Original audit evidence (2026-10 audit).** Status strings repeated; PENDING vs pending across layers

**Impact.** Bugs when values change

**Suggested fix.** Central enums and constants

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P3-02)
```

## [P3-03] No lint or type configuration

labels: `sev:p3`, `code-quality`

```
**Current position.** No ruff/black/mypy config. Bandit+coverage gates exist; formatting/typing config proposed in this phase's issues preview.

**Original audit evidence (2026-10 audit).** No ruff/black/mypy config

**Impact.** Inconsistent style and missed errors

**Suggested fix.** Add ruff, black, mypy

**Effort.** S | **Area.** Code Quality | **Register.** docs/ISSUE_REGISTER.csv (P3-03)
```

## [P3-05] No licence

labels: `sev:p3`, `docs`

```
**Current position.** No LICENSE file — legal decision required.

**Original audit evidence (2026-10 audit).** No LICENSE file

**Impact.** Legal ambiguity for contributors

**Suggested fix.** Choose and add a licence

**Effort.** S | **Area.** Docs | **Register.** docs/ISSUE_REGISTER.csv (P3-05)
```

## [P3-06] Generic backend error messages

labels: `sev:p3`, `ux`

```
**Work done so far.** Error catalogue still thin; envelope carries message/code; UX copy pass pending.

**Original audit evidence (2026-10 audit).** Failed to create loan; Server error

**Impact.** Users cannot act on errors

**Suggested fix.** Error catalogue with actionable messages

**Effort.** S | **Area.** UX | **Register.** docs/ISSUE_REGISTER.csv (P3-06)
```

## [P3-08] No changelog releases or tags

labels: `sev:p3`, `docs`

```
**Current position.** Versioned releases/tags not started; traceability index (final phase) names the git history.

**Original audit evidence (2026-10 audit).** Single grafted commit named Update register.html

**Impact.** No traceability

**Suggested fix.** Semantic versioning and release notes

**Effort.** S | **Area.** Docs | **Register.** docs/ISSUE_REGISTER.csv (P3-08)
```
