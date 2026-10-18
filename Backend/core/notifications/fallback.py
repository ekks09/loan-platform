"""In-app fallback channel.

Only *allowed* transactional purposes may fall back, and only for a known,
authenticated account. One-time codes never fall back: an OTP that cannot be
delivered by SMS must simply not arrive anywhere else (the verification
requirement is not weakened, and nothing sensitive is copied to a weaker
channel).
"""
from __future__ import annotations

import logging

from core.notifications.base import mask_phone

logger = logging.getLogger(__name__)

# Purposes whose content is safe (and useful) inside the customer's account.
FALLBACK_ALLOWED_PURPOSES = frozenset(
    {"verification", "disbursed", "disbursement_failed"}
)

# Purposes that must never take any channel other than the primary one.
SENSITIVE_PURPOSES = frozenset({"password_reset"})


def fallback_allowed(purpose: str) -> bool:
    return purpose in FALLBACK_ALLOWED_PURPOSES and purpose not in SENSITIVE_PURPOSES


def record_in_app_fallback(*, user, purpose: str, body: str) -> bool:
    """Persist the notification for a platform user. Returns success."""
    if user is None or not fallback_allowed(purpose):
        return False
    try:
        from core.models import InAppNotification

        InAppNotification.objects.create(user=user, purpose=purpose[:32], body=body)
        logger.info(
            "In-app fallback recorded for %s notification (purpose=%s)",
            mask_phone(getattr(user, "phone", "")),
            purpose,
        )
        return True
    except Exception:  # a notification must never break the calling operation
        logger.exception("In-app fallback write failed (purpose=%s)", purpose)
        return False
