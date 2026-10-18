from __future__ import annotations

from django.test import TestCase

from core.tests.helpers import (
    DictMock,
    make_loan,
    make_staff,
    make_verified_user,
    patched_provider,
)
from loans.models import Loan
from payments.models import (
    LedgerEntry,
    Payment,
    ReconciliationRun,
    Transfer,
)
from payments.services import (
    confirm_transfer_success,
    get_or_create_fee_payment,
    mark_payment_succeeded,
    run_reconciliation,
    start_disbursement,
)


class ReconciliationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.staff = make_staff()

    def test_clean_state_reports_no_issues(self):
        run = run_reconciliation(dry_run=True)
        self.assertEqual(run.status, ReconciliationRun.Status.COMPLETED)
        self.assertTrue(run.dry_run)
        self.assertEqual(run.issues, [])
        self.assertEqual(run.summary["ledger_imbalances"], 0)

    def test_paid_but_not_disbursed_is_flagged(self):
        payment = get_or_create_fee_payment(self.loan)
        mark_payment_succeeded(
            payment, DictMock.charge(payment.reference, payment.amount_kes), reason="test"
        )
        run = run_reconciliation(dry_run=True)
        self.assertEqual(run.summary["paid_not_disbursed"], 1)
        self.assertIn(
            "paid_not_disbursed", {issue["type"] for issue in run.issues}
        )

    def test_ledger_imbalance_is_detected(self):
        LedgerEntry.objects.create(
            account=LedgerEntry.Account.PLATFORM_REVENUE,
            direction=LedgerEntry.Direction.CREDIT,
            amount_kes=500,
            reference="unbalanced",
        )
        run = run_reconciliation(dry_run=True)
        self.assertEqual(run.summary["ledger_imbalances"], 1)

    def test_duplicate_successful_fee_payments_are_detected(self):
        first = get_or_create_fee_payment(self.loan)
        mark_payment_succeeded(
            first, DictMock.charge(first.reference, first.amount_kes), reason="test"
        )
        Payment.objects.create(
            loan=self.loan,
            user=self.user,
            purpose=Payment.Purpose.SERVICE_FEE,
            reference="LPF_duplicate",
            idempotency_key="fee:duplicate",
            amount_kes=first.amount_kes,
            status=Payment.Status.SUCCEEDED,
        )
        run = run_reconciliation(dry_run=True)
        self.assertEqual(run.summary["duplicate_payments"], 1)

    def test_dry_run_does_not_confirm_a_provider_success(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_1", "reference": "LNT_1"},
        ):
            transfer = start_disbursement(self.loan, actor=self.staff)

        with patched_provider(
            get_transfer={"status": "success", "id": 1, "reference": "LNT_1"}
        ):
            run = run_reconciliation(dry_run=True)

        transfer.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.Status.PENDING_PROVIDER)
        self.assertEqual(self.loan.status, Loan.Status.DISBURSEMENT_PENDING)
        self.assertIn(
            "transfer_should_be_success", {issue["type"] for issue in run.issues}
        )

    def test_apply_confirms_provider_success_and_marks_the_loan_disbursed(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_1", "reference": "LNT_1"},
        ):
            transfer = start_disbursement(self.loan, actor=self.staff)

        with patched_provider(
            get_transfer={"status": "success", "id": 1, "reference": "LNT_1"}
        ):
            run = run_reconciliation(dry_run=False)

        transfer.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.Status.SUCCESS)
        self.assertEqual(self.loan.status, Loan.Status.DISBURSED)
        self.assertIn(
            "transfer_confirmed", {issue["type"] for issue in run.issues}
        )

    def test_provider_lookup_failure_is_recorded_not_fatal(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_1", "reference": "LNT_1"},
        ):
            start_disbursement(self.loan, actor=self.staff)

        from payments.paystack import ProviderError

        with patched_provider(get_transfer=ProviderError("unavailable", retryable=True)):
            run = run_reconciliation(dry_run=True)

        self.assertEqual(run.status, ReconciliationRun.Status.COMPLETED)
        self.assertIn(
            "transfer_lookup_failed", {issue["type"] for issue in run.issues}
        )
