"""Notification delivery.

Public API kept stable for existing callers::

    from core.notifications import send_sms, sms_available, mask_phone

SMS is the primary channel (accounts are phone-based). The console provider
is for local/staging only. The in-app channel is a restricted fallback for
allowed transactional notifications — never for one-time codes.
"""
from core.notifications.base import (
    DeliveryStatus,
    NotificationError,
    NotificationProvider,
    NotificationResult,
    PermanentNotificationError,
    mask_phone,
)
from core.notifications.fallback import (
    FALLBACK_ALLOWED_PURPOSES,
    fallback_allowed,
    record_in_app_fallback,
)
from core.notifications.service import send_sms, send_with_retry, sms_available
from core.notifications.sms import get_sms_provider

__all__ = [
    "DeliveryStatus",
    "FALLBACK_ALLOWED_PURPOSES",
    "NotificationError",
    "NotificationProvider",
    "NotificationResult",
    "PermanentNotificationError",
    "fallback_allowed",
    "get_sms_provider",
    "mask_phone",
    "record_in_app_fallback",
    "send_sms",
    "send_with_retry",
    "sms_available",
]
