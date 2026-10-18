# Credit product specification (Phase 3 — decisions only)

> **Nothing in this document offers credit.** No interest rate, term, APR,
> penalty, grace period, limit or repayment schedule is proposed. Those are
> business, actuarial and legal decisions that must be made and approved before
> the product exists. Engineering has deliberately not invented them.

## 0. Current state

The repository implements an **application and disbursement pipeline only**:

- KES 100–60,000 principal;
- a disclosed processing fee from a contiguous schedule (proposed pricing,
  fee collection disabled pending approval);
- verified identity and consent gates;
- written offer acceptance;
- provider-confirmed disbursement, reversal and refund mechanics;
- an immutable double-entry ledger.

It does **not** implement repayment, interest, due dates, arrears, penalties,
credit reporting, restructuring or collections. `REPAID` exists as a reserved
state and cannot be reached because no repayment lifecycle is defined.
`REPAYMENT` exists as a reserved payment purpose but is not accepted by any
endpoint.

## 1. Decisions required before design can be completed

Each item needs an owner, a written decision and (where indicated) external
approval. "TBD" is the correct value today.

| # | Decision | Owner | Why it blocks engineering |
| --- | --- | --- | --- |
| 1 | Legal product classification and CBK licensing position | Legal / compliance | Determines whether the product may be offered at all, and which rules apply |
| 2 | Product type (single-repayment loan, instalment, revolving line, etc.) | Business | Changes the domain model, not just the UI |
| 3 | Pricing: interest, fees, APR disclosure and total cost of credit | Business / finance / legal | The processing-fee schedule is implemented and versioned (`2026-10-draft`) but must be approved before collection; interest/APR/repayment terms are still undefined |
| 4 | Term and repayment frequency | Business | Drives due-date, schedule and reminder logic |
| 5 | Eligibility: income, employment, existing obligations, age, geography | Credit / risk | Drives data collection and decisioning |
| 6 | Limits and exposure caps per borrower | Credit / risk | Prevents uncontrolled lending |
| 7 | Credit assessment method and data sources (including any bureau) | Credit / risk / legal | Requires lawful basis and vendor agreements |
| 8 | Repayment channels and reconciliation of partial payments | Finance / payments | Determines the repayment ledger and allocation rules |
| 9 | Arrears, penalty and hardship policy | Credit / legal | Must be fair, disclosed and lawful; cannot be invented in code |
| 10 | Collections approach and third-party agencies | Operations / legal | Consumer-protection and conduct rules apply |
| 11 | Complaints, disputes and escalation SLAs | Operations / legal | Required for licensing and trust |
| 12 | Credit reporting obligations and bureau integration | Compliance | Statutory and contractual |
| 13 | Capital source, treasury float and settlement policy | Finance | Determines whether disbursement can be funded |
| 14 | Insurance or guarantee arrangement (if any) | Business / legal | Changes cost disclosure and risk |
| 15 | Data retention schedule, DPIA and processor DPAs | Data protection | Required before processing at scale |
| 16 | Pricing-change governance (CBK notification where applicable) | Compliance | New products/pricing may require approval |

## 2. Domain model changes that will be needed

Only after the decisions above:

- **Loan terms**: principal, approved rate, fee components, term, instalment
  count, first/last due date, total repayable, APR-equivalent disclosure.
- **Repayment schedule**: one row per instalment with due date, principal,
  interest/fee split, amount paid, allocation order.
- **Repayment transactions**: a payment with `purpose=repayment`, allocated to
  instalments; partial and over-payments must have explicit rules.
- **Arrears**: days past due, ageing buckets, penalty accrual (only if approved
  and lawful), hardship flags.
- **Settlement**: `REPAID` reached only when the allocated total equals the
  amount due (or an approved settlement amount).
- **Credit decision record**: inputs, ruleset version, outcome, reasons,
  reviewer, appeal path.
- **Collections events**: contact attempts, promises to pay, complaints,
  hardship arrangements — access-controlled and auditable.

Do not reuse the service-fee payment record as a repayment. Keep purposes and
ledger accounts distinct.

## 3. API and UI changes

- Quote endpoint must return the **full cost of credit**, not only a processing
  fee, including the approved disclosure fields.
- Offer acceptance must record the exact terms version, the schedule, and the
  total repayable.
- Dashboard must show next due date, amount due, paid-to-date, outstanding
  balance and a downloadable statement.
- Staff operations need repayment allocation, reversal, settlement, hardship
  and complaint workflows with four-eyes approval for write-offs.
- Every money movement must continue to use the existing double-entry ledger,
  idempotency keys and reconciliation pattern.

## 4. Risk and compliance artifacts required

- Licence application/permission and any CBK product or pricing notifications.
- Approved customer terms, fee table, privacy notice and complaints policy.
- DPIA, lawful basis, retention schedule, data-subject request procedure.
- Processor agreements (Paystack, Supabase, SMS provider) with region and
  sub-processor confirmation.
- Credit policy, affordability rules, forbearance and collections policy.
- Outsourcing/vendor risk assessments and exit plans.
- Independent security test and penetration-test remediation.
- Financial model, treasury plan and reconciliation controls.

## 5. Launch gates (all required)

1. Written legal opinion on licensing and product structure.
2. Approved pricing and total-cost disclosure, with a signed-off example.
3. Approved terms/privacy/complaints pack published in the app.
4. DPIA signed off; DPAs executed.
5. Credit policy and limits approved; decision records auditable.
6. Repayment, allocation, arrears and settlement implemented **and tested**.
7. Treasury funded; disbursement and refund limits agreed.
8. Reconciliation and month-end controls signed off by finance.
9. Independent penetration test passed; critical/high findings closed.
10. Staff 2FA, access reviews and operational runbooks in place.
11. Controlled pilot with capped exposure and daily monitoring.
12. Post-pilot review before general availability.

Until gates 1–6 are met, the correct production posture is the current one:
the service may be shown internally, but no live applications are accepted,
no money moves and no credit is offered.
