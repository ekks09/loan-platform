"""Retention job for identity documents.

Deletes stored document objects for users whose verification decision is older
than the configured retention period *and* who do not have an open loan. The
audit metadata (paths, decision, timestamps) is retained.

Runs as a dry-run unless ``--apply`` is passed. Legal retention obligations may
differ; adjust ``DOCUMENT_RETENTION_DAYS`` only with compliance sign-off.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from loans.models import Loan
from users.models import User
from users.storage import SupabaseStorage


class Command(BaseCommand):
    help = "Delete stored identity documents past the retention period."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--days", type=int, default=settings.DOCUMENT_RETENTION_DAYS)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        candidates = User.objects.filter(
            verification_status__in=[
                User.VerificationStatus.VERIFIED,
                User.VerificationStatus.REJECTED,
            ]
        ).exclude(id_front_path="").filter(
            documents_uploaded_at__lt=cutoff
        )[:200]

        deleted = 0
        for user in candidates:
            has_open_loan = Loan.objects.filter(
                user=user, status__in=Loan.OPEN_STATUSES
            ).exists()
            if has_open_loan:
                continue
            paths = [user.id_front_path, user.id_back_path, user.selfie_path]
            if not options["apply"]:
                self.stdout.write(f"would purge documents for user {user.pk}")
                deleted += 1
                continue
            for path in paths:
                SupabaseStorage.delete_file(path)
            user.id_front_path = user.id_back_path = user.selfie_path = ""
            user.save(update_fields=["id_front_path", "id_back_path", "selfie_path"])
            deleted += 1

        verb = "would purge" if not options["apply"] else "purged"
        self.stdout.write(self.style.SUCCESS(f"{verb} documents for {deleted} user(s)."))
