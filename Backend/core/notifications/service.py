"""Notification delivery service.

Central entry point used by the rest of the platform (``send_sms`` keeps its
long-standing signature). Responsibilities:

* provider selection through the registry;
* bounded retry of transient failures (timeout / network / 5xx / rate-limit);
* fallback to the in-app channel for allowed transactional purposes;
* uniform, secret-free operational logs.

A failed notification must never roll back a financial or identity
operation, so this module never raises into request handling.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings

from core.notifications.base import (
    DeliveryStatus,
    NotificationError,  # noqa: F401  (re-exported for compatibility)
    NotificationResult,
    mask_phone,
)
from core.notifications.fallback import fallback_allowed, record_in_app_fallback
from core.notifications.sms import get_sms_provider

logger = logging.getLogger(__name__)

_TRANSIENT_DETAILS = frozenset({"timeout", "network_error", "provider_5xx", "rate_limited"})


def send_with_retry(provider, phone: str, message: str, *, purpose: str) -> NotificationResult:
    """Bounded retry: 1 initial attempt + NOTIFICATION_MAX_RETRIES retries.

    Retries apply only to transient failures; a rejected payload/credential
    set fails immediately and loudly in the logs.
    """
    max_retries = max(0, settings.NOTIFICATION_MAX_RETRIES)
    attempts = 0
    last = NotificationResult(
        status=DeliveryStatus.FAILED, provider=provider.name, detail="not_attempted"
    )
    for attempt in range(1, max_retries + 2):
        attempts = attempt
        try:
            result = provider.send(phone, message, purpose=purpose)
        except Exception:  # provider adapters must not raise, but never trust it
            logger.exception(
                "Notification provider %s crashed for %s",
                provider.name,
                mask_phone(phone),
            )
            break
        if isinstance(result, NotificationResult):
            last = result
        else:  # a misbehaving adapter returning the wrong type is a bug, fail safe
            last = NotificationResult(
                status=DeliveryStatus.FAILED,
                provider=provider.name,
                detail="bad_result_type",
            )
        if last.sent or last.status is DeliveryStatus.SKIPPED:
            break
        if last.detail not in _TRANSIENT_DETAILS or attempt > max_retries:
            break
        time.sleep(min(settings.NOTIFICATION_RETRY_BACKOFF_SECONDS * attempt, 3))
    return NotificationResult(
        status=last.status,
        provider=last.provider,
        attempts=attempts,
        detail=last.detail,
    )


def send_sms(phone: str, message: str, *, purpose: str = "general", user=None) -> bool:
    """Send an SMS notification. Returns True only when the provider accepted.

    ``user`` enables the in-app fallback (allowed transactional purposes
    only). OTP code senders must not pass it: sensitive content never leaves
    the primary channel.
    """
    provider = get_sms_provider()
    if not provider.available():
        logger.warning(
            "SMS provider %s unavailable; %s notification to %s not sent",
            provider.name,
            purpose,
            mask_phone(phone),
        )
        if user is not None and fallback_allowed(purpose):
            record_in_app_fallback(user=user, purpose=purpose, body=message)
        return False

    result = send_with_retry(provider, phone, message, purpose=purpose)
    if result.sent:
        logger.info(
            "SMS(%s) accepted by %s for %s (attempts=%s)",
            purpose,
            provider.name,
            mask_phone(phone),
            result.attempts,
        )
        return True

    logger.error(
        "SMS(%s) failed via %s for %s after %s attempt(s): %s",
        purpose,
        provider.name,
        mask_phone(phone),
        result.attempts,
        result.detail,
    )
    if user is not None and fallback_allowed(purpose):
        record_in_app_fallback(user=user, purpose=purpose, body=message)
    return False


def sms_available() -> bool:
    return get_sms_provider().available()
