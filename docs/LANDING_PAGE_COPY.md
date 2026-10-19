# Landing page — plain, brand-forward copy

Status: **implemented**.

The public page opens on the brand, states the offer in one line, and gets out
of the way. No slang, no emoji, no invented numbers: the labels, the amounts
and the step numbers carry the meaning.

## Voice

Short sentences, plain English, no Kenyan-register or Sheng copy. Anything the
platform cannot evidence is either rendered from the server (loan limits, fee
schedule) or left unpublished.

## Copy set (`Frontend/index.html`)

| Slot | Copy |
| --- | --- |
| Brand | `{{ PLATFORM_NAME }}` wordmark under the brand mark (SVG) |
| Small header | `Borrow Quick Loan Now` |
| Title tag | `{{ PLATFORM_NAME }} — Borrow Quick Loan Now` |
| Lead | `Quick loans from KES 100 to KES 60,000, paid straight to your M-PESA. Apply with your phone number and National ID. No paperwork, no branch visits.` |
| Primary CTA | `Create Account` |
| Secondary CTA | `See how it works` |
| Trust points | `Free to create an account` · `No paperwork, no guarantor` · `Money sent to your M-PESA line` |

The headline is the platform name itself, rendered as an `<h1>` beside the
brand mark. The KES figures come from `Loan.MIN_AMOUNT` / `Loan.MAX_AMOUNT`, so
the offer can never drift from what the server will actually lend.

### Key features (three cards)

| Title | Line |
| --- | --- |
| Instant M-PESA payout | Once your loan is approved, the money is sent straight to the M-PESA line registered on your account. |
| Start small, grow your limit | Borrow from KES 100 and grow your limit each time you repay on time. |
| 100% digital and paperless | All you need is your phone number and your National ID. Apply from anywhere. |

### Three steps

| Step | Copy |
| --- | --- |
| 01 | **Enter your phone number** — register with your Kenyan mobile number and National ID, then record consent |
| 02 | **Choose your loan amount** — from KES 100 to KES 60,000, fee shown before you commit |
| 03 | **Receive M-PESA cash** — approved and confirmed, paid to the line on your account |

### Section headlines

- Why: *Simple loans. Clear costs.*
- How: *Three steps.*
- Fees: *Know the cost before you apply.*
- Security: *Privacy, straight up.*
- FAQ: *Straight answers.*
- Closing CTA: *Create your free account.*

## Proof band

Four facts, no statistics: minimum loan (server-rendered) · 3 min to apply · no
paperwork · paid to M-PESA. The hero badge below them takes its text from
`PLATFORM_SOCIAL_PROOF` and falls back to a non-numeric line, so a
volume claim is only published once the business sets one.

## Hiding the complexity

Two disclosures keep the page short without deleting anything:

- `The full process, in detail` — the five-step operational flow.
- `See the full fee table` — the band table, the fee-collection banner and the
  "what the fee covers" copy.

The fee **calculator stays on the surface**: it is the one piece of detail that
sells, because it answers "how much will this cost me?" without a scroll.

## Configurable marketing slot (`Backend/core/settings.py`)

```bash
PLATFORM_SOCIAL_PROOF="Over KES 100,000+ disbursed to happy customers"
```

| Setting | Behaviour |
| --- | --- |
| `PLATFORM_SOCIAL_PROOF` | Hero badge text. **Empty by default** — the template falls back to *"Clear fees, quick decisions, and money paid straight to your M-PESA."* rather than publishing a statistic that cannot be evidenced. |

## Claims that stay honest

- The minimum and maximum are rendered from the model — never typed into copy.
- "Receive M-PESA cash" is written as following **approval and provider
  confirmation**; the page never promises an approval or a clock.
- The licence status line, the fee-collection banner and the "no live credit in
  this environment" note still render, just inside the disclosures.
- No interest rate, term or repayment schedule is stated: repayment is not
  implemented in this build.
