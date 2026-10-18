from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from loans.models import Loan
from payments.models import Transfer
from payments.services import retry_disbursement


class Command(BaseCommand):
    help = (
        "Retry disbursements whose backoff has elapsed. Dry-run by default. "
        "Unknown states are reconciled with Paystack before any retry."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Perform retries.")
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        due = Transfer.objects.filter(
            status__in=[Transfer.Status.FAILED, Transfer.Status.UNKNOWN],
            attempts__lt=settings.PAYSTACK_TRANSFER_MAX_ATTEMPTS,
        ).filter(
            next_retry_at__lte=timezone.now()
        ).select_related("loan")[: options["limit"]]

        count = 0
        for transfer in due:
            count += 1
            if not options["apply"]:
                self.stdout.write(
                    f"would retry transfer {transfer.pk} "
                    f"(status={transfer.status}, attempts={transfer.attempts})"
                )
                continue
            loan = Loan.objects.get(pk=transfer.loan_id)
            try:
                retry_disbursement(loan)
                self.stdout.write(
                    self.style.SUCCESS(f"retried transfer {transfer.pk}")
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"retry failed for {transfer.pk}: {exc}")
                )

        verb = "would retry" if not options["apply"] else "processed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {count} transfer(s)."))
