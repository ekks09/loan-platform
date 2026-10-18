# PESAFIX dashboard & sidebar — unified layout blueprint

Status: **implemented** on the `arena/8a4cb94f-loan-platform` branch.

Goal: strip the post-login surface down to a borrowing funnel. Every screen
element either moves money forward or is removed from view. Legal and
infrastructure prose is collapsed behind one disclosure, never deleted.

---

## 1. Dynamic sidebar (`Frontend/partials/sidebar.html`, `active="dashboard"`)

### Navigation tree

```
Account
  💹 Overview        → /dashboard/            (selected, aria-current="page")
  💱 Quick Apply     → /dashboard/?apply=1#borrow
  💰 My Wallet       → /dashboard/#repay
Quick actions
  💸 New Application → /dashboard/?apply=1#loanCard   (high-visibility accent)
  💳 Upload Documents→ /dashboard/#verifyTitle        (hidden unless documents are missing)
```

Removed: **Identity** and **Activity** as standalone items (identity lives in
the dashboard pipeline; activity is the transaction history disclosure).
Removed: the bottom technical card ("Money states are server-authoritative…")
and its button. It is replaced by one low-profile status marker:

```
● Secure · balances server-confirmed
```

### Account panel (replaces the dashboard "Account" card)

The sidebar footer is now a `<details id="sideAccount">` disclosure holding
phone, masked national ID, member-since and sign out — plus support channels.
On phones the bottom bar's **Account** tab opens the drawer on this panel
(`data-nav-open data-nav-focus="sideAccount"` in `core.js`).

### Exact string set

| Key | String |
| --- | --- |
| Overview | `💹 Overview` |
| Quick Apply | `💱 Quick Apply` |
| My Wallet | `💰 My Wallet` |
| New Application | `💸 New Application` |
| Upload Documents | `💳 Upload Documents` |
| Status marker | `Secure · balances server-confirmed` |

---

## 2. Main dashboard pipeline (`Frontend/dashboard.html`)

Order, top to bottom — a single centred column (`max-width: 760px`), so the
stack reads identically on desktop and phone:

```
.funnel
  1  #overview   👤 Profile Status: Verified ✅
  2  #limit      [credit dial]  💹 Max Limit  KES 15,000  💱
  3  #borrow     💱 Need Instant Cash? 💸      [ ⚡ BORROW NOW ]
  4  #repay      Amount Due: KES X,XXX   Due Date: DD/MM/YYYY
                 [ 💳 PAY NOW ]
                 recent activity (3 rows)
                 ▸ 💰 View Transaction History   → Payments + Application history
.funnel-stack
  ·  Identity verification        (visible — the gate before borrowing)
  ·  Your loan / application      (visible — BORROW NOW scrolls here)
  ·  Updates                     (visible — in-app notifications)
  ·  ▸ 💹 PESAFIX credit health   (collapsed, last)
  ·  ▸ 🔒 Notices & support       (collapsed, last)
```

### Module contract

| Module | Element | Behaviour |
| --- | --- | --- |
| 1 | `#funnelProfileState` | `Verified ✅` / `In review` / `Action needed` / `Not verified`, painted from `GET /users/me/` |
| 2 | `#loyaltyRing`, `#loyaltyScore`, `#limitValue` | 360 credit dial beside the largest type on the page; value from the loyalty borrowing limit, capped by the product maximum |
| 3 | `#borrowNow` | Reveals `#applyBlock`, scrolls to `#loanCard`, focuses `#amount`; otherwise routes to verification with a toast |
| 4 | `#dueAmount`, `#dueDate`, `#funnelPayNow` | Amount/due date from the active loan; **PAY NOW** is enabled only when a real amount is payable (fail-closed) |

### Removed from the dashboard

* `Fees disabled · no live credit` badge
* The multi-sentence "Operating environment" banner (kept, collapsed, under
  **Notices & support**)
* The standalone **Account** and **Support** cards (moved to the sidebar)
* Technical payment prose is untouched inside the pay dialog, where it is part
  of the transaction, not the funnel

### Exact string set

```
👤 Profile Status:
Verified ✅        In review        Action needed        Not verified
💹 Max Limit
Unlock your limit  |  Ready to borrow  |  Verify your ID to unlock it
💱 Need Instant Cash? 💸
⚡ BORROW NOW
Amount Due:        Due Date:
💳 PAY NOW
💰 View Transaction History
💹 PESAFIX credit health
🔒 Notices & support
```

---

## 3. Global UI rules

* **Numbers dominate.** `.funnel-money` is `clamp(2rem, 8.6vw, 3.4rem)` at
  weight 800 with tabular figures; amounts and scores are the boldest type on
  the page. KES values use `MB.money()` (Intl, `en-KE`).
* **Tight spacing.** Module padding `clamp(12px, 1.6vw, 18px)`, stack gap
  `--space-3`, no decorative margins, so the four modules fit a phone screen.
* **Emoji are anchors, not content.** Every emoji is `aria-hidden="true"` and
  sits beside a real label: 💹 limits, 💱 apply/exchange, 💰 balances and
  history, 💸 new money, 💳 pay/documents.
* **Money figures wrap.** `overflow-wrap: anywhere` on every amount, so a long
  figure can never push content off a 320px screen.

---

## 4. Mobile notification feed

Changes in `css/style.css` (section "Narrow-screen safeguards"):

* `.notify-item` is a two-column grid (marker + body) instead of a bare flex
  row, so the body cannot be squeezed by the marker.
* Titles, timestamps and message bodies wrap (`overflow-wrap: anywhere`) and
  the timestamp no longer inherits an 8px indent that mis-aligned it under the
  title.
* Unread items read as cards: gold-tinted surface, gold hairline.
* `.card-head` chips stop competing with headings on narrow screens
  (`flex: none`, wrapping).
* Toasts wrap their text onto its own line, keep both actions reachable, and
  sit above the bottom bar including the safe-area inset.
* Bottom bar labels are single-line and ellipsised so five tabs share a
  phone-width row.

---

## 5. Front-end architecture notes

* `js/page-dashboard.js` owns the funnel: `renderFunnel()` →
  `paintStanding()`, `paintLimit()`, `paintDue()`, `paintRecent()`. It is
  called after the profile, the loan and the ledger load, and re-runs when the
  loyalty panel publishes a new borrowing limit (`mb:borrow-limit`).
* No new endpoints. Every figure comes from data the page already fetched.
* Content inside a `<details>` must not carry `data-reveal`: closed content has
  no layout, so the IntersectionObserver would never fire and the block would
  stay invisible when opened.
* `js/core.js` gained an optional focus target for the drawer
  (`data-nav-focus`), used by the mobile **Account** tab.
