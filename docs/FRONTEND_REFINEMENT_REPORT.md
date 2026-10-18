# M-Biashara — frontend refinement report

Scope: refine the existing M-Biashara frontend in place. No backend business
rule, loan calculation, payment path, authentication rule or database schema
was changed. Backend-adjacent edits are limited to PWA settings, a context-
passing 500 handler (so the error page keeps its shell) and regression tests;
section K records the obsidian-and-gold pass that followed.

This document records what was found, what was changed, what was actually
executed, and what remains unverified.

---

## A. Inspection findings

| Area | Finding | Disposition |
| --- | --- | --- |
| Theming | One light palette only; page background and text were fixed values | Added a semantic token layer and a separately designed dark palette |
| Colour usage | ~30 hardcoded hex/rgba values in components and templates | Replaced with semantic tokens; the remaining literal values sit inside the two palette blocks or fixed-print rules |
| Header | Sticky header with nav, but no appearance control and no contact anchor | Added a single theme switch and a `#support` target |
| Section nav | Links existed for how/fees/security/FAQ, but the sticky header could cover the destination and the mobile panel stayed open | `scroll-margin-top` on all `[id]`, close-menu-before-scroll, history-aware scrolling |
| Motion | No reveals; everything appeared at once | One shared `IntersectionObserver`, opacity + small translate only |
| Homepage | Strong content, but no minimum-fee disclosure and a wide fee table that clipped on small screens | Minimum fee stated, fee table reflows into labelled rows under 760 px |
| Registration | Two-step flow but no visible progress state | Progress list with `aria-label`, step copy aligned to the three states |
| Dashboard | Status, actions and history were present but read as one long column | Status + next action first, offer/payment blocks below, history/payments last |
| Staff queue | Review cards carried every control at equal weight | Grouped header, filters and search in a toolbar, actions under the evidence |
| PWA | Service worker, manifest, offline page and icons already correct | Kept; added the pre-paint theme script to the precache and bumped the cache version |
| Accessibility | Skip link, landmarks, live regions and native dialogs already present | Switch semantics for the theme control, focus rings verified, targets raised to 44 px |

---

## B. Theme system

### Tokens

`Frontend/css/style.css` now opens with the design-token layer:

- light: ink `#0B1F33` / `#16324A` / `#24445F`, paper `#F7F4EE`, surfaces
  `#FFFFFF` / `#F1EEE7`, emerald `#0F766E`, teal `#10B981` / `#2DD4BF`;
- dark: paper `#08121B`, surfaces `#0E1C29` / `#122636` / `#162C3F`, text
  `#E7EFE9` / `#C9D8D3`, muted `#9AA8B4`, action `#14B8A6` on `#04211E`.

Both palettes define the same 60+ variables (surfaces, text, borders, actions,
status soft/line/ink triples, overlays, shadows, focus ring, panels used by the
footer, CTA, fee aside and auth aside). Components consume variables only, so
the dark theme is a token swap, not an inversion filter. There is no pure black
surface and no neon accent.

The dark block sets `color-scheme: dark`; the light block sets
`color-scheme: light`. The pre-paint script also writes `color-scheme` on
`<html>` so native controls follow the active theme.

### Contrast (computed, not eyeballed)

| Pair | Ratio |
| --- | --- |
| Light text on paper | 15.21:1 |
| Light body text on surface | 13.21:1 |
| Light muted on paper | 5.40:1 |
| Light action text / action fill | 5.47:1 |
| Light success ink / success soft | 5.11:1 |
| Light warning ink / warning soft | 7.49:1 |
| Light danger / danger soft | 5.45:1 |
| Dark text on paper | 16.11:1 |
| Dark text on surface | 14.73:1 |
| Dark body text on surface | 11.71:1 |
| Dark muted on surface | 7.10:1 |
| Dark action text / action fill | 6.80:1 |
| Dark muted-2 on surface | 5.42:1 |

All measured pairs exceed WCAG AA for normal text (4.5:1).

### No flash of the wrong theme

`Frontend/js/theme-init.js` (56 lines) is loaded synchronously in `<head>`,
before the stylesheet:

1. reads `mb-theme` from `localStorage`, falling back to `system`;
2. resolves the system preference with `matchMedia("(prefers-color-scheme: dark)")`;
3. writes `data-theme` and `color-scheme` on `<html>` and swaps the
   `theme-color` meta between `#08121B` and `#F7F4EE`;
4. removes the `no-js` class;
5. enables the reveal-hiding class only when motion is allowed and
   `IntersectionObserver` exists, with a 2.2 s watchdog and a window `error`
   listener that remove it if `core.js` never takes over.

Storage is wrapped in `try/catch`; unavailable storage leaves the theme on the
system preference for that page.

### The control

A single `<button role="switch" aria-checked="false" aria-label="Dark theme">`
sits in the header actions (desktop and mobile share one control, so there is
never a duplicate on a screen). The staff review page uses the same markup in
its own header. Sun and moon icons swap on `[data-theme="dark"]`; the button is
44 × 44 px and the label/state are updated by `core.js`.

`core.js` exposes `MB.theme.preference` (`system` / `light` / `dark`),
`MB.theme.resolved`, `MB.theme.set()` and `MB.theme.toggle()`. An explicit
choice is persisted under `mb-theme`; choosing "system" is represented by
removing the key. The page reacts to `prefers-color-scheme` changes while in
system mode and to `storage` events from other tabs. Toggling causes no layout
shift (fixed-size control) and no reload.

**Known limitation.** The switch presents light/dark. System mode is the
default (and what a first visit resolves), but there is no UI action that
returns to it once an explicit choice exists, short of clearing site data. This
is documented rather than adding a third visual state to a compact header.

---

## C. Section navigation and anchors

Real IDs exist on the public page: `how`, `fees`, `security`, `faq`, plus
`support` on the footer (contact + install + legal links), and `history`,
`payments`, `support` targets in the signed-in dashboard. The header shows
`#how`, `#fees`, `#security`, `#faq`, `#support`. Every header link is
route-qualified (`/#how`, `/#fees`, `/#security`, `/#faq`, `/#support`,
`/dashboard/#history`), so the same markup is valid on the homepage, the legal
pages, the auth pages and the dashboard.

`core.js` intercepts only same-origin, same-path anchors:

1. resolves the link with `new URL(...)` and checks origin and pathname;
2. decodes the hash and requires a real element;
3. closes the mobile panel first;
4. `history.pushState` (falling back to `location.hash`);
5. scrolls in a double `requestAnimationFrame` so the collapsing panel is
   measured correctly;
6. focuses the target with `tabindex="-1"` and removes it on blur, so keyboard
   and screen-reader position follows the destination;
7. `popstate` and `hashchange` replay the scroll, and a direct link after a
   refresh is handled on load.

`scrollIntoView` uses `behavior: "smooth"`, downgraded to `"auto"` under
`prefers-reduced-motion`. All `[id]` elements receive
`scroll-margin-top: calc(var(--header-height) + 24px)`, and form controls get a
slightly larger offset so a focused field never lands under the sticky header.

---

## D. Scroll reveals

`setupReveal()` in `core.js`:

- runs once on `DOMContentLoaded`;
- bails out and reveals everything when the user prefers reduced motion, when
  `IntersectionObserver` is absent, or when there are no `[data-reveal]` nodes;
- uses **one** observer for every `[data-reveal]` element with
  `rootMargin: "0px 0px -8% 0px"` and `threshold: 0.12`;
- adds `.is-revealed` and immediately unobserves the node;
- listens for a later change to `prefers-reduced-motion` and reveals the rest;
- sets `window.__mbRevealReady` so the boot script knows the reveal layer is
  alive.

CSS uses opacity plus an 18 px translate over `--dur-reveal`, with a small
stagger driven by `data-reveal-delay`. No bounce, zoom, parallax, rAF loop,
scroll listener or animation library. Elements are hidden only while
`<html class="has-reveal">` is present — a class added exclusively by the boot
script. If JavaScript never runs, the class is never added and all content
renders; the print stylesheet also forces reveals visible.

Homepage: hero copy, hero visual, every section heading, the four steps, the
fee layout, the three security cards, the FAQ list and the CTA. Dashboard:
header, loan card, history, payments and the three sidebar cards. Auth pages:
aside and form.

---

## E. Page simplification

- **Homepage** — hero → four steps → fee table (range, rate, worked example,
  minimum disclosure) → privacy/security cards → FAQ → support/contact →
  CTA. The fee table becomes a labelled card list on narrow screens. The
  minimum KES 200 charge is stated beside the table so the smallest band's
  effective percentage is not misleading. *(Superseded by §L: the schedule now
  starts at KES 100 with a KES 45 minimum.)*
- **Registration** — three-state progress (account → review and consent →
  ready), real labels, `aria-describedby` hints, per-field errors,
  `aria-live` status, explicit consent with links to the privacy notice and
  terms, and a submit button that stays visible above the mobile keyboard.
- **Sign in** — single column, one primary action, reset flow behind a
  disclosure, SMS unavailability stated when `SMS_BACKEND == "none"`.
- **Dashboard** — status chip and next action at the top of the loan card,
  offer and fee/payment blocks below, history and payments at the bottom,
  verification and account actions in the sidebar. No oversized stat tiles.
- **Staff queue** — toolbar (segmented status filter, search), review cards
  with phone/masked ID/registration time, document tiles, reviewer notes and
  actions grouped at the card foot. Permissions, confirmation prompts and the
  notes-required rejection rule are unchanged.

Copy that makes claims about fees, approvals or disbursement was left as-is
where it was already accurate; no new guarantees were introduced.

---

## F. Mobile and PWA

- Breakpoints at 980 / 760 / 430 px (plus fluid type and spacing).
- Mobile panel closes before scrolling; the header keeps only the brand,
  theme switch and menu button.
- Interactive targets are 44 px or larger (buttons were already 46 px; small
  buttons, the theme switch and the nav toggle were raised to 44 px).
- Fee table reflows via `table--cards`; no horizontal clipping at 320 px.
- Dialogs are native `<dialog>` with a backdrop; the install dialog fits small
  screens and provides iOS Safari steps.
- The manifest, service worker, offline page and icons are unchanged in
  behaviour. `theme-init.js` was added to the precache and the cache version
  moved to `2026-10-18.1`. A theme change requires no cache bump because the
  theme lives in storage, not in cached HTML.
- The service worker still never caches `/api/`, `/admin/`, `/health/`, the
  manifest, non-GET requests or authenticated pages. Navigations fall back to
  `/offline/`, which is token-styled and readable in both themes.
- Installed app and browser share the same origin, storage and switch.

---

## G. Verification actually performed

Run from `Backend/` with the project virtual environment and
`--settings=core.test_settings` unless stated.

| Check | Command | Result |
| --- | --- | --- |
| Django system check | `manage.py check --settings=core.test_settings` | `System check identified no issues (2 silenced).` |
| Migration drift | `manage.py makemigrations --check --dry-run --noinput` | `No changes detected` |
| Test suite | `manage.py test --settings=core.test_settings` | **Ran 235 tests, OK** (~3.8 s) at that point; 237 after section K |
| Static collection | `manage.py collectstatic --noinput` | 2 copied, 191 unmodified, 177 post-processed |
| JS syntax | `node --check` on `core.js`, `theme-init.js`, `sw.js`, `page-auth.js`, `page-dashboard.js`, `page-admin.js` | all pass |
| CSS parse | `tinycss2.parse_stylesheet` | 378 top-level rules, 0 parse errors, braces balanced (448/448) |
| Contrast | computed WCAG ratios for 15 token pairs | all ≥ 5.11:1 |
| Service worker | `curl /sw.js` | serves `const VERSION = "2026-10-18.1"`, no `__CACHE_VERSION__` |
| Illustration assets | `identify` on all 14 files | WebP 14–36 KB, JPEG 40–81 KB; correct aspect per plate |
| Rendered-page audit | Django test client on `/`, `/login/`, `/register/`, `/dashboard/`, `/legal/privacy/`, `/legal/terms/`, `/offline/` | no duplicate IDs, no unresolved same-page anchors, exactly one theme switch per page |

HTTP smoke against a running development server (fresh SQLite database,
`PAYMENTS_ENABLED=false`), all `200`:

`/`, `/register/`, `/login/`, `/dashboard/`, `/legal/privacy/`,
`/legal/terms/`, `/offline/`, `/manifest.webmanifest`, `/sw.js`,
`/robots.txt`, plus every referenced static asset (`style.css`,
`theme-init.js`, `core.js`, `pwa.js`, `auth.js`, `loan.js`, `paystack.js`,
`page-auth.js`, `page-dashboard.js`, `page-admin.js`, icons).

New regression coverage in `core/tests/test_health_and_pages.py`:
`test_appearance_boot_is_blocking_and_progressive` asserts the boot script is
ordered before the stylesheet, exactly one switch per page, the `no-js`
contract and the dark/reveal/reduced-motion CSS contracts.
`test_home_section_anchors_have_matching_targets` asserts every header anchor
resolves to an element on the page.
`test_static_illustrations_are_shipped_and_referenced` asserts every plate has
both formats, stays under 200 KB, and is referenced from its page with a WebP
`<source>` and no PNG fallback.
`test_staff_queue_and_error_pages_carry_illustration_plates` signs in a staff
user and asserts the queue plate and the 404 plate render.

### Not executed

- No browser automation was available in the environment, so **no screenshots
  and no Lighthouse/axe results are reported**. Visual claims above come from
  CSS/HTML inspection, computed contrast and HTTP-level checks.
- No real install prompt, service-worker update cycle, screen reader, on-screen
  keyboard or touch-device test was executed.
- JavaScript behaviour (menu focus order, observer timing, storage events) was
  reviewed statically and syntax-checked, not executed in a browser.

A manual pass on real devices remains the honest next step; the checklist is
in `docs/DEPLOYMENT.md` under staging QA, and the theme/nav/motion matrix in
section 13 of the master brief can be walked against this build.

---

## H. Illustration plates

The final plate set is eight original images under `Backend/Frontend/img/`,
each shipped as WebP with a JPEG fallback inside `<picture>`. They replaced the
seven earlier plates, whose stems no longer exist in the tree.

| File stem | Used on | Intrinsic size | WebP | JPEG |
| --- | --- | --- | --- | --- |
| `hero-skyline` | Homepage hero, `fetchpriority="high"` | 1440 × 900 | 100 KB | 155 KB |
| `how-flow` | Homepage "How it works" band | 1400 × 659 | 16 KB | 37 KB |
| `security-plate` | Homepage privacy/security section | 600 × 900 | 13 KB | 28 KB |
| `identity-plate` | Sign-in and registration aside | 750 × 1000 | 15 KB | 35 KB |
| `verify-plate` | Dashboard application block | 1400 × 788 | 31 KB | 59 KB |
| `staff-plate` | Staff verification queue heading | 1400 × 663 | 18 KB | 41 KB |
| `texture-band` | `/offline/`, CTA band | 1200 × 514 | 61 KB | 78 KB |
| `commerce-plate` | Homepage fee section | 1200 × 531 | 42 KB | 64 KB |

Design notes:

- The plates are obsidian-and-gold artwork: deep `#071320` fields, gold light
  lines and emerald transaction motifs. They carry no text, so they never
  pretend to be a screenshot or a dashboard.
- Every plate is decorative (`aria-hidden="true"`, empty `alt`) with explicit
  `width`/`height`, `loading="lazy"` and `decoding="async"`. The homepage hero
  is the one exception to lazy loading and is marked `fetchpriority="high"`.
- `partials/art.html` takes `file`, optional `modifier` and optional `w`/`h`,
  and renders one `<picture>` with a WebP `<source>` and a JPEG `<img>`.
- Masters (`src-*.png`) are working files. They were deleted after encoding and
  must not be referenced, precached or committed.
- `texture-band.webp`/`.jpg` are in the service-worker precache so `/offline/`
  renders its band without a network.
- Total weight is 832 KB for all sixteen final files; no page requests more
  than three.

**Deviation from the brief:** the brief suggested Unsplash CDN URLs. The
preview environment has no outbound network, so remote images would never
render; the artwork was generated locally and shipped as static assets
instead. This is recorded rather than hidden.

## I. Outstanding defects and follow-ups

1. The header switch exposes light/dark; the sidebar adds an explicit
   Light / Dark / Auto menu, where Auto is the default and follows
   `prefers-color-scheme`.
2. With JavaScript disabled the site stays in the light theme; content, links
   and legal pages remain readable. Reveals are disabled, so nothing is hidden.
3. If `core.js` were blocked but the boot script ran, content is hidden for up
   to 2.2 s before the watchdog reveals it. A script error reveals immediately.
4. `/dashboard/` still returns its shell to signed-out visitors and redirects
   client-side; the API is authoritative. Changing this would alter auth
   routing, which is out of scope.
5. The fee table's `caption` is visually hidden on mobile; the block above it
   repeats the schedule version, so no information is lost.
6. Plates are single-resolution per slot (600–1440 px wide). They are small
   enough that a `srcset` set was not justified; revisit if art direction
   changes.
7. The payment dialog can only be exercised end-to-end with
   `PAYMENTS_ENABLED=true` and a public webhook URL. In the preview those are
   off, so the dialog was verified by markup, syntax check and HTTP smoke, not
   by a live M-Pesa prompt.

---

## J. Files changed in this pass

- `Frontend/css/style.css` (2,913 lines) — token layer, dark palette, component
  tokenisation, nav/reveal/mobile refinements, print and reduced-motion rules.
- `Frontend/js/theme-init.js` (new) — blocking paint, `color-scheme`,
  `theme-color`, reveal gating and watchdog.
- `Frontend/js/core.js` — theme model and switch wiring, focus-aware anchor
  scrolling, one shared reveal observer, offline banner reuse.
- `Frontend/base.html` — `no-js`, `light dark`, boot script before the
  stylesheet.
- `Frontend/partials/header.html`, `Frontend/partials/footer.html` — switch,
  contact anchor, mode-aware fee note.
- `Frontend/index.html`, `login.html`, `register.html`, `dashboard.html`,
  `privacy.html`, `terms.html`, `admin/verification_dashboard.html` —
  `data-reveal` hooks, support anchor, staff header switch.
- `Frontend/sw.js` — precache the boot script; cache version `2026-10-18.1`.
- `core/settings.py` — `PWA_CACHE_VERSION` bumped.
- `core/tests/test_health_and_pages.py` — two new regression tests.
- `Frontend/img/` (new) — seven illustrations, WebP + JPEG.
- `Frontend/partials/art.html` (new) — `<picture>` plate partial.
- `README.md` and test-count references updated to 235.

No template was rebuilt from scratch; no backend calculation, model, migration,
payment path or auth rule was touched.

---

## K. Obsidian and gold elevation (2026-10-18)

This pass replaced the earlier light-first theme with the "Obsidian + Kenyan
Royalty Gold" system and rebuilt every view around it. No loan calculation,
payment path, authentication rule, permission or database model was changed.
The only backend-adjacent edits are the PWA cache-version and theme-colour
settings and the illustration regression tests.

### Palette and tokens

- Obsidian `#071320` page field, ink `#0B1F33` panels, card `#112A45`,
  Kenyan Royalty Gold `#D4AF37` with deep amber `#B8860B`, and M-Pesa green
  `#10B981` / emerald `#0F766E` for successful money states.
- Light theme is warm paper `#F7F4EE` with crisp off-white `#FAFAFA` cards and
  the same gold/amber accents darkened for contrast on paper.
- Borders are `1px solid rgba(212, 175, 55, 0.2)` on accent surfaces and
  `rgba(255, 255, 255, 0.08)` on dark panels; glass top bars use a 18 px
  backdrop blur with a solid fallback.
- `style.css` was rewritten from the token layer up: 3,853 lines, 932 rules,
  zero parse errors (tinycss2), balanced braces.

### Shell, theme and navigation

- Every page renders through `base.html`: skip link, offline banner, sidebar,
  glass top bar, `<main id="main">`, footer, toast region and the install
  dialog.
- The sidebar is page-aware: public (explore/quick actions), auth (process and
  trust links), dashboard (account, application, activity, payments, identity,
  quick actions) and staff (queue filters with live counts, operations).
- Below 1024 px the sidebar becomes a fixed slide-over with a scrim, focus
  moved into the panel, `inert` while closed, Escape to close and focus
  restored to the opener. The dashboard adds a five-slot bottom bar below
  760 px instead of squeezing the slide-over into a thumb reach.
- `theme-init.js` paints before the stylesheet is parsed (no flash), follows
  `prefers-color-scheme` until the user chooses, and stores an explicit choice
  under `mb-theme`. The header carries one accessible switch
  (`role="switch"`, `aria-checked`) and the sidebar carries a three-state
  Light / Dark / Auto menu (`aria-pressed`). No second switch.

### Views

- `index.html`, `login.html`, `register.html`, `dashboard.html`,
  `admin/verification_dashboard.html`, `privacy.html`, `terms.html`,
  `offline.html`, `404.html` and `500.html` were regenerated complete.
- All "pilot service" wording is gone from templates, JavaScript and CSS
  (including the former `.pilot-note` hook, renamed `.env-note`). The copy
  describes implemented controls and calls out what is not implemented
  instead of implying a licence, certification or live credit product.
- `dashboard.html` gains a native `<dialog>` M-Pesa flow: masked number, amount,
  three-step progress, spinner on the active step, provider-state chip and
  distinct copy for `PENDING`, `INITIALIZED`, `SUCCEEDED`, `FAILED`,
  `ABANDONED`, `REFUND_PENDING` and `REFUNDED`. The handler polls the saved
  payment record only; the browser never sets success.

### Assets, PWA and tests

- Eight plates were generated, encoded, inspected and shipped; the PNG masters
  were deleted. `partials/art.html` callers, the service-worker precache and
  the illustration tests were repointed in the same pass.
- Icons were regenerated by `scripts/generate_icons.py` as a gold M on an
  obsidian tile with an emerald baseline.
- `PWA_THEME_COLOR` is `#071320`; `PWA_CACHE_VERSION` is `2026-10-18.3`;
  `sw.js` precaches `texture-band.webp`/`.jpg` instead of the deleted
  `offline.*` files.
- `node --check` passes on every JavaScript file. The Django suite passes:
  **237 tests, 0 failures**. `collectstatic` reports 44 copied / 170 unmodified
  / 182 post-processed with no errors.
- `login.html` and `register.html` now load `js/auth.js` before
  `js/page-auth.js`. The page script returns early without `window.Auth`, so
  the forms previously did nothing; a regression test asserts the load order.
- HTTP smoke on the preview server returned 200 for `/`, `/login/`,
  `/register/`, `/dashboard/`, `/legal/privacy/`, `/legal/terms/`, `/offline/`,
  `/manifest.webmanifest` and `/sw.js`, and 200 for every referenced plate.
  `/admin/verification/` returns 302 to the admin login as expected.

### Not executed

- No browser automation exists in this environment, so there are **no
  screenshots, no Lighthouse score and no axe run**. Responsive behaviour was
  reviewed in CSS at the 1024 / 760 / 430 breakpoints, not rendered on devices.
- The M-Pesa dialog was not driven against a live provider: the preview runs
  with `PAYMENTS_ENABLED=false`. Its states are the backend's real enum values
  and its markup/JS were syntax-checked. The UI presents the prompt flow, but
  the backend still initialises a Paystack transaction; no Safaricom STK API
  call was added, because that would change the payment path.
- Screen-reader, on-screen-keyboard and touch-target testing remain manual.

### Preview environment

The sandbox preview runs with `DJANGO_PREVIEW_EMBED=1`. That flag drops
`X-Frame-Options` and swaps `frame-ancestors 'none'` for `frame-ancestors *`
on rendered HTML, because the hosted preview frames the app from the proxy
origin and would otherwise show a blank panel. It is off by default, does not
change any other header, is covered by
`test_preview_flag_relaxes_framing_only`, and must stay off in every deployed
environment.

---

## L. 2026-10 fee, phone and M-Pesa refinement

This pass replaces the five-band 1,000–60,000 schedule with a six-band schedule
starting at KES 100, and rebuilds the payment surfaces around it.

### Fee schedule

| Band (KES) | Rate | Example |
| --- | --- | --- |
| 100 – 999 | 9.0% | KES 45 at KES 500 |
| 1,000 – 4,999 | 10.0% | KES 499 at KES 4,999 |
| 5,000 – 9,999 | 10.5% | KES 1,049 at KES 9,999 |
| 10,000 – 19,999 | 11.0% | KES 2,199 at KES 19,999 |
| 20,000 – 34,999 | 11.5% | KES 4,024 at KES 34,999 |
| 35,000 – 60,000 | 12.0% | KES 7,200 at KES 60,000 |

`FEE_MINIMUM = 45`, `FEE_ROUNDING = 1`, and fees are **truncated down** to the
whole shilling. The formula is expressed so the rounding step cannot scale the fee:

```text
((amount × rate_bps) // (10_000 × step)) × step
```

The brief's KES 499 at KES 4,999 required a rounding decision: 4,999 × 10.0% =
499.9. The product owner chose truncation, so the published figures are **499**,
**1,049**, **2,199** and **4,024**. Half-up rounding was rejected because it
would have published 500 against the written schedule.

### Frontend changes

- Homepage: `#overview`, `#how-it-works`, `#fees`, `#security`, `#faq`,
  `#contact`; `scroll-margin-top: calc(var(--header-height) + 1.5rem)`;
  scroll-spy in `core.js` marks the section at the reading line with a gold
  rail and glow. The mobile drawer is closed on `touchstart` and on `click`
  before `scrollIntoView({behavior:"smooth", block:"start"})`.
- Fee calculator: slider 100 – 60,000, band/rate/fee/total readout. Every fee
  comes from `GET /api/loans/quote/`; no fee arithmetic is duplicated in the
  browser. The quote response now includes `fee_rate_percent` (band rate)
  alongside `fee_percent` (effective rate).
- Phone: `js/phone.js` accepts `+254…`, `254…`, `07…`, `01…` and bare
  `7…`/`1…`; it classifies Safaricom / Airtel / Telkom / Equitel and says
  plainly that M-Pesa prompts need a Safaricom line. Wired into register,
  login, password reset and the dashboard payout field.
- Sidebar: the header and the sidebar now share one theme switch plus an
  "Auto" preference; the duplicate Light/Dark/Auto segmented control was
  removed. `data-theme-toggle` therefore appears twice; the regression test
  now asserts at least two.
- M-Pesa modal: 60-second ring, `STK_PUSH_SENT` → `VERIFYING` → `SUCCESS`
  states, amber pulse / gold shimmer / emerald glow, provider and M-Pesa
  references on success, failure codes (Insufficient Funds, User Cancelled,
  STK Timeout), a re-trigger link and a "Check status" action. State is read
  from the saved payment row; the browser callback never promotes a payment.
  Polling uses the new read-only
  `GET /api/paystack/verify/<reference>/` route (owner-scoped, no provider
  call, no state change); `POST /api/payments/verify/` and the signed webhook
  remain the only verification paths.
- Ledger: filters for All / Disbursements / Repayments. Fee rows come from
  `/api/payments/history/`; disbursement rows come from loan history, only when
  `disbursed_at` exists. Repayments are deliberately empty because repayment is
  not implemented. Receipts open in a dialog and print to PDF through the
  browser (`Save as PDF`), with Paystack reference, M-Pesa code when the
  provider returned one, channel and timestamps.
- Paybill / Till card: copy buttons and a "Copied to clipboard!" toast. No
  Paybill or Till number is invented; the card shows the collection route and
  the real payment reference, and only renders configured numbers from
  `MPESA_PAYBILL_NUMBER` / `MPESA_TILL_NUMBER` (empty by default).
- Under 430 px a "Paystack Mobile Express" button opens a bottom sheet with
  pay / receipts / disbursement actions. It is display-only above 430 px.

### Verification

- `manage.py test --settings=core.test_settings` → **241 tests, OK**.
- `manage.py check --settings=core.test_settings` → no issues.
- `node --check` on every file in `Backend/Frontend/js/`.
- HTTP smoke on the preview: `/`, `/login/`, `/register/`, `/dashboard/`,
  `/legal/terms/`, `/health/` all 200. Quote endpoint: 4,999 → fee 499,
  `fee_rate_percent` 10.0.

### Not verified

- No browser automation, so no screenshots at 980 / 760 / 430; layout was
  reviewed in CSS and in the rendered markup only.
- The STK modal was not driven against a live Paystack account; the preview
  runs with `PAYMENTS_ENABLED=false`.
- Paystack's hosted mobile-money checkout collects the phone number; the app
  passes `channels: ["mobile_money"]` and `currency: "KES"` but does not call
  Safaricom directly. A server-side `/charge` call would change the payment
  path and needs explicit approval.
