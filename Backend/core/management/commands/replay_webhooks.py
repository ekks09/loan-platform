from __future__ import annotations

from django.core.management.base import BaseCommand

from payments.models import WebhookEvent
from payments.services import process_webhook_event


class Command(BaseCommand):
    help = "Replay stored webhook events that previously failed."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--id", type=int, help="Replay a single event id.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        queryset = WebhookEvent.objects.all()
        if options["id"]:
            queryset = queryset.filter(pk=options["id"])
        else:
            queryset = queryset.filter(status=WebhookEvent.Status.FAILED)
        events = list(queryset[: options["limit"]])
        if options["dry_run"]:
            for event in events:
                self.stdout.write(
                    f"would replay {event.pk} {event.event_type} ({event.status})"
                )
            return
        processed = failed = 0
        for event in events:
            try:
                process_webhook_event(event)
                processed += 1
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"{event.pk}: {exc}"))
        self.stdout.write(
            self.style.SUCCESS(f"replayed={processed} failed={failed}")
        )
