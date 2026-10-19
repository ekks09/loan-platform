"""Template context shared by every page."""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone


def platform(request):
    # Imported lazily: loans imports core.error types, so a module-level import
    # here would tighten the startup order for no benefit.
    from loans.models import Loan

    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PLATFORM_TAGLINE": settings.PLATFORM_TAGLINE,
        "PLATFORM_LEGAL_NAME": settings.PLATFORM_LEGAL_NAME,
        "PLATFORM_LICENCE_STATUS": settings.PLATFORM_LICENCE_STATUS,
        "PLATFORM_LICENCE_NUMBER": settings.PLATFORM_LICENCE_NUMBER,
        "PLATFORM_SUPPORT_PHONE": settings.PLATFORM_SUPPORT_PHONE,
        "PLATFORM_SUPPORT_EMAIL": settings.PLATFORM_SUPPORT_EMAIL,
        "PLATFORM_ADDRESS": settings.PLATFORM_ADDRESS,
        "PLATFORM_SOCIAL_PROOF": settings.PLATFORM_SOCIAL_PROOF,
        "TERMS_VERSION": settings.TERMS_VERSION,
        "PRIVACY_VERSION": settings.PRIVACY_VERSION,
        "CONSENT_VERSION": settings.CONSENT_VERSION,
        "LEGAL_TERMS_APPROVED": settings.LEGAL_TERMS_APPROVED,
        "FEE_COLLECTION_MODE": settings.FEE_COLLECTION_MODE,
        "PAYMENTS_LIVE_MODE": settings.PAYMENTS_LIVE_MODE,
        "DOCUMENT_RETENTION_DAYS": settings.DOCUMENT_RETENTION_DAYS,
        "API_BASE_URL": settings.API_BASE_URL,
        "SUPPORT_EMAIL": settings.PLATFORM_SUPPORT_EMAIL,
        "PWA_THEME_COLOR": settings.PWA_THEME_COLOR,
        "PWA_BACKGROUND_COLOR": settings.PWA_BACKGROUND_COLOR,
        "PWA_CACHE_VERSION": settings.PWA_CACHE_VERSION,
        "SMS_BACKEND": settings.SMS_BACKEND,
        "MPESA_PAYBILL_NUMBER": settings.MPESA_PAYBILL_NUMBER,
        "MPESA_TILL_NUMBER": settings.MPESA_TILL_NUMBER,
        "CURRENT_YEAR": timezone.now().year,
        "fee_schedule_version": Loan.FEE_SCHEDULE_VERSION,
        "fee_minimum": Loan.FEE_MINIMUM,
    }
