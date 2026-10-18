# M-Biashara — redesign, PWA and fee repricing report

**Branch:** `arena/f463a275-loan-platform`
**Date:** 17 October 2026
**Status:** implemented in the repository; **not deployed**. Fee collection and
live money movement remain disabled.

This report covers sections A–G requested by the execution prompt. It records
what was built, what was verified, and what remains blocked on decisions outside
engineering. It does not claim browser, Lighthouse, screen-reader or penetration
test results, because none were executed in this environment.

A follow-up pass covering light/dark theming, the section navigation, scroll
reveals, mobile polish and component refinements is documented separately in
`docs/FRONTEND_REFINEMENT_REPORT.md` (235 tests at that point).

---

## A. Scope, constraints and what was preserved

The application was renovated **in place**. No framework, database, API path,
authentication issuer, provider reference or identifier was renamed.

Preserved deliberately:

- Django 5.0.8 + DRF server-rendered architecture and the existing JSON API.
- API routes under `/api/users/`, `/api/loans/`, `/api/payments/` and the
  webhook at `/api/webhooks/paystack/`.
- Database tables, field names and the three audit-remediation migrations.
- The rule that the **server is the source of truth** for money states:
  a 2xx transfer request is not "disbursed", and a browser callback is not
  "paid". Only provider-confirmed state is rendered as final.
- The fail-closed posture: `PAYMENTS_ENABLED=false`, `FEE_COLLECTION_MODE=disabled`,
  `LEGAL_TERMS_APPROVED=false` by default.

Not done, on purpose:

- No licence, registration, CBK approval, ODPC registration or "bank-grade"
  claim is made anywhere in the UI. Where status is unknown the interface says
  "Licence status not yet published".
- No invented repayment terms, interest, APR, penalties or testimonials.
- No live payment, disbursement or fee collection was enabled.

---

## B. Design system and public information architecture

### Design language

A restrained fintech palette replaces the previous ad-hoc styling:

| Token | Value | Use |
| --- | --- | --- |
| `--ink` | `#0B1F33` | Primary text, dark panels, header |
| `--ink-2` / `--ink-3` | `#16324A` / `#24445F` | Secondary text, layered surfaces |
| `--paper` | `#F7F4EE` | Page background (warm white) |
| `--surface` | `#FFFFFF` | Cards and forms |
| `--emerald` | `#0F766E` | Primary action, links, focus |
| `--teal` / `--teal-bright` | `#10B981` / `#2DD4BF` | Positive accents |
| `--danger`, `--warning`, `--info` | semantic pairs | States and banners |

`Backend/Frontend/css/style.css` (2,541 lines) defines typography
(`--text-xs` … `--text-hero`), a spacing scale, three radii, buttons
(primary/secondary/ghost/quiet/danger), form controls, cards, chips, banners,
tables, dialogs, toasts, skeletons and print styles. There are no web-font or CDN
dependencies; the stack is native system fonts, so the app renders without
network access to a font host.

### Homepage information architecture

The public page now answers the prompt's required questions in order:

1. **What is offered** — a hero with the plain-language offering, the amount
   range and an explicit "pilot, no live fees" badge.
2. **How it works** — four numbered steps with a "what this is not" note on each
   (application ≠ approval, offer ≠ disbursement, etc.).
3. **What information is needed** — account, National ID, mobile number and
   consent, with privacy routing.
4. **Approval vs offer vs payment vs disbursement** — a four-step block and a
   dashboard that keeps the states separate.
5. **Fees** — the complete published schedule, computed from the model, not
   typed into the template.
6. **Security and privacy** — controls described as engineering controls, not
   certifications.
7. **FAQ** — native `<details>` elements, readable without JavaScript.
8. **Verified contact/legal** — footer with privacy, terms, licence status
   ("not yet published") and the fee-schedule version.

Navigation is responsive: a desktop row and a mobile panel driven by
`#navToggle`, with `aria-expanded`, Escape-to-close and outside-click handling.
The same header/footer partials are shared by every page, so links cannot drift
between templates.

---

## C. Authentication, registration and the customer dashboard

### Sign-in and password reset

`login.html` and `page-auth.js` implement:

- phone + password sign-in against the existing `/api/users/login/`;
- inline password reset (request code → code + new password) against the
  existing endpoints, including throttling copy for 429 responses;
- an offline guard that refuses to submit while `navigator.onLine === false`;
- field-level errors and a live status region, not `alert()`.

### Registration

`register.html` is a two-step wizard: account details, then review and explicit
consent. It posts to the existing `/api/users/register/` contract (including
`consent_version`), shows the version of the privacy notice and terms being
accepted, and auto-signs-in after success. If auto sign-in fails, it offers a
sign-in link instead of pretending the session exists.

No identity documents are collected on the registration screen. They are
uploaded later from the dashboard, which matches the backend rule that
`has_uploaded_documents` is separate from "verified".

### Dashboard state mapping

The dashboard consumes `/api/loans/active/` as
`{ has_active_loan, loan, latest }` and renders `loan` when present, otherwise
`latest` as a read-only receipt. This fixes the audit's `has_active_loan` vs
`has_loan` confusion: the page never invents a field name, and it does not hide
the application form merely because an old loan exists.

Each state maps to explicit copy:

| Server state | Dashboard behaviour |
| --- | --- |
| `PENDING` | "Submitted / awaiting review"; no accept or pay buttons |
| `APPROVED` | Offer box with principal, fee, expiry and terms version; accept action |
| `DISBURSEMENT_PENDING` | "In progress; not complete until the provider confirms" |
| `DISBURSED` | "Provider confirmed the transfer" + confirmation date |
| `REJECTED` | Rejection reason banner; apply flow available again when eligible |
| `EXPIRED` | Offer-expired copy, no accept button |
| `CANCELLED` / `FAILED` / `REVERSED` | Explicit failed/reversed copy; no success language |
| `REPAID` | Reserved state; rendered as closed |

The apply form is only shown when the profile is verified and consent is
current. Document upload (front, back, selfie) enforces the 5 MB / JPEG-PNG-WebP
limits client-side as a convenience; the server re-validates magic bytes.

The staff queue (`admin/verification_dashboard.html`, `page-admin.js`) keeps the
original server-rendered CSRF-protected POST per card, with a per-card
`data-verify-url`. Approve and reject both require an explicit confirmation;
reject requires a note. Signed document links are rendered by the server and
are not fetched or stored by the service worker.

---

## D. Installable PWA

### Manifest

Served at `/manifest.webmanifest` with `application/manifest+json`:

```json
{
  "id": "/",
  "name": "M-Biashara",
  "short_name": "M-Biashara",
  "start_url": "/dashboard/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "lang": "en-KE",
  "theme_color": "#0B1F33",
  "background_color": "#F7F4EE",
  "icons": [
    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "purpose": "any"},
    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "purpose": "any"},
    {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "purpose": "maskable"}
  ]
}
```

Icons are real PNGs generated by `scripts/generate_icons.py` (a pure-Python
rasteriser, because Pillow is not available in this environment). The same
script produces `apple-touch-icon.png` (180×180) and `favicon-32.png`.
Every page declares the manifest, the touch icon and the Apple standalone
metadata.

### Service worker

`Backend/Frontend/sw.js` is served from the site root so its scope is `/`.
The view injects `PWA_CACHE_VERSION` into the file at request time and sends
`Service-Worker-Allowed: /` plus `no-store`.

Caching rules:

- **Precached:** `/offline/`, the stylesheet, `core.js`, `pwa.js`, `auth.js`,
  `loan.js`, `paystack.js`, `page-auth.js`, `page-dashboard.js`, and three icons.
- **Never cached:** `/api/*` (accounts, loans, payments, uploads), `/admin/*`,
  `/health/*`, `/sw.js`, `/manifest.webmanifest`.
- Only `GET` requests are considered. `POST`/`PUT`/`PATCH`/`DELETE` are never
  intercepted, queued or replayed, so the browser cannot replay a financial
  action from the service worker.
- Navigations are network-first with the precached offline page as fallback.
  HTML is never served from the cache while online.
- Static assets are cache-first with a background refresh.
- Activation deletes older `mb-shell-*` caches.
- A waiting worker does not take over silently: the page shows an update toast
  and sends `SKIP_WAITING` only when the user chooses "Refresh".

### Install and offline UX

- `beforeinstallprompt` is captured and used for a genuine install prompt.
- On iOS (where the event does not fire) the Install button opens a dialog with
  the Safari Share → "Add to Home Screen" instructions. This is a real fallback,
  not a dead button.
- The button hides once the app is running standalone or after installation.
- The offline banner is driven by `online`/`offline` events. Forms refuse to
  submit offline and tell the user why.
- The offline page says that pages already visited may open, but balances,
  applications, uploads and payments cannot be submitted. No balance or loan
  value is ever rendered from cache.

---

## E. Fee schedule (proposed pricing)

The previous schedule had gaps and an uneven shape. It is replaced by a
contiguous six-band schedule starting at KES 100. Pricing is versioned as
`2026-10-draft` and is a **commercial proposal awaiting business approval**, not
a live price.

| Band (KES) | Rate | Basis points | Example |
| --- | --- | --- | --- |
| 100 – 999 | 9.0% | 900 | KES 45 at KES 500 |
| 1,000 – 4,999 | 10.0% | 1000 | KES 499 at KES 4,999 |
| 5,000 – 9,999 | 10.5% | 1050 | KES 1,049 at KES 9,999 |
| 10,000 – 19,999 | 11.0% | 1100 | KES 2,199 at KES 19,999 |
| 20,000 – 34,999 | 11.5% | 1150 | KES 4,024 at KES 34,999 |
| 35,000 – 60,000 | 12.0% | 1200 | KES 7,200 at KES 60,000 |

Computation, in integer arithmetic only:

```text
step = FEE_ROUNDING                       # 1 = whole shillings
fee  = max(45, ((amount × rate_bps) // (10_000 × step)) × step)
```

`FEE_MINIMUM = 45` (KES) and `FEE_ROUNDING = 1` (whole shillings, truncated down).
Examples at band boundaries:

| Amount | Band rate | Fee |
| --- | --- | --- |
| 100 | 9.0% | **45** (minimum applies) |
| 500 | 9.0% | 45 |
| 999 | 9.0% | 89 |
| 1,000 | 10.0% | 100 |
| 4,999 | 10.0% | **499** |
| 9,999 | 10.5% | 1,049 |
| 19,999 | 11.0% | 2,199 |
| 34,999 | 11.5% | 4,024 |
| 60,000 | 12.0% | 7,200 |

> Rounding decision: fees are truncated down to the whole shilling, so
> 4,999 × 10.0% = 499.9 publishes as **KES 499**. The same rule gives 1,049 at
> 9,999, 2,199 at 19,999 and 4,024 at 34,999. The alternative (half-up) was
> rejected by the product owner because it contradicts the published examples.

`Loan.validate_schedule()` is run by the test suite, the Django system check
and `check_config`. It checks:

- the bands start at `MIN_AMOUNT` and end at `MAX_AMOUNT`;
- every band is well-formed and rates are positive;
- there are no gaps or overlaps between bands;
- the rate strictly increases at each band start;
- the absolute fee strictly increases across band boundaries.

`Loan.pricing_rows()` derives the published table from the same bands; the
homepage never hard-codes a fee. The homepage calculator calls
`GET /api/loans/quote/` for each slider position, so the public figure and the
charge come from one function. The table discloses the KES 45 minimum, because
the smallest loans pay a higher effective percentage than the band rate.

Fee status today: published for transparency, **collection disabled**. The
dashboard and homepage state that no fee is collected in this environment. When
`FEE_COLLECTION_MODE=upfront` is approved, the copy switches to
"collected only after your application is approved and you accept the written
offer" — the code path already exists, but the gate is not enabled.

---

## F. Accessibility, responsiveness, performance and SEO

Implemented:

- Skip link, one `<main>`, landmark header/footer/nav, logical heading order.
- Visible `:focus-visible` outlines on links, buttons and inputs.
- Every form field has a label; errors are announced through
  `role="alert"`/`aria-live`; the submit buttons expose `aria-busy`.
- Native dialogs (`<dialog>`) with labelled titles and a close path; Escape
  closes the mobile navigation.
- Status is never conveyed by colour alone: chips include text.
- `prefers-reduced-motion` disables transitions and animations.
- Tables collapse into labelled cards below 760 px (`table--cards`), avoiding
  horizontal scrolling on small phones.
- Touch targets use the shared button sizing; the layout is tested down to
  320 px by construction (single-column grids, fluid type via `clamp()`).
- Language is declared as `en-KE`; number and date formatting uses `en-KE`.

Performance:

- one stylesheet, no CSS framework, no web fonts, no images in the critical
  path except the small brand mark (inline SVG);
- all scripts are `defer`red; page-specific JS is only loaded on pages that need
  it;
- dashboard skeletons render before data arrives;
- icons are 0.3–5.8 KB.

SEO and sharing:

- unique `<title>` and description per page;
- Open Graph and Twitter card metadata in `base.html`;
- `robots.txt` allows the public site and legal pages, disallows `/api/`,
  `/admin/`, `/dashboard/` and `/health/`;
- the manifest and theme colour are declared on every page.

Honest limits: no browser engine, Lighthouse, axe, screen reader or visual
regression tool was available here. The claims above are about the code and the
server-rendered output, not measured scores.

---

## G. Verification, deployment posture and remaining blockers

### Commands run (from `Backend/`)

```bash
python manage.py check --settings=core.test_settings
python manage.py makemigrations --check --dry-run --noinput --settings=core.test_settings
python manage.py test --settings=core.test_settings
python manage.py collectstatic --noinput --settings=core.test_settings
python manage.py check_config --allow-test-mode --settings=core.ci_settings
```

Results:

| Check | Result |
| --- | --- |
| `check` | No issues (2 test-mode warnings silenced) |
| Migration drift | `No changes detected` |
| Test suite | **235 tests, OK** in ~3.8 s (core 60, users 51, loans 52, payments 72) |
| `collectstatic` | 192 files, ~5.4 MB |
| `check_config` | OK, 1 warning: Paystack test keys configured; `payments=on mode=test fees=disabled` |

Server-rendered smoke test (Django test client, `HTTP_HOST=localhost`, fresh
database):

| URL | Status | Bytes |
| --- | --- | --- |
| `/` | 200 | 18,970 |
| `/register/` | 200 | 13,013 |
| `/login/` | 200 | 11,009 |
| `/dashboard/` | 200 | 19,287 |
| `/legal/privacy/` | 200 | 12,415 |
| `/legal/terms/` | 200 | 11,488 |
| `/offline/` | 200 | 7,839 |
| `/manifest.webmanifest` | 200 | 925 |
| `/sw.js` | 200 | 3,583 |
| `/robots.txt` | 200 | 113 |

New tests cover the fee boundaries (including the KES 45 floor and every band
edge), effective-rate monotonicity, pricing-row contiguity, the manifest
identity, the service-worker version substitution and root scope, the private
route exclusions in the worker, the offline page, `robots.txt`, and the
security headers.

### Deployment

**No deployment was performed.** The Render blueprint keeps
`autoDeploy: false`, payments and fee collection false, and every credential as
`sync: false` (entered in the Render dashboard, never committed). Enabling live
payments still requires, at minimum:

- business sign-off on this fee schedule and the customer terms;
- legal sign-off on the privacy notice and terms (currently marked draft);
- a resolved CBK licensing position for the actual product;
- Paystack live-account approval and live keys;
- a private Supabase storage bucket and its service key;
- SMS delivery credentials (the console backend is not production delivery);
- independent security testing and a funded treasury for disbursement.

Until then the technical defaults stay closed, and the UI says so in plain
language.
