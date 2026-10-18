"""Validate deployment configuration before starting traffic.

Exits non-zero when a production-critical requirement is missing so a deploy
pipeline stops instead of serving a broken or unsafe application.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from loans.models import Loan


class Command(BaseCommand):
    help = "Validate environment configuration and platform invariants."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-test-mode",
            action="store_true",
            help="Do not fail when Paystack test keys are configured.",
        )

    def handle(self, *args, **options):
        errors: list[str] = []
        warnings: list[str] = []

        # Fee schedule must be contiguous and cover the full supported range.
        for problem in Loan.validate_schedule():
            errors.append(f"Fee schedule: {problem}")

        if not settings.SECRET_KEY or len(settings.SECRET_KEY) < 32:
            errors.append("DJANGO_SECRET_KEY must be at least 32 characters.")

        if not settings.DEBUG:
            if not settings.ALLOWED_HOSTS:
                errors.append("DJANGO_ALLOWED_HOSTS must be set in production.")
            if not settings.CSRF_TRUSTED_ORIGINS and settings.SECURE_SSL_REDIRECT:
                warnings.append(
                    "CSRF_TRUSTED_ORIGINS is empty; cross-origin form posts will fail."
                )
            if not settings.SESSION_COOKIE_SECURE:
                errors.append("Secure session cookies must be enabled in production.")

        if settings.PAYMENTS_ENABLED:
            if settings.PAYSTACK_SECRET_KEY.startswith("sk_test_"):
                message = "Paystack test keys are configured; live money cannot move."
                # --allow-test-mode exists so a staging deploy can run with test
                # keys. It must never silence a *live-mode* misconfiguration.
                if settings.PAYMENTS_LIVE_MODE:
                    errors.append(message)
                else:
                    warnings.append(message)
            if settings.PAYMENTS_LIVE_MODE and not settings.LEGAL_TERMS_APPROVED:
                errors.append("Live payments require LEGAL_TERMS_APPROVED=true.")
        else:
            warnings.append("PAYMENTS_ENABLED=false: payment endpoints will refuse requests.")

        if settings.FEE_COLLECTION_MODE == "upfront":
            if not settings.FEE_COLLECTION_APPROVED:
                errors.append("Upfront fees require FEE_COLLECTION_APPROVED=true.")
            warnings.append("Upfront fee collection is enabled; confirm legal approval.")

        if settings.REQUIRE_DOCUMENT_STORAGE and not (
            settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY
        ):
            errors.append("Document storage is required but Supabase is not configured.")

        if settings.SMS_BACKEND == "none" and not settings.DEBUG:
            warnings.append("SMS_BACKEND=none: users will not receive notifications.")

        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"WARN  {warning}"))
        for error in errors:
            self.stdout.write(self.style.ERROR(f"ERROR {error}"))
        if errors:
            raise CommandError(f"{len(errors)} configuration error(s) found.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Configuration OK ({len(warnings)} warning(s)). "
                f"payments={'on' if settings.PAYMENTS_ENABLED else 'off'} "
                f"mode={'live' if settings.PAYMENTS_LIVE_MODE else 'test'} "
                f"fees={settings.FEE_COLLECTION_MODE}"
            )
        )
