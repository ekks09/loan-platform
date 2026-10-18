"""Loan application services.

All state changes go through ``Loan.transition_to`` so illegal transitions are
impossible regardless of which view or admin action triggered them.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction

from core.errors import DomainError, InvalidLoanAmount, InvalidTransition
from loans.models import Loan
from users.phone import normalize_ke_phone


class ActiveLoanExists(DomainError):
    code = "active_loan_exists"
    http_status = 409
    default_message = "You already have an open loan application."


class AmountAboveTierLimit(InvalidLoanAmount):
    code = "amount_above_tier_limit"


def borrowing_limit(user) -> int:
    """The largest principal this borrower may apply for today.

    Base members can apply up to the loyalty layer's standard ceiling; Gold
    members reach the product maximum. Both are read from the loyalty app so
    one ladder controls the limit, the badge and the dashboard copy.
    """
    from loyalty.services import account_for, max_limit_kes

    return min(Loan.MAX_AMOUNT, max_limit_kes(account_for(user)))


def quoted_fee(user, amount: int) -> int:
    """Published schedule fee for ``amount``, less any member discount."""
    base_fee = Loan.compute_service_fee(amount)
    from loyalty.services import account_for, discounted_fee

    return discounted_fee(base_fee, account_for(user))


@transaction.atomic
def create_application(*, user, amount: int, mpesa_phone: str) -> Loan:
    """Create a loan application atomically.

    The partial unique constraint on ``(user) WHERE status IN open`` is the
    final guard against the check-then-create race; the explicit check gives a
    useful error in the common case.
    """
    if Loan.objects.filter(user=user, status__in=Loan.OPEN_STATUSES).exists():
        raise ActiveLoanExists()
    limit = borrowing_limit(user)
    if amount > limit:
        raise AmountAboveTierLimit(
            f"Your current borrowing limit is {limit:,} KES. "
            "Three consecutive on-time repayments raise it."
        )
    fee = quoted_fee(user, amount)
    try:
        loan = Loan.objects.create(
            user=user,
            amount=int(amount),
            service_fee=fee,
            mpesa_phone=normalize_ke_phone(mpesa_phone),
            status=Loan.Status.PENDING,
            last_event="Application submitted",
        )
    except IntegrityError as exc:
        raise ActiveLoanExists() from exc
    from loans.models import LoanEvent

    LoanEvent.objects.create(
        loan=loan,
        from_status="",
        to_status=Loan.Status.PENDING,
        actor=user,
        reason="Application submitted",
    )
    return loan


def approve(loan: Loan, *, actor, expiry_hours: int = 48) -> Loan:
    with transaction.atomic():
        return loan.issue_offer(actor=actor, expiry_hours=expiry_hours)


def reject(loan: Loan, *, actor, reason: str) -> Loan:
    if loan.status not in {Loan.Status.PENDING, Loan.Status.APPROVED}:
        raise InvalidTransition("This application can no longer be rejected.")
    if not reason.strip():
        raise InvalidTransition("A rejection reason is required.")
    with transaction.atomic():
        loan.rejection_reason = reason.strip()[:2000]
        loan.save(update_fields=["rejection_reason", "updated_at"])
        loan.transition_to(Loan.Status.REJECTED, actor=actor, reason=reason[:255])
    return loan


def cancel(loan: Loan, *, actor, reason: str = "Cancelled by customer") -> Loan:
    if loan.status not in {Loan.Status.PENDING, Loan.Status.APPROVED}:
        raise InvalidTransition("This loan can no longer be cancelled.")
    with transaction.atomic():
        loan.transition_to(Loan.Status.CANCELLED, actor=actor, reason=reason)
    return loan


def accept_offer(loan: Loan, *, actor, version: str) -> Loan:
    with transaction.atomic():
        return loan.accept_offer(version=version, actor=actor)


def expire_stale_offers(*, dry_run: bool = True) -> int:
    from django.utils import timezone

    stale = Loan.objects.filter(
        status=Loan.Status.APPROVED, offer_expires_at__lt=timezone.now()
    )
    count = stale.count()
    if dry_run:
        return count
    for loan in stale:
        loan.transition_to(
            Loan.Status.EXPIRED, reason="Offer expired without acceptance"
        )
    return count
