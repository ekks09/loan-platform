"""Repayment schedule generation, allocation and settlement services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.errors import InvalidTransition
from core.money import from_minor, to_minor
from core.notifications import send_sms
from loans.models import Loan, RepaymentAllocation, RepaymentSchedule
from loyalty.services import record_repayment as loyalty_record_repayment
from payments.models import Payment

logger = logging.getLogger(__name__)


# Configuration constants
LATE_FEE_RATE_BPS = 150  # 1.5% per month
LATE_FEE_GRACE_DAYS = 7
LATE_FEE_MAX_BPS = 1000  # 10% cap
REPAYMENT_DAY_OF_MONTH = 1  # Due on 1st of each month


@dataclass(frozen=True)
class InstallmentPlan:
    installment_number: int
    due_on: date
    principal_kes: int
    interest_kes: int
    fees_kes: int
    total_kes: int


def _calculate_installments(
    principal_kes: int,
    service_fee_kes: int,
    disbursed_on: date,
    num_installments: int,
) -> list[InstallmentPlan]:
    """Calculate equal-principal installment schedule.

    Interest/fees are distributed evenly across installments.
    First due date is the 1st of the month following disbursement.
    """
    if num_installments <= 0:
        raise ValueError("Number of installments must be positive")

    principal_per = principal_kes // num_installments
    remainder = principal_kes % num_installments
    interest_per = service_fee_kes // num_installments
    interest_remainder = service_fee_kes % num_installments

    # First due date: 1st of next month after disbursement
    if disbursed_on.day >= REPAYMENT_DAY_OF_MONTH:
        # Disbursed on or after the 1st -> first payment next month
        first_due = date(disbursed_on.year, disbursed_on.month + 1, REPAYMENT_DAY_OF_MONTH)
        if first_due.month > 12:
            first_due = date(disbursed_on.year + 1, 1, REPAYMENT_DAY_OF_MONTH)
    else:
        # Disbursed before the 1st -> first payment this month
        first_due = date(disbursed_on.year, disbursed_on.month, REPAYMENT_DAY_OF_MONTH)

    installments = []
    for i in range(1, num_installments + 1):
        # Distribute remainder to early installments
        this_principal = principal_per + (1 if i <= remainder else 0)
        this_interest = interest_per + (1 if i <= interest_remainder else 0)
        this_fees = 0  # No additional fees in base schedule

        # Calculate due date
        due_month = first_due.month + (i - 1)
        due_year = first_due.year
        while due_month > 12:
            due_month -= 12
            due_year += 1
        due_on = date(due_year, due_month, REPAYMENT_DAY_OF_MONTH)

        installments.append(InstallmentPlan(
            installment_number=i,
            due_on=due_on,
            principal_kes=this_principal,
            interest_kes=this_interest,
            fees_kes=this_fees,
            total_kes=this_principal + this_interest + this_fees,
        ))

    return installments


@transaction.atomic
def generate_schedule(loan: Loan, *, actor=None) -> list[RepaymentSchedule]:
    """Generate repayment schedule when loan is disbursed.

    Idempotent: if schedule already exists, returns existing schedules.
    """
    if loan.status != Loan.Status.DISBURSED:
        raise InvalidTransition("Schedule can only be generated for disbursed loans.")

    existing = loan.repayment_schedules.first()
    if existing:
        logger.info("Schedule already exists for loan %s", loan.pk)
        return list(loan.repayment_schedules.all())

    # Determine number of installments based on loan amount
    # Simple rule: 1-4 installments based on amount
    if loan.amount <= 5000:
        num_installments = 1
    elif loan.amount <= 15000:
        num_installments = 2
    elif loan.amount <= 35000:
        num_installments = 3
    else:
        num_installments = 4

    # Use disbursed_at or created_at as fallback
    disbursed_on = (loan.disbursed_at or timezone.now()).date()

    plans = _calculate_installments(
        principal_kes=loan.amount,
        service_fee_kes=loan.service_fee,
        disbursed_on=disbursed_on,
        num_installments=num_installments,
    )

    schedules = []
    for plan in plans:
        schedule = RepaymentSchedule.objects.create(
            loan=loan,
            installment_number=plan.installment_number,
            due_on=plan.due_on,
            expected_principal_kes=plan.principal_kes,
            expected_interest_kes=plan.interest_kes,
            expected_fees_kes=plan.fees_kes,
            expected_total_kes=plan.total_kes,
            status=RepaymentSchedule.Status.PENDING,
        )
        schedules.append(schedule)

    loan.last_event = f"Repayment schedule generated ({len(schedules)} installments)"
    loan.save(update_fields=["last_event", "updated_at"])

    logger.info("Generated %s installments for loan %s", len(schedules), loan.pk)
    return schedules


@transaction.atomic
def allocate_payment(payment: Payment) -> list[RepaymentAllocation]:
    """Allocate a successful repayment payment to pending schedules.

    Returns list of created allocations. Idempotent on payment reference.
    """
    if payment.purpose != Payment.Purpose.REPAYMENT:
        raise InvalidTransition("Only repayment payments can be allocated.")
    if payment.status != Payment.Status.SUCCEEDED:
        raise InvalidTransition("Payment must be succeeded before allocation.")

    loan = payment.loan
    schedules = loan.repayment_schedules.filter(
        status__in=[RepaymentSchedule.Status.PENDING, RepaymentSchedule.Status.PARTIAL, RepaymentSchedule.Status.OVERDUE]
    ).order_by("installment_number")

    if not schedules.exists():
        logger.warning("No pending schedules for loan %s", loan.pk)
        return []

    remaining = payment.amount_kes
    allocations = []

    for schedule in schedules:
        if remaining <= 0:
            break
        if not schedule.can_allocate(remaining):
            continue

        alloc_amount = min(remaining, schedule.outstanding_kes)
        allocation = RepaymentAllocation.objects.create(
            payment=payment,
            schedule=schedule,
            amount_kes=alloc_amount,
        )
        allocations.append(allocation)

        schedule.amount_paid_kes += alloc_amount
        remaining -= alloc_amount

        # Update schedule status
        if schedule.amount_paid_kes >= schedule.expected_total_kes:
            schedule.status = RepaymentSchedule.Status.PAID
            schedule.paid_at = timezone.now()
        elif schedule.amount_paid_kes > 0:
            schedule.status = RepaymentSchedule.Status.PARTIAL

        # Clear overdue status if payment made
        if schedule.status == RepaymentSchedule.Status.OVERDUE and schedule.amount_paid_kes > 0:
            schedule.status = RepaymentSchedule.Status.PARTIAL if schedule.amount_paid_kes < schedule.expected_total_kes else RepaymentSchedule.Status.PAID

        schedule.save(update_fields=["amount_paid_kes", "status", "paid_at", "updated_at"])

    # Post journal entries for allocations
    if allocations:
        from payments.services import post_journal
        from payments.models import LedgerEntry

        for alloc in allocations:
            post_journal(
                [
                    (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, alloc.amount_kes),
                    (LedgerEntry.Account.LOAN_RECEIVABLE, LedgerEntry.Direction.CREDIT, alloc.amount_kes),
                ],
                reference=f"REPAY_{payment.reference}_{alloc.schedule.installment_number}",
                memo=f"Repayment allocation for schedule {alloc.schedule.installment_number}",
                loan=loan,
                payment=payment,
            )

        # Record loyalty effects for each fully-paid schedule
        for schedule in schedules:
            if schedule.status == RepaymentSchedule.Status.PAID and not schedule.paid_at:
                # This installment was completed by this payment
                loyalty_record_repayment(
                    user=loan.user,
                    loan=loan,
                    reference=payment.reference,
                    amount_kes=schedule.expected_total_kes,
                    due_on=schedule.due_on,
                    paid_at=timezone.now(),
                    on_time=not schedule.is_overdue,
                )
                schedule.paid_at = timezone.now()
                schedule.save(update_fields=["paid_at", "updated_at"])

    logger.info("Allocated payment %s to %s installments for loan %s",
                payment.reference, len(allocations), loan.pk)
    return allocations


def apply_late_fees(loan: Loan, *, actor=None) -> int:
    """Apply late fees to overdue installments.

    Returns number of schedules that received late fees.
    """
    overdue = loan.repayment_schedules.filter(
        status__in=[RepaymentSchedule.Status.PENDING, RepaymentSchedule.Status.PARTIAL, RepaymentSchedule.Status.OVERDUE],
        due_on__lt=timezone.now().date() - timedelta(days=LATE_FEE_GRACE_DAYS),
    )

    count = 0
    for schedule in overdue:
        if schedule.late_fee_kes > 0:
            continue  # Already applied

        # Late fee: LATE_FEE_RATE_BPS per month on outstanding, capped at LATE_FEE_MAX_BPS
        days_overdue = (timezone.now().date() - schedule.due_on).days
        months_overdue = max(1, (days_overdue + 29) // 30)  # Round up

        late_fee = (schedule.outstanding_kes * LATE_FEE_RATE_BPS * months_overdue) // 10_000
        max_fee = (schedule.outstanding_kes * LATE_FEE_MAX_BPS) // 10_000
        late_fee = min(late_fee, max_fee)

        if late_fee <= 0:
            continue

        schedule.late_fee_kes = late_fee
        schedule.late_fee_applied_at = timezone.now()
        schedule.expected_fees_kes += late_fee
        schedule.expected_total_kes += late_fee
        schedule.status = RepaymentSchedule.Status.OVERDUE
        schedule.save(update_fields=[
            "late_fee_kes", "late_fee_applied_at",
            "expected_fees_kes", "expected_total_kes", "status", "updated_at",
        ])

        loan.last_event = f"Late fee KES {late_fee:,} applied to installment {schedule.installment_number}"
        loan.save(update_fields=["last_event", "updated_at"])

        count += 1

        # Notify borrower
        send_sms(
            loan.user.phone,
            f"Your {settings.PLATFORM_NAME} installment {schedule.installment_number} is overdue. "
            f"Late fee KES {late_fee:,} applied. Pay now to avoid further charges.",
            purpose="repayment_overdue",
            user=loan.user,
        )

    if count:
        logger.info("Applied late fees to %s installments for loan %s", count, loan.pk)
    return count


def check_loan_settlement(loan: Loan) -> bool:
    """Check if loan is fully repaid and mark as REPAID if so.

    Returns True if loan was settled by this call.
    """
    if loan.status != Loan.Status.DISBURSED:
        return False

    schedules = loan.repayment_schedules.all()
    if not schedules.exists():
        return False

    all_paid = all(s.status == RepaymentSchedule.Status.PAID for s in schedules)
    if not all_paid:
        return False

    # All installments paid - mark loan as REPAID
    loan.transition_to(Loan.Status.REPAID, actor=None, reason="All installments repaid")
    logger.info("Loan %s marked as REPAID", loan.pk)

    # Notify borrower
    send_sms(
        loan.user.phone,
        f"Congratulations! Your {settings.PLATFORM_NAME} loan of KES {loan.amount:,} "
        f"has been fully repaid. Thank you for your timely payments.",
        purpose="loan_settled",
        user=loan.user,
    )
    return True


@transaction.atomic
def process_repayment_webhook(payment: Payment) -> list[RepaymentAllocation]:
    """Process a successful repayment payment from webhook.

    This is the entry point called from payments webhook for repayment purpose.
    """
    if payment.purpose != Payment.Purpose.REPAYMENT:
        return []

    allocations = allocate_payment(payment)

    # Check settlement
    check_loan_settlement(payment.loan)

    return allocations
