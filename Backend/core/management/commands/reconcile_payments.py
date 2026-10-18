from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from payments.services import run_reconciliation


class Command(BaseCommand):
    help = (
        "Compare internal payment/transfer state with Paystack. "
        "Dry-run by default; pass --apply to correct confirmed mismatches."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply safe corrections.")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")

    def handle(self, *args, **options):
        run = run_reconciliation(dry_run=not options["apply"])
        payload = {
            "run_id": run.pk,
            "status": run.status,
            "dry_run": run.dry_run,
            "summary": run.summary,
            "issues": run.issues,
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciliation {run.pk} {run.status} (dry_run={run.dry_run})"
            )
        )
        for key, value in run.summary.items():
            self.stdout.write(f"  {key}: {value}")
        for issue in run.issues:
            self.stdout.write(self.style.WARNING(f"  issue: {issue}"))
