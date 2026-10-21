"""Payment, disbursement, refund and reconciliation services.

Concurrency and safety rules implemented here:

* provider calls happen outside database transactions;
* every money-moving operation is claimed under ``select_for_update`` and is
  idempotent on its provider reference;
* a transfer is only ``SUCCESS`` when the provider confirms it — an accepted
  request is ``PENDING_PROVIDER``;
* an uncertain outcome becomes ``UNKNOWN`` and must be reconciled before retry;
* every consequential movement posts balanced, immutable ledger entries.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from typing import Mapping

from core.errors import (
    FeeCollectionDisabled,
    InvalidTransition,
    PaymentsDisabled,
)
from core.money import from_minor, to_minor
from core.notifications import send_sms
from loans.models import Loan, RepaymentSchedule
from payments.models import (
    LedgerEntry,
    Payment,
    ReconciliationRun,
    Refund,
    Transfer,
    TransferEvent,
    WebhookEvent,
)
from payments.providers import (
    ChargeStatus,
    ChargeVerification,
    PaymentProvider,
    ProviderError,
    RefundStatus,
    TransferState,
    TransferStatus,
    WebhookEventKind,
    get_payment_provider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------
def post_journal(
    lines: list[tuple[str, str, int]],
    *,
    reference: str,
    memo: str,
    loan: Loan | None = None,
    payment: Payment | None = None,
    transfer: Transfer | None = None,
    refund: Refund | None = None,
    created_by=None,
) -> uuid.UUID:
    """Post a balanced journal.

    ``lines`` is a list of ``(account, direction, amount_kes)``. Debits and
    credits must net to zero; otherwise the journal is rejected before any row
    is written.
    """
    debits = sum(amount for _, direction, amount in lines if direction == "DEBIT")
    credits = sum(amount for _, direction, amount in lines if direction == "CREDIT")
    if not lines or debits != credits or debits <= 0:
        raise ValueError(
            f"Unbalanced journal: debits={debits} credits={credits} lines={len(lines)}"
        )
    group = uuid.uuid4()
    with transaction.atomic():
        LedgerEntry.objects.bulk_create(
            [
                LedgerEntry(
                    entry_group=group,
                    account=account,
                    direction=direction,
                    amount_kes=amount,
                    reference=reference[:64],
                    memo=memo[:255],
                    loan=loan,
                    payment=payment,
                    transfer=transfer,
                    refund=refund,
                    created_by=created_by,
                )
                for account, direction, amount in lines
            ]
        )
    logger.info(
        "Ledger group %s posted for %s (%s KES)", group, reference, debits
    )
    return group


def journal_balance(entry_group) -> int:
    """Return debits minus credits for a journal group (must be zero)."""
    totals = LedgerEntry.objects.filter(entry_group=entry_group).aggregate(
        debit=Sum("amount_kes", filter=models_q("DEBIT")),
        credit=Sum("amount_kes", filter=models_q("CREDIT")),
    )
    return int(totals["debit"] or 0) - int(totals["credit"] or 0)


def models_q(direction: str):
    from django.db.models import Q

    return Q(direction=direction)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def internal_email(phone: str) -> str:
    return f"user-{phone}@{settings.INTERNAL_EMAIL_DOMAIN}"


def _provider() -> PaymentProvider:
    """Return the configured payment provider (fails closed when disabled)."""
    return get_payment_provider()


def get_or_create_fee_payment(loan: Loan) -> Payment:
    """Create the fee payment row for a loan exactly once."""
    existing = loan.payments.filter(purpose=Payment.Purpose.SERVICE_FEE).first()
    if existing:
        return existing
    reference = f"LPF_{loan.pk}_{secrets.token_hex(6)}"
    payment = Payment.objects.create(
        loan=loan,
        user=loan.user,
        purpose=Payment.Purpose.SERVICE_FEE,
        reference=reference,
        idempotency_key=f"fee:loan:{loan.pk}",
        amount_kes=loan.compute_service_fee(loan.amount),
        currency="KES",
        status=Payment.Status.PENDING,
        provider=settings.PAYMENT_PROVIDER,
    )
    Loan.objects.filter(pk=loan.pk).update(paystack_reference=reference)
    loan.refresh_from_db(fields=["paystack_reference"])
    return payment


def initialize_fee_payment(loan: Loan, *, actor=None) -> Payment:
    """Create/reuse a Paystack checkout for the loan's fee.

    The upfront fee is refused unless both the product flag and the explicit
    approval flag are set (enforced in settings at boot as well).
    """
    if not settings.PAYMENTS_ENABLED:
        raise PaymentsDisabled()
    if settings.FEE_COLLECTION_MODE != "upfront":
        raise FeeCollectionDisabled()
    if loan.status != Loan.Status.APPROVED:
        raise InvalidTransition("A service fee can only be paid on an approved loan.")
    if settings.REQUIRE_OFFER_ACCEPTANCE and not loan.offer_is_accepted:
        raise InvalidTransition("Accept the loan offer before paying a fee.")

    payment = get_or_create_fee_payment(loan)
    if payment.status == Payment.Status.SUCCEEDED:
        return payment
    if payment.authorization_url and payment.access_code:
        return payment

    checkout = _provider().initialize(
        email=internal_email(loan.user.phone),
        amount_minor=to_minor(payment.amount_kes),
        reference=payment.reference,
        currency=payment.currency,
        metadata={
            "loan_id": loan.pk,
            "purpose": "service_fee",
            "user_id": loan.user_id,
            # Read back by the dashboard to label the prompt as M-Pesa rather
            # than a generic checkout. Never used for authorisation.
            "provider": "mpesa",
        },
        channels=["mobile_money"],
    )

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        payment.provider_reference = (checkout.provider_reference or payment.reference)[:64]
        payment.authorization_url = checkout.authorization_url
        payment.access_code = checkout.access_code or None
        payment.save(
            update_fields=[
                "provider_reference",
                "authorization_url",
                "access_code",
                "updated_at",
            ]
        )
        if payment.status == Payment.Status.PENDING:
            payment.transition_to(
                Payment.Status.INITIALIZED, reason="Checkout created", actor=actor
            )
    logger.info("Payment %s initialised with provider", payment.pk)
    return payment


# ---------------------------------------------------------------------
# Charge success
# ---------------------------------------------------------------------
def _verify_charge_with_provider(payment: Payment) -> ChargeVerification:
    return _provider().verify(payment.reference)


def _coerce_charge(data: ChargeVerification | Mapping) -> ChargeVerification:
    """Normalize a provider charge payload when one is passed directly.

    Internal callers pass :class:`ChargeVerification`. A raw mapping is the
    authenticated webhook payload path; the provider adapter is the only
    code allowed to parse it.
    """
    if isinstance(data, ChargeVerification):
        return data
    return _provider().normalize_charge(data)


def _validate_charge(payment: Payment, verification: ChargeVerification) -> None:
    if verification.status is not ChargeStatus.SUCCESS:
        raise InvalidTransition(
            f"Provider reports payment status {verification.status.value}."
        )
    if verification.reference != payment.reference:
        raise InvalidTransition("Provider reference does not match the payment.")
    currency = verification.currency.upper()
    if currency != payment.currency:
        raise InvalidTransition(
            f"Currency mismatch: expected {payment.currency}, received {currency}."
        )
    try:
        amount_kes = from_minor(verification.amount_minor)
    except Exception as exc:
        raise InvalidTransition("Provider amount is invalid.") from exc
    if amount_kes != payment.amount_kes:
        raise InvalidTransition(
            f"Amount mismatch: expected {payment.amount_kes} KES, received {amount_kes} KES."
        )


def mark_payment_succeeded(
    payment: Payment, data: ChargeVerification | Mapping, *, reason: str
) -> Payment:
    """Idempotently mark a payment paid and post the collection journal."""
    verification = _coerce_charge(data)
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.Status.SUCCEEDED:
            return payment
        if payment.status == Payment.Status.REFUNDED:
            raise InvalidTransition("A refunded payment cannot be marked paid again.")
        payment.provider_transaction_id = verification.transaction_id[:64]
        payment.channel = verification.channel[:32]
        payment.gateway_response = verification.gateway_response[:255]
        payment.last_verified_at = timezone.now()
        payment.save(
            update_fields=[
                "provider_transaction_id",
                "channel",
                "gateway_response",
                "last_verified_at",
                "updated_at",
            ]
        )
        payment.transition_to(Payment.Status.SUCCEEDED, reason=reason)

        loan = Loan.objects.select_for_update().get(pk=payment.loan_id)
        if not loan.service_fee_paid:
            loan.service_fee_paid = True
            loan.last_event = "Service fee paid"
            loan.save(update_fields=["service_fee_paid", "last_event", "updated_at"])

        post_journal(
            [
                (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, payment.amount_kes),
                (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.CREDIT, payment.amount_kes),
            ],
            reference=payment.reference,
            memo="Service fee collected",
            loan=loan,
            payment=payment,
        )
    logger.info("Payment %s marked SUCCEEDED", payment.pk)
    return payment


def process_charge_success(
    reference: str, data: ChargeVerification | Mapping, *, verify: bool | None = None
) -> Payment:
    payment = Payment.objects.filter(reference=reference).first()
    if payment is None:
        raise InvalidTransition("No payment matches this provider reference.")
    if verify is None:
        verify = settings.PAYSTACK_VERIFY_ON_WEBHOOK
    verification = (
        _verify_charge_with_provider(payment) if verify else _coerce_charge(data)
    )
    _validate_charge(payment, verification)
    return mark_payment_succeeded(
        payment, verification, reason="Provider confirmed charge"
    )


# ---------------------------------------------------------------------
# Disbursement
# ---------------------------------------------------------------------
def _transfer_reference(loan: Loan) -> str:
    # Stable per loan: a retry reuses the reference so the provider can dedupe.
    return f"LNT_{loan.pk}"


def start_disbursement(loan: Loan, *, actor=None) -> Transfer:
    if not settings.PAYMENTS_ENABLED:
        raise PaymentsDisabled()
    if not loan.can_be_disbursed:
        raise InvalidTransition(
            "This loan is not ready for disbursement (check status, acceptance and expiry)."
        )
    if settings.FEE_COLLECTION_MODE == "upfront" and not loan.service_fee_paid:
        raise InvalidTransition("The service fee has not been paid for this loan.")
    if loan.payments.filter(
        purpose=Payment.Purpose.SERVICE_FEE, status=Payment.Status.REFUNDED
    ).exists():
        raise InvalidTransition("This loan was refunded and cannot be disbursed.")

    existing = Transfer.objects.filter(loan=loan).first()
    if existing and existing.status in {
        Transfer.Status.INITIATING,
        Transfer.Status.PENDING_PROVIDER,
        Transfer.Status.UNKNOWN,
    }:
        # Never start a second transfer while one may be in flight.
        return existing
    if existing and existing.status == Transfer.Status.SUCCESS:
        return existing

    with transaction.atomic():
        transfer = existing
        if transfer is None:
            transfer = Transfer.objects.create(
                loan=loan,
                reference=_transfer_reference(loan),
                idempotency_key=f"transfer:loan:{loan.pk}",
                amount_kes=loan.amount,
                currency="KES",
                status=Transfer.Status.PENDING,
                max_attempts=settings.PAYSTACK_TRANSFER_MAX_ATTEMPTS,
            )
        loan = Loan.objects.select_for_update().get(pk=loan.pk)
        if loan.status == Loan.Status.APPROVED:
            loan.transition_to(
                Loan.Status.DISBURSEMENT_PENDING,
                actor=actor,
                reason="Disbursement requested",
            )
    return execute_transfer(transfer.pk, actor=actor)


def _claim_transfer(transfer_id: int, *, actor=None) -> Transfer | None:
    """Short transaction: claim an attempt or return None when not claimable."""
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer_id)
        if transfer.status in Transfer.TERMINAL:
            return None
        if transfer.status in {Transfer.Status.INITIATING, Transfer.Status.PENDING_PROVIDER}:
            return None
        if transfer.status == Transfer.Status.UNKNOWN:
            return None
        if transfer.attempts >= transfer.max_attempts:
            transfer.transition_to(
                Transfer.Status.FAILED, reason="Maximum attempts reached", actor=actor
            )
            _fail_loan_after_transfer(transfer, "Disbursement attempts exhausted")
            return None
        transfer.attempts += 1
        if transfer.recipient_code:
            transfer.transition_to(
                Transfer.Status.INITIATING,
                reason=f"Attempt {transfer.attempts}",
                actor=actor,
            )
        else:
            transfer.transition_to(
                Transfer.Status.RECIPIENT_CREATED,
                reason="Recipient creation requested",
                actor=actor,
            )
        transfer.last_error = ""
        transfer.save(
            update_fields=["attempts", "last_error", "updated_at", "status"]
        )
        return transfer


def _ensure_recipient(transfer: Transfer) -> Transfer:
    if transfer.recipient_code:
        return transfer
    try:
        recipient = _provider().create_transfer_recipient(
            name=f"Borrower {transfer.loan.user.phone[-4:]}",
            phone=transfer.loan.mpesa_phone,
        )
    except ProviderError as exc:
        _record_transfer_error(transfer, exc)
        raise
    code = recipient.recipient_code
    if not code:
        error = ProviderError("Provider did not return a recipient code.")
        _record_transfer_error(transfer, error)
        raise error
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        transfer.recipient_code = code[:64]
        transfer.save(update_fields=["recipient_code", "updated_at"])
    return transfer


def _mark_initiating(transfer: Transfer) -> Transfer:
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.status == Transfer.Status.RECIPIENT_CREATED:
            transfer.transition_to(Transfer.Status.INITIATING, reason="Transfer requested")
        return transfer


def execute_transfer(transfer_id: int, *, actor=None) -> Transfer:
    """Execute one disbursement attempt.

    Provider calls occur outside transactions. The transfer only becomes
    ``SUCCESS`` from a confirmed provider event/lookup, never from the
    request response alone.
    """
    claimed = _claim_transfer(transfer_id, actor=actor)
    if claimed is None:
        return Transfer.objects.get(pk=transfer_id)

    transfer = _ensure_recipient(claimed)
    if transfer.status == Transfer.Status.UNKNOWN:
        return transfer
    transfer = _mark_initiating(transfer)

    try:
        initiation = _provider().initiate_transfer(
            amount_minor=to_minor(transfer.amount_kes),
            recipient_code=transfer.recipient_code,
            reference=transfer.reference,
            reason=f"Loan disbursement #{transfer.loan_id}",
        )
    except ProviderError as exc:
        _record_transfer_error(transfer, exc)
        raise

    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        transfer.provider_transfer_code = initiation.transfer_code[:64]
        transfer.provider_reference = (initiation.provider_reference or transfer.reference)[:64]
        transfer.initiated_at = timezone.now()
        transfer.save(
            update_fields=[
                "provider_transfer_code",
                "provider_reference",
                "initiated_at",
                "updated_at",
            ]
        )
        # A 2xx from /transfer means "accepted", not "paid".
        if transfer.status == Transfer.Status.INITIATING:
            transfer.transition_to(
                Transfer.Status.PENDING_PROVIDER,
                reason="Accepted by provider; awaiting confirmation",
            )
    logger.info("Transfer %s accepted by provider, awaiting confirmation", transfer.pk)
    return transfer


def _record_transfer_error(transfer: Transfer, exc: ProviderError) -> None:
    """Persist an error with an explicit, recoverable transfer state."""
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        transfer.last_error = str(exc)[:255]
        if exc.uncertain or exc.retryable:
            target = Transfer.Status.UNKNOWN
        else:
            target = Transfer.Status.FAILED
        transfer.next_retry_at = timezone.now() + timedelta(
            seconds=settings.PAYSTACK_TRANSFER_RETRY_BACKOFF_SECONDS
        )
        transfer.save(update_fields=["last_error", "next_retry_at", "updated_at"])
        if transfer.status != target and transfer.can_transition_to(target):
            transfer.transition_to(target, reason=str(exc)[:200])
        if target == Transfer.Status.FAILED and transfer.attempts >= transfer.max_attempts:
            _fail_loan_after_transfer(transfer, "Disbursement failed permanently")
    logger.error(
        "Transfer %s error (%s): %s", transfer.pk, exc.code, str(exc)[:200]
    )


def _fail_loan_after_transfer(transfer: Transfer, reason: str) -> None:
    loan = Loan.objects.select_for_update().get(pk=transfer.loan_id)
    if loan.status == Loan.Status.DISBURSEMENT_PENDING:
        loan.transition_to(Loan.Status.FAILED, reason=reason[:255])
    phone = loan.user.phone
    transaction.on_commit(
        lambda: send_sms(
            phone,
            f"Your {settings.PLATFORM_NAME} disbursement could not be completed. "
            "Our team has been alerted and will contact you.",
            purpose="disbursement_failed",
            user=loan.user,
        )
    )


def confirm_transfer_success(transfer: Transfer, data: dict | None = None) -> Transfer:
    """Provider-confirmed success. Idempotent."""
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.status == Transfer.Status.SUCCESS:
            return transfer
        if transfer.status == Transfer.Status.REVERSED:
            return transfer
        if transfer.status not in {
            Transfer.Status.PENDING_PROVIDER,
            Transfer.Status.UNKNOWN,
            Transfer.Status.INITIATING,
            Transfer.Status.FAILED,
        }:
            raise InvalidTransition(
                f"Transfer in state {transfer.status} cannot be confirmed."
            )
        if data:
            transfer.raw_last_event = data
        transfer.save(update_fields=["raw_last_event", "updated_at"])
        transfer.transition_to(Transfer.Status.SUCCESS, reason="Provider confirmed transfer")

        loan = Loan.objects.select_for_update().get(pk=transfer.loan_id)
        if loan.status != Loan.Status.DISBURSED:
            if loan.status not in {
                Loan.Status.DISBURSEMENT_PENDING,
                Loan.Status.FAILED,
            }:
                raise InvalidTransition(
                    f"Loan in state {loan.status} cannot be marked disbursed."
                )
            if loan.status == Loan.Status.FAILED:
                # Recovery path for a loan marked failed before confirmation.
                loan.transition_to(
                    Loan.Status.DISBURSEMENT_PENDING,
                    reason="Provider confirmed a transfer previously marked failed",
                )
            loan.transition_to(Loan.Status.DISBURSED, reason="Disbursement confirmed")

            post_journal(
                            [
                                (LedgerEntry.Account.LOAN_RECEIVABLE, LedgerEntry.Direction.DEBIT, transfer.amount_kes),
                                (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.CREDIT, transfer.amount_kes),
                            ],
                            reference=transfer.reference,
                            memo="Loan principal disbursed",
                            loan=loan,
                            transfer=transfer,
            )
            # Generate repayment schedule now that loan is disbursed
            transaction.on_commit(
                lambda: generate_schedule(loan)
            )
            phone = loan.user.phone
        amount = transfer.amount_kes
        transaction.on_commit(
            lambda: send_sms(
                phone,
                f"{settings.PLATFORM_NAME}: KES {amount:,} has been sent to your M-Pesa. "
                "Reply to this number if you need help.",
                purpose="disbursed",
                user=loan.user,
            )
        )
    logger.info("Transfer %s confirmed successful", transfer.pk)
    return transfer


def mark_transfer_failed(transfer: Transfer, reason: str, data: dict | None = None) -> Transfer:
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.status in Transfer.TERMINAL:
            return transfer
        if data:
            transfer.raw_last_event = data
        transfer.last_error = (reason or "")[:255]
        transfer.next_retry_at = timezone.now() + timedelta(
            seconds=settings.PAYSTACK_TRANSFER_RETRY_BACKOFF_SECONDS
        )
        transfer.save(
            update_fields=["raw_last_event", "last_error", "next_retry_at", "updated_at"]
        )
        if transfer.can_transition_to(Transfer.Status.FAILED):
            transfer.transition_to(Transfer.Status.FAILED, reason=reason[:200])
        if transfer.attempts >= transfer.max_attempts:
            _fail_loan_after_transfer(transfer, "Disbursement failed permanently")
    return transfer


def mark_transfer_reversed(transfer: Transfer, data: dict | None = None) -> Transfer:
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.status == Transfer.Status.REVERSED:
            return transfer
        transfer.raw_last_event = data
        transfer.save(update_fields=["raw_last_event", "updated_at"])
        was_settled = transfer.status == Transfer.Status.SUCCESS
        transfer.transition_to(Transfer.Status.REVERSED, reason="Provider reversed transfer")
        loan = Loan.objects.select_for_update().get(pk=transfer.loan_id)
        if loan.status == Loan.Status.DISBURSED:
            loan.transition_to(
                Loan.Status.REVERSED, reason="Disbursement reversed by provider"
            )
        elif loan.status == Loan.Status.DISBURSEMENT_PENDING:
            # Reversed before settlement: the borrower never received funds.
            loan.transition_to(
                Loan.Status.FAILED, reason="Disbursement reversed by provider"
            )
        if was_settled and loan.status == Loan.Status.REVERSED:
            post_journal(
                [
                    (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, transfer.amount_kes),
                    (LedgerEntry.Account.LOAN_RECEIVABLE, LedgerEntry.Direction.CREDIT, transfer.amount_kes),
                ],
                reference=f"REV_{transfer.reference}"[:64],
                memo="Disbursement reversed (compensating entry)",
                loan=loan,
                transfer=transfer,
            )
    logger.warning("Transfer %s reversed", transfer.pk)
    return transfer


def retry_disbursement(loan: Loan, *, actor=None) -> Transfer:
    transfer = Transfer.objects.filter(loan=loan).first()
    if transfer is None:
        return start_disbursement(loan, actor=actor)
    if transfer.status in {Transfer.Status.SUCCESS, Transfer.Status.REVERSED}:
        return transfer
    if transfer.status == Transfer.Status.INITIATING:
        raise InvalidTransition(
            "A disbursement request is currently in flight. Wait for it to settle."
        )
    if transfer.status in {Transfer.Status.PENDING_PROVIDER, Transfer.Status.UNKNOWN}:
        # Reconcile with the provider before any retry that could double-pay.
        transfer = refresh_transfer_from_provider(transfer, actor=actor)
        if transfer.status in {
            Transfer.Status.SUCCESS,
            Transfer.Status.PENDING_PROVIDER,
            Transfer.Status.REVERSED,
        }:
            return transfer
    return execute_transfer(transfer.pk, actor=actor)


def refresh_transfer_from_provider(transfer: Transfer, *, actor=None) -> Transfer:
    """Ask the provider for the authoritative state of a transfer."""
    provider = _provider()
    try:
        if transfer.provider_transfer_code:
            state: TransferState | None = provider.get_transfer(
                transfer.provider_transfer_code
            )
        else:
            # The transfer reference is deterministic per loan, so a request
            # whose acknowledgement never arrived (timeout) can still be
            # reconciled by looking the provider up for that reference.
            state = provider.find_transfer_by_reference(transfer.reference)
    except ProviderError as exc:
        logger.error("Transfer lookup failed for %s: %s", transfer.pk, exc.code)
        return transfer

    if state is None:
        return transfer
    if state.status is TransferStatus.SUCCESS:
        return confirm_transfer_success(transfer, dict(state.raw))
    if state.status is TransferStatus.FAILED:
        return mark_transfer_failed(
            transfer, state.reason or "Provider reports failed", dict(state.raw)
        )
    if state.status is TransferStatus.REVERSED:
        return mark_transfer_reversed(transfer, dict(state.raw))
    # PENDING: accepted but not settled; keep awaiting confirmation.
    with transaction.atomic():
        transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.status == Transfer.Status.UNKNOWN and transfer.can_transition_to(
            Transfer.Status.PENDING_PROVIDER
        ):
            transfer.transition_to(
                Transfer.Status.PENDING_PROVIDER,
                reason="Provider still processing",
                actor=actor,
            )
    return transfer


# ---------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------
def request_refund(
    payment: Payment, *, amount_kes: int | None = None, reason: str, actor=None
) -> Refund:
    if payment.status not in {Payment.Status.SUCCEEDED, Payment.Status.REFUND_PENDING}:
        raise InvalidTransition("Only a successful payment can be refunded.")
    if amount_kes is None:
        amount_kes = payment.amount_kes
    if amount_kes <= 0 or amount_kes > payment.amount_kes:
        raise InvalidTransition("Refund amount must be between 1 and the payment amount.")

    existing = payment.refunds.filter(
        status__in=[Refund.Status.PENDING, Refund.Status.PROCESSING, Refund.Status.SUCCEEDED]
    ).first()
    if existing:
        return existing

    refund = Refund.objects.create(
        payment=payment,
        loan=payment.loan,
        reference=f"RFD_{payment.pk}_{secrets.token_hex(4)}",
        amount_kes=amount_kes,
        currency=payment.currency,
        reason=reason[:255] or "Customer refund",
        status=Refund.Status.PENDING,
        requested_by=actor,
    )

    try:
        outcome = _provider().refund(
            transaction_reference=payment.reference,
            amount_minor=to_minor(amount_kes),
            reason=reason[:255],
        )
    except ProviderError as exc:
        refund.status = Refund.Status.FAILED
        refund.provider_response = {"error": exc.code}
        refund.save(update_fields=["status", "provider_response", "updated_at"])
        raise

    refund.provider_refund_id = outcome.refund_id[:64]
    refund.provider_response = dict(outcome.raw)
    if outcome.status is RefundStatus.SETTLED:
        refund.status = Refund.Status.SUCCEEDED
    elif outcome.status is RefundStatus.FAILED:
        refund.status = Refund.Status.FAILED
    else:
        refund.status = Refund.Status.PROCESSING
    refund.save(update_fields=["provider_refund_id", "provider_response", "status", "updated_at"])

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.can_transition_to(Payment.Status.REFUND_PENDING):
            payment.transition_to(
                Payment.Status.REFUND_PENDING, reason="Refund requested", actor=actor
            )
    if refund.status == Refund.Status.SUCCEEDED:
        complete_refund(refund)
    return refund


def complete_refund(refund: Refund) -> Refund:
    """Mark a provider-confirmed refund complete and post compensating entries."""
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.status == Refund.Status.SUCCEEDED and refund.processed_at:
            return refund
        refund.status = Refund.Status.SUCCEEDED
        refund.processed_at = timezone.now()
        refund.save(update_fields=["status", "processed_at", "updated_at"])
        payment = Payment.objects.select_for_update().get(pk=refund.payment_id)
        if payment.can_transition_to(Payment.Status.REFUNDED):
            payment.transition_to(
                Payment.Status.REFUNDED, reason="Refund completed", actor=refund.requested_by
            )
        post_journal(
            [
                (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.DEBIT, refund.amount_kes),
                (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.CREDIT, refund.amount_kes),
            ],
            reference=refund.reference,
            memo=f"Refund: {refund.reason}"[:255],
            loan=refund.loan,
            payment=payment,
            refund=refund,
            created_by=refund.requested_by,
        )
    logger.info("Refund %s completed", refund.pk)
    return refund


def fail_refund(refund: Refund, reason: str) -> Refund:
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.status == Refund.Status.SUCCEEDED:
            return refund
        refund.status = Refund.Status.FAILED
        refund.provider_response = {**(refund.provider_response or {}), "failure": reason[:200]}
        refund.save(update_fields=["status", "provider_response", "updated_at"])
        payment = Payment.objects.select_for_update().get(pk=refund.payment_id)
        if payment.status == Payment.Status.REFUND_PENDING:
            payment.transition_to(
                Payment.Status.SUCCEEDED, reason="Refund failed; payment remains collected"
            )
    return refund


# ---------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------
def record_webhook_event(*, event_id: str, event_type: str, payload: dict, reference: str = ""):
    """Store a delivery exactly once. Returns ``(event, created)``."""
    event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "provider": settings.PAYMENT_PROVIDER,
            "event_type": event_type[:64],
            "reference": (reference or "")[:64],
            "payload": payload,
        },
    )
    return event, created


def process_webhook_event(event: WebhookEvent) -> str:
    """Dispatch a stored event. Idempotent: re-running is safe."""
    if event.status in {WebhookEvent.Status.PROCESSED, WebhookEvent.Status.IGNORED}:
        return "duplicate"
    event.attempts += 1
    try:
        outcome = _dispatch_event(event)
        event.status = (
            WebhookEvent.Status.PROCESSED
            if outcome == "processed"
            else WebhookEvent.Status.IGNORED
        )
        event.processed_at = timezone.now()
        event.error = ""
        event.save(update_fields=["attempts", "status", "processed_at", "error"])
        return outcome
    except Exception as exc:  # retained for manual replay
        event.status = WebhookEvent.Status.FAILED
        event.error = str(exc)[:500]
        event.save(update_fields=["attempts", "status", "error"])
        raise


def _dispatch_event(event: WebhookEvent) -> str:
    envelope = _provider().classify_webhook(
        event.payload if isinstance(event.payload, Mapping) else {}
    )
    data = envelope.data
    reference = envelope.reference

    if envelope.kind is WebhookEventKind.CHARGE_SUCCESS:
            payment = process_charge_success(reference, data)
            # If this was a repayment payment, allocate it to schedules
            if payment.purpose == Payment.Purpose.REPAYMENT:
                from repayments.services import process_repayment_webhook
                process_repayment_webhook(payment)
            return "processed"

    if envelope.kind in {
        WebhookEventKind.TRANSFER_SUCCESS,
        WebhookEventKind.TRANSFER_FAILED,
        WebhookEventKind.TRANSFER_REVERSED,
    }:
        transfer = (
            Transfer.objects.filter(provider_reference=reference).first()
            or Transfer.objects.filter(reference=reference).first()
            or Transfer.objects.filter(
                provider_transfer_code=str(data.get("transfer_code") or "")
            ).first()
        )
        if transfer is None:
            raise InvalidTransition(f"No transfer matches reference {reference!r}.")
        if envelope.kind is WebhookEventKind.TRANSFER_SUCCESS:
            confirm_transfer_success(transfer, dict(data))
        elif envelope.kind is WebhookEventKind.TRANSFER_FAILED:
            mark_transfer_failed(
                transfer, str(data.get("reason") or "Provider failed transfer"), dict(data)
            )
        else:
            mark_transfer_reversed(transfer, dict(data))
        return "processed"

    if envelope.kind in {
        WebhookEventKind.REFUND_PROCESSED,
        WebhookEventKind.REFUND_FAILED,
    }:
        refund = (
            Refund.objects.filter(provider_refund_id=str(data.get("id") or "")).first()
            or Refund.objects.filter(reference=reference).first()
        )
        if refund is None:
            raise InvalidTransition("No refund matches this event.")
        if envelope.kind is WebhookEventKind.REFUND_PROCESSED:
            complete_refund(refund)
        else:
            fail_refund(refund, str(data.get("reason") or "Provider failed refund"))
        return "processed"

    return "ignored"


# ---------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------
def run_reconciliation(*, dry_run: bool = True, actor=None) -> ReconciliationRun:
    """Compare internal state with provider state. Never moves money in dry-run."""
    run = ReconciliationRun.objects.create(dry_run=dry_run)
    issues: list[dict] = []
    summary = {
        "stale_payments_checked": 0,
        "in_flight_transfers_checked": 0,
        "paid_not_disbursed": 0,
        "ledger_imbalances": 0,
        "duplicate_payments": 0,
    }
    provider: PaymentProvider | None = None
    try:
        if settings.PAYMENTS_ENABLED:
            provider = get_payment_provider()

        # 1. Payments marked paid but the loan never progressed.
        paid_not_disbursed = Loan.objects.filter(
            service_fee_paid=True, status=Loan.Status.APPROVED
        ).select_related("user")
        summary["paid_not_disbursed"] = paid_not_disbursed.count()
        for loan in paid_not_disbursed[:100]:
            issues.append(
                {
                    "type": "paid_not_disbursed",
                    "loan_id": loan.pk,
                    "user_id": loan.user_id,
                    "status": loan.status,
                    "amount": loan.amount,
                }
            )

        # 2. In-flight or unknown transfers: query the provider.
        transfers = Transfer.objects.filter(
            status__in=[
                Transfer.Status.PENDING_PROVIDER,
                Transfer.Status.UNKNOWN,
                Transfer.Status.INITIATING,
                Transfer.Status.FAILED,
            ]
        )[:100]
        summary["in_flight_transfers_checked"] = transfers.count()
        for transfer in transfers:
            provider_state: TransferState | None = None
            if provider is not None and transfer.provider_transfer_code:
                try:
                    provider_state = provider.get_transfer(transfer.provider_transfer_code)
                except ProviderError as exc:
                    issues.append(
                        {
                            "type": "transfer_lookup_failed",
                            "transfer_id": transfer.pk,
                            "error": exc.code,
                        }
                    )
                    continue
            if provider_state is not None and provider_state.status is TransferStatus.SUCCESS and not dry_run:
                confirm_transfer_success(transfer, dict(provider_state.raw))
                issues.append({"type": "transfer_confirmed", "transfer_id": transfer.pk})
            elif provider_state is not None and provider_state.status is TransferStatus.SUCCESS:
                issues.append(
                    {"type": "transfer_should_be_success", "transfer_id": transfer.pk}
                )
            elif provider_state is not None and provider_state.status is TransferStatus.FAILED and not dry_run:
                mark_transfer_failed(
                    transfer, "Reconciliation: provider failed", dict(provider_state.raw)
                )
                issues.append({"type": "transfer_failed", "transfer_id": transfer.pk})
            elif transfer.status == Transfer.Status.FAILED and not transfer.raw_last_event:
                issues.append({"type": "transfer_failed_unconfirmed", "transfer_id": transfer.pk})

        # 3. Ledger imbalance check.
        groups = (
            LedgerEntry.objects.values("entry_group")
            .annotate(
                debit=Sum("amount_kes", filter=models_q("DEBIT")),
                credit=Sum("amount_kes", filter=models_q("CREDIT")),
            )
        )
        for group in groups:
            if int(group["debit"] or 0) != int(group["credit"] or 0):
                summary["ledger_imbalances"] += 1
                issues.append(
                    {
                        "type": "ledger_imbalance",
                        "entry_group": str(group["entry_group"]),
                        "debit": group["debit"],
                        "credit": group["credit"],
                    }
                )

        # 4. Duplicate successful payments for the same loan.
        from django.db.models import Count

        duplicates = (
            Payment.objects.filter(
                status=Payment.Status.SUCCEEDED,
                purpose=Payment.Purpose.SERVICE_FEE,
            )
            .values("loan_id")
            .annotate(count=Count("id"))
        )
        for row in duplicates:
            if (row["count"] or 0) > 1:
                summary["duplicate_payments"] += 1
                issues.append(
                    {"type": "duplicate_successful_payment", "loan_id": row["loan_id"]}
                )

        run.status = ReconciliationRun.Status.COMPLETED
        run.summary = summary
        run.issues = issues
        run.finished_at = timezone.now()
        run.save()
        logger.info(
            "Reconciliation run %s completed dry_run=%s issues=%s",
            run.pk,
            dry_run,
            len(issues),
        )
        return run
    except Exception as exc:
        run.status = ReconciliationRun.Status.FAILED
        run.error = str(exc)[:500]
        run.summary = summary
        run.issues = issues
        run.finished_at = timezone.now()
        run.save()
        logger.exception("Reconciliation run %s failed", run.pk)
        raise



# ---------------------------------------------------------------------
# Repayment payment initialization
# ---------------------------------------------------------------------
def initialize_repayment_payment(loan: Loan, schedule: "RepaymentSchedule", *, actor=None) -> Payment:
    """Create a Paystack checkout for a repayment installment.

    The amount is the outstanding balance on the schedule (including any late fees).
    """
    if not settings.PAYMENTS_ENABLED:
        raise PaymentsDisabled()

    if loan.status != Loan.Status.DISBURSED:
        raise InvalidTransition("Repayment only available for disbursed loans.")

    if schedule.loan_id != loan.pk:
        raise InvalidTransition("Schedule does not belong to this loan.")

    if not schedule.can_allocate(schedule.outstanding_kes):
        raise InvalidTransition("This installment cannot accept payment.")

    # Check for existing pending payment for this schedule
    existing = Payment.objects.filter(
        loan=loan,
        purpose=Payment.Purpose.REPAYMENT,
        status__in=[Payment.Status.PENDING, Payment.Status.INITIALIZED],
    ).first()
    if existing:
        return existing

    # Create new payment for the outstanding amount
    reference = f"RPT_{loan.pk}_{schedule.installment_number}_{secrets.token_hex(4)}"
    payment = Payment.objects.create(
        loan=loan,
        user=loan.user,
        purpose=Payment.Purpose.REPAYMENT,
        reference=reference,
        idempotency_key=f"repay:loan:{loan.pk}:schedule:{schedule.installment_number}",
        amount_kes=schedule.outstanding_kes,
        currency="KES",
        status=Payment.Status.PENDING,
        provider=settings.PAYMENT_PROVIDER,
    )

    checkout = _provider().initialize(
        email=internal_email(loan.user.phone),
        amount_minor=to_minor(payment.amount_kes),
        reference=payment.reference,
        currency=payment.currency,
        metadata={
            "loan_id": loan.pk,
            "schedule_id": schedule.pk,
            "installment_number": schedule.installment_number,
            "purpose": "repayment",
            "user_id": loan.user_id,
            "provider": "mpesa",
        },
        channels=["mobile_money"],
    )

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        payment.provider_reference = (checkout.provider_reference or payment.reference)[:64]
        payment.authorization_url = checkout.authorization_url
        payment.access_code = checkout.access_code or None
        payment.save(
            update_fields=[
                "provider_reference",
                "authorization_url",
                "access_code",
                "updated_at",
            ]
        )
        if payment.status == Payment.Status.PENDING:
            payment.transition_to(
                Payment.Status.INITIALIZED, reason="Checkout created", actor=actor
            )

    logger.info("Repayment payment %s initialised for loan %s schedule %s",
                payment.pk, loan.pk, schedule.installment_number)
    return payment
