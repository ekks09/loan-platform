from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.tests.helpers import (
    expire_loan_offer,
    make_loan,
    make_staff,
    make_verified_user,
    patched_provider,
)
from loans.models import Loan
from payments.models import Transfer, WebhookEvent
from payments.services import start_disbursement
from users.models import User


class OfferExpiryCommandTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, approve_offer=True)
        expire_loan_offer(self.loan)

    def test_dry_run_is_the_default(self):
        out = StringIO()
        call_command("expire_offers", stdout=out)
        self.assertIn("would expire 1", out.getvalue())
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)

    def test_apply_expires_offers(self):
        out = StringIO()
        call_command("expire_offers", "--apply", stdout=out)
        self.assertIn("expired 1", out.getvalue())
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.EXPIRED)


class ReconcilePaymentsCommandTests(TestCase):
    def test_json_output_is_machine_readable(self):
        out = StringIO()
        call_command("reconcile_payments", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertTrue(payload["dry_run"])
        self.assertIn("summary", payload)


class RetryDisbursementsCommandTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.staff = make_staff()
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_1", "reference": "LNT_1"},
        ):
            self.transfer = start_disbursement(self.loan, actor=self.staff)
        Transfer.objects.filter(pk=self.transfer.pk).update(
            status=Transfer.Status.FAILED,
            next_retry_at=timezone.now() - timedelta(minutes=1),
            attempts=1,
        )

    def test_dry_run_lists_due_retries_without_calling_the_provider(self):
        out = StringIO()
        call_command("retry_disbursements", stdout=out)
        self.assertIn("would retry 1", out.getvalue())
        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, Transfer.Status.FAILED)

    def test_apply_retries_the_transfer(self):
        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_2", "reference": "LNT_1"},
        ):
            out = StringIO()
            call_command("retry_disbursements", "--apply", stdout=out)
        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, Transfer.Status.PENDING_PROVIDER)
        self.assertEqual(self.transfer.attempts, 2)
        self.assertIn("processed 1", out.getvalue())


class ReplayWebhooksCommandTests(TestCase):
    def test_dry_run_lists_failed_events(self):
        event = WebhookEvent.objects.create(
            event_id="abc123",
            event_type="charge.success",
            payload={},
            status=WebhookEvent.Status.FAILED,
        )
        out = StringIO()
        call_command("replay_webhooks", "--dry-run", stdout=out)
        self.assertIn(f"would replay {event.pk}", out.getvalue())


class PurgeDocumentsCommandTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        User.objects.filter(pk=self.user.pk).update(
            documents_uploaded_at=timezone.now() - timedelta(days=400)
        )

    @patch("core.management.commands.purge_documents.SupabaseStorage.delete_file")
    def test_dry_run_deletes_nothing(self, delete_file):
        out = StringIO()
        call_command("purge_documents", "--days", "90", stdout=out)
        self.assertIn("would purge documents for 1", out.getvalue())
        delete_file.assert_not_called()
        self.user.refresh_from_db()
        self.assertTrue(self.user.id_front_path)

    @patch("core.management.commands.purge_documents.SupabaseStorage.delete_file")
    def test_apply_clears_paths_but_keeps_the_decision_record(self, delete_file):
        out = StringIO()
        call_command("purge_documents", "--days", "90", "--apply", stdout=out)
        self.user.refresh_from_db()
        self.assertEqual(self.user.id_front_path, "")
        self.assertEqual(self.user.id_back_path, "")
        self.assertEqual(self.user.selfie_path, "")
        self.assertEqual(self.user.verification_status, User.VerificationStatus.VERIFIED)
        self.assertEqual(delete_file.call_count, 3)

    @patch("core.management.commands.purge_documents.SupabaseStorage.delete_file")
    def test_users_with_an_open_loan_are_skipped(self, delete_file):
        # The setUp user now has an open loan and must retain their documents.
        make_loan(self.user)

        other = make_verified_user(phone="0722222222", national_id="22222222")
        User.objects.filter(pk=other.pk).update(
            documents_uploaded_at=timezone.now() - timedelta(days=400)
        )

        out = StringIO()
        call_command("purge_documents", "--days", "90", "--apply", stdout=out)

        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(self.user.id_front_path)
        self.assertEqual(other.id_front_path, "")
        self.assertEqual(delete_file.call_count, 3)
