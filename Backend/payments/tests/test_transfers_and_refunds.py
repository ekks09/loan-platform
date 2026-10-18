from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from core.errors import InvalidTransition, PaymentsDisabled
from core.tests.helpers import (
    DictMock,
    make_loan,
    make_staff,
    make_verified_user,
    patched_provider,
)
from loans.models import Loan
from payments.models import LedgerEntry, Payment, Refund, Transfer
from payments.paystack import ProviderError
from payments.services import (
    complete_refund,
    confirm_transfer_success,
    fail_refund,
    get_or_create_fee_payment,
    journal_balance,
    mark_payment_succeeded,
    mark_transfer_failed,
    mark_transfer_reversed,
    request_refund,
    retry_disbursement,
    start_disbursement,
)


def make_disbursable_loan():
    user = make_verified_user()
    loan = make_loan(user, accept=True)
    return user, loan


def ack_provider(*, transfer_code: str = "TRF_1", recipient: str = "RCP_1"):
    return patched_provider(
        create_transfer_recipient={"recipient_code": recipient},
        initiate_transfer={
            "transfer_code": transfer_code,
            "reference": "LNT_1",
            "status": "pending",
        },
    )


class DisbursementTests(TestCase):
    def setUp(self):
        self.user, self.loan = make_disbursable_loan()
        self.staff = make_staff()

    def test_accepted_request_is_pending_provider_not_success(self):
        with ack_provider() as client:
            transfer = start_disbursement(self.loan, actor=self.staff)

        self.loan.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DISBURSEMENT_PENDING)
        self.assertEqual(transfer.status, Transfer.Status.PENDING_PROVIDER)
        self.assertEqual(transfer.attempts, 1)
        self.assertEqual(transfer.recipient_code, "RCP_1")
        self.assertEqual(transfer.provider_transfer_code, "TRF_1")
        self.assertEqual(transfer.reference, f"LNT_{self.loan.pk}")
        self.assertEqual(transfer.amount_kes, self.loan.amount)
        self.assertIsNone(transfer.confirmed_at)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        client.create_transfer_recipient.assert_called_once()

    def test_second_request_does_not_start_a_second_transfer(self):
        with ack_provider() as client:
            first = start_disbursement(self.loan, actor=self.staff)
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_2"},
            initiate_transfer={"transfer_code": "TRF_2", "reference": "LNT_1"},
        ) as second_client:
            second = start_disbursement(self.loan, actor=self.staff)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.status, Transfer.Status.PENDING_PROVIDER)
        second_client.initiate_transfer.assert_not_called()
        self.assertEqual(Transfer.objects.filter(loan=self.loan).count(), 1)

    def test_provider_confirmation_marks_loan_disbursed_and_posts_ledger(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)

        confirmed = confirm_transfer_success(transfer)
        confirmed.refresh_from_db()
        self.loan.refresh_from_db()

        self.assertEqual(confirmed.status, Transfer.Status.SUCCESS)
        self.assertIsNotNone(confirmed.confirmed_at)
        self.assertEqual(self.loan.status, Loan.Status.DISBURSED)
        self.assertIsNotNone(self.loan.disbursed_at)

        group = LedgerEntry.objects.filter(loan=self.loan).first().entry_group
        self.assertEqual(journal_balance(group), 0)
        self.assertEqual(LedgerEntry.objects.filter(loan=self.loan).count(), 2)

        # Idempotent replay.
        again = confirm_transfer_success(confirmed)
        self.assertEqual(again.status, Transfer.Status.SUCCESS)
        self.assertEqual(LedgerEntry.objects.filter(loan=self.loan).count(), 2)

    def test_timeout_becomes_unknown_and_is_reconciled_before_retry(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer=ProviderError(
                "provider timeout", retryable=True, uncertain=True
            ),
        ):
            with self.assertRaises(ProviderError):
                start_disbursement(self.loan, actor=self.staff)

        transfer = Transfer.objects.get(loan=self.loan)
        self.assertEqual(transfer.status, Transfer.Status.UNKNOWN)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DISBURSEMENT_PENDING)

        # The retry must ask the provider what happened instead of sending a
        # second transfer that could double-pay the borrower.
        with patched_provider(
            list_transfers=[{"status": "success", "reference": "LNT_1", "id": 7}],
        ) as client:
            result = retry_disbursement(self.loan, actor=self.staff)

        self.assertEqual(result.status, Transfer.Status.SUCCESS)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DISBURSED)
        client.initiate_transfer.assert_not_called()

    def test_retry_waits_while_the_provider_still_reports_processing(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer=ProviderError(
                "timeout", retryable=True, uncertain=True
            ),
        ):
            with self.assertRaises(ProviderError):
                start_disbursement(self.loan, actor=self.staff)

        with patched_provider(list_transfers=[{"status": "pending"}]):
            result = retry_disbursement(self.loan, actor=self.staff)

        self.assertEqual(result.status, Transfer.Status.PENDING_PROVIDER)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_retry_is_refused_while_a_request_is_in_flight(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)
        Transfer.objects.filter(pk=transfer.pk).update(status=Transfer.Status.INITIATING)
        transfer.refresh_from_db()
        with self.assertRaises(InvalidTransition):
            retry_disbursement(self.loan, actor=self.staff)

    def test_confirmed_failure_can_be_retried_and_then_succeed(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)
        mark_transfer_failed(transfer, "insufficient provider balance")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.Status.FAILED)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DISBURSEMENT_PENDING)

        with ack_provider(transfer_code="TRF_2"):
            retried = retry_disbursement(self.loan, actor=self.staff)
        self.assertEqual(retried.status, Transfer.Status.PENDING_PROVIDER)
        self.assertEqual(retried.attempts, 2)

    def test_exhausted_attempts_fail_the_loan_and_late_success_recovers_it(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)
        Transfer.objects.filter(pk=transfer.pk).update(
            attempts=transfer.max_attempts
        )
        transfer.refresh_from_db()
        mark_transfer_failed(transfer, "provider rejected")
        transfer.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.Status.FAILED)
        self.assertEqual(self.loan.status, Loan.Status.FAILED)

        confirmed = confirm_transfer_success(transfer, {"status": "success"})
        self.assertEqual(confirmed.status, Transfer.Status.SUCCESS)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DISBURSED)

    def test_reversal_after_settlement_reverses_the_loan_and_compensates(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)
        confirm_transfer_success(transfer)
        transfer.refresh_from_db()

        reversed_transfer = mark_transfer_reversed(transfer, {"status": "reversed"})
        reversed_transfer.refresh_from_db()
        self.loan.refresh_from_db()

        self.assertEqual(reversed_transfer.status, Transfer.Status.REVERSED)
        self.assertEqual(self.loan.status, Loan.Status.REVERSED)

        groups = {
            entry.entry_group
            for entry in LedgerEntry.objects.filter(loan=self.loan)
        }
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(journal_balance(group), 0)
        self.assertEqual(LedgerEntry.objects.filter(loan=self.loan).count(), 4)

        again = mark_transfer_reversed(reversed_transfer)
        self.assertEqual(again.status, Transfer.Status.REVERSED)
        self.assertEqual(LedgerEntry.objects.filter(loan=self.loan).count(), 4)

    def test_reversal_before_settlement_marks_the_loan_failed_without_a_journal(self):
        with ack_provider():
            transfer = start_disbursement(self.loan, actor=self.staff)

        reversed_transfer = mark_transfer_reversed(transfer)
        self.loan.refresh_from_db()
        self.assertEqual(reversed_transfer.status, Transfer.Status.REVERSED)
        self.assertEqual(self.loan.status, Loan.Status.FAILED)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    @override_settings(PAYMENTS_ENABLED=False)
    def test_disbursement_requires_payments_to_be_enabled(self):
        with self.assertRaises(PaymentsDisabled):
            start_disbursement(self.loan, actor=self.staff)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_upfront_fee_must_be_paid_before_disbursement(self):
        with self.assertRaises(InvalidTransition):
            start_disbursement(self.loan, actor=self.staff)

    def test_refunded_loan_cannot_be_disbursed(self):
        payment = get_or_create_fee_payment(self.loan)
        payment.transition_to(Payment.Status.SUCCEEDED, reason="test")
        payment.transition_to(Payment.Status.REFUND_PENDING, reason="test")
        payment.transition_to(Payment.Status.REFUNDED, reason="test")
        with self.assertRaises(InvalidTransition):
            start_disbursement(self.loan, actor=self.staff)


class RefundTests(TestCase):
    def setUp(self):
        self.user, self.loan = make_disbursable_loan()
        self.staff = make_staff()
        self.payment = get_or_create_fee_payment(self.loan)
        mark_payment_succeeded(
            self.payment, DictMock.charge(self.payment.reference, self.payment.amount_kes), reason="test"
        )
        self.payment.refresh_from_db()

    def test_full_refund_completes_and_posts_compensating_entry(self):
        with patched_provider(
            create_refund={"id": 55, "status": "processed", "amount": 120000}
        ):
            refund = request_refund(
                self.payment, reason="Could not disburse", actor=self.staff
            )

        refund.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.SUCCEEDED)
        self.assertIsNotNone(refund.processed_at)
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)

        refund_entries = LedgerEntry.objects.filter(refund=refund)
        self.assertEqual(refund_entries.count(), 2)
        self.assertEqual(journal_balance(refund_entries.first().entry_group), 0)

        # Completing again is a no-op.
        complete_refund(refund)
        self.assertEqual(LedgerEntry.objects.filter(refund=refund).count(), 2)

    def test_provider_refund_pending_leaves_payment_refund_pending(self):
        with patched_provider(
            create_refund={"id": 56, "status": "pending"}
        ):
            refund = request_refund(self.payment, reason="test", actor=self.staff)
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.PROCESSING)
        self.assertEqual(self.payment.status, Payment.Status.REFUND_PENDING)

        complete_refund(refund)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)

    def test_provider_failure_records_a_failed_refund_and_keeps_the_payment(self):
        with patched_provider(
            create_refund=ProviderError("rejected", retryable=False)
        ):
            with self.assertRaises(ProviderError):
                request_refund(self.payment, reason="test", actor=self.staff)

        refund = Refund.objects.get(payment=self.payment)
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.FAILED)
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_refund_amount_is_bounded(self):
        for amount in (0, self.payment.amount_kes + 1):
            with self.assertRaises(InvalidTransition):
                request_refund(
                    self.payment, amount_kes=amount, reason="test", actor=self.staff
                )

    def test_only_successful_payments_can_be_refunded(self):
        payment = get_or_create_fee_payment(
            make_loan(make_verified_user(phone="0744444444", national_id="44444444"), accept=True)
        )
        with self.assertRaises(InvalidTransition):
            request_refund(payment, reason="test", actor=self.staff)

    def test_existing_refund_is_returned_instead_of_duplicated(self):
        with patched_provider(create_refund={"id": 57, "status": "pending"}):
            first = request_refund(self.payment, reason="test", actor=self.staff)
        second = request_refund(self.payment, reason="again", actor=self.staff)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Refund.objects.filter(payment=self.payment).count(), 1)

    def test_failed_refund_restores_the_successful_payment(self):
        with patched_provider(create_refund={"id": 58, "status": "pending"}):
            refund = request_refund(self.payment, reason="test", actor=self.staff)
        fail_refund(refund, "provider declined")
        refund.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.FAILED)
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
