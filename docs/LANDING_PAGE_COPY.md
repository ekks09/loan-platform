# Landing page — marketable copy system

Status: **implemented**. The public page now sells the product first and keeps
the operational detail one tap away.

## Voice

Kenyan, conversational, Sheng-friendly but never unreadable: short sentences,
real situations (rent, stock, a puncture before work), and "bob" instead of
"KES" in the informal lines. Every promise still has the formal figure next to
it, so nothing depends on slang to be understood.

## Copy set (`Frontend/index.html`)

| Slot | Copy |
| --- | --- |
| Eyebrow | `🇰🇪 Made for Kenyan hustlers` |
| Headline | `Form ni ku-boost cash — instantly! 💸` |
| Subheadline | `Need quick cash for a small emergency or the daily hustle? Boost from as low as KES 100 — no paperwork, no queues, no guarantor. Just your phone and your ID, then M-PESA does the rest.` |
| Primary CTA | `⚡ Boost sasa` |
| Secondary CTA | `See how it works` |
| Trust strip | `📱 Apply on your phone · 🪪 ID only — no paperwork · 💸 Cash straight to M-PESA` |

### Key features (three, replacing the technical trio)

| Emoji | Title | Line |
| --- | --- | --- |
| 💸 | Instant M-PESA payout | Once approved, cash lands on your own M-PESA line |
| 📈 | Start small, grow big | Borrow from KES 100, build your limit with every on-time repayment |
| 📱 | 100% digital & paperless | Just your phone number and National ID |

### Three steps

| Step | Copy |
| --- | --- |
| 01 📱 | **Enter your phone number** — three minutes, free to create |
| 02 🎯 | **Choose your amount** — from KES 100 to KES 60,000, cost shown first |
| 03 💸 | **Get M-PESA cash** — confirmed by the provider, clear on time to grow |

### Section headlines

- Why: *Small boost today, bigger limit tomorrow.*
- How: *Hatua tatu tu — three steps and you're sorted.*
- Fees: *Ujue cost kabla u-send. Know the cost first.*
- Stories: *Real boosts, real hustles.*
- Security: *Privacy, straight up.*
- FAQ: *Maswali ya kawaida — straight answers.*
- Closing CTA: *Account ni bure. Start in three minutes.*

## Hiding the complexity

Two disclosures keep the page short without deleting anything:

- `🔍 The full process, in detail` — the five-step operational flow.
- `📋 See the full fee table` — the band table, the fee-collection banner and
  the "what the fee covers" copy.

The fee **calculator stays on the surface**: it is the one piece of "detail"
that actually sells, because it answers "how much will this cost me?" without
a scroll.

## Configurable marketing slots (`Backend/core/settings.py`)

```bash
PLATFORM_SOCIAL_PROOF="Over KES 100,000+ disbursed to happy hustlers!"
PLATFORM_TESTIMONIALS_ENABLED=true
```

| Setting | Behaviour |
| --- | --- |
| `PLATFORM_SOCIAL_PROOF` | Shown in the hero badge. **Empty by default** — the template falls back to a non-numeric line rather than publishing a statistic the business cannot evidence. |
| `PLATFORM_TESTIMONIALS_ENABLED` | `false` removes the stories block entirely. |
| `PLATFORM_TESTIMONIALS` | The quote list. Edit `core/settings.py` — each entry is `name`, `role`, `quote`. |

⚠️ **The three shipped stories are placeholder illustrations.** Replace them
with real, consented customer quotes (or set `PLATFORM_TESTIMONIALS_ENABLED=false`)
before this page runs as a live advert. Publishing invented reviews as genuine
customer feedback breaches advertising and consumer-protection rules, and a
digital-credit licence application is not the place to be caught doing it.

## Claims that stay honest

The marketing voice never overrides the platform's fail-closed posture:

- The minimum is rendered from `Loan.MIN_AMOUNT` (KES 100) — never typed.
- "Get M-PESA cash" is written as happening **after approval and provider
  confirmation**; the page never promises an approval or a clock.
- The licence status line and the "no live credit in this environment" banner
  are still rendered, just inside the fee disclosure instead of the hero.
- No interest rate, term or repayment schedule is stated: repayment is not
  implemented in this build.
