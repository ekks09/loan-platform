from django.core.management.base import BaseCommand

from loans.services import expire_stale_offers


class Command(BaseCommand):
    help = "Expire approved offers that were never accepted. Dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually expire offers.")

    def handle(self, *args, **options):
        count = expire_stale_offers(dry_run=not options["apply"])
        verb = "would expire" if not options["apply"] else "expired"
        self.stdout.write(self.style.SUCCESS(f"{verb} {count} stale offer(s)."))
