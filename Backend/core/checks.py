"""Django system checks that catch configuration drift before deploy."""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def check_fee_schedule(app_configs, **kwargs):
    from loans.models import Loan

    errors = []
    for message in Loan.validate_schedule():
        errors.append(Error(message, id="loans.E001"))
    return errors


@register()
def check_production_configuration(app_configs, **kwargs):
    issues = []
    if settings.PAYMENTS_LIVE_MODE:
        if not settings.LEGAL_TERMS_APPROVED:
            issues.append(
                Error(
                    "Live payments are enabled without LEGAL_TERMS_APPROVED.",
                    hint="Complete legal review before enabling live payments.",
                    id="core.E002",
                )
            )
        if settings.FEE_COLLECTION_MODE == "upfront" and not settings.FEE_COLLECTION_APPROVED:
            issues.append(
                Error(
                    "Upfront fee collection is enabled without approval.",
                    id="core.E003",
                )
            )
    if not settings.DEBUG:
        if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
            issues.append(
                Warning(
                    "Secure cookies are disabled in a non-debug environment.",
                    id="core.W004",
                )
            )
        if settings.REQUIRE_DOCUMENT_STORAGE:
            try:
                from core.storage import document_storage_configured

                storage_ok = document_storage_configured()
            except Exception:
                storage_ok = False
            if not storage_ok:
                issues.append(
                    Error(
                        "Document storage is required but the selected "
                        "DOCUMENT_STORE is not configured.",
                        id="core.E005",
                    )
                )
    if settings.PAYMENTS_ENABLED and settings.PAYSTACK_SECRET_KEY.startswith("sk_test_"):
        issues.append(
            Warning(
                "Paystack test keys are configured; no real money can move.",
                id="core.W006",
            )
        )
    return issues
