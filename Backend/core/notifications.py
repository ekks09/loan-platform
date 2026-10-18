"""Notification delivery.

SMS is the only channel available to users (accounts are phone-based). The
console backend is for local/staging only. Production requires an approved
provider to be configured; when none is configured, sends are skipped and
flagged in the logs rather than pretending to deliver.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    pass


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:4]}***{phone[-2:]}"


def sms_available() -> bool:
    return settings.SMS_BACKEND in {"console", "africastalking"}


def send_sms(phone: str, message: str, *, purpose: str = "general") -> bool:
    """Send an SMS. Returns True when handed to a provider.

    Never raises into request handling: a failed notification must not roll
    back a financial or identity operation.
    """
    backend = settings.SMS_BACKEND
    if backend == "none":
        logger.warning(
            "SMS backend not configured; skipping %s notification to %s",
            purpose,
            mask_phone(phone),
        )
        return False

    if backend == "console":
        logger.info(
            "SMS(%s) to %s: %s", purpose, mask_phone(phone), message
        )
        return True

    if backend == "africastalking":
        try:
            response = requests.post(
                "https://api.africastalking.com/version1/messaging",
                headers={
                    "apiKey": settings.AFRICASTALKING_API_KEY,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "username": settings.AFRICASTALKING_USERNAME,
                    "to": f"+{phone}",
                    "message": message,
                    "from": settings.AFRICASTALKING_SENDER_ID or None,
                },
                timeout=15,
            )
            response.raise_for_status()
            logger.info("SMS(%s) delivered to provider for %s", purpose, mask_phone(phone))
            return True
        except requests.RequestException as exc:
            logger.error("SMS delivery failed for %s: %s", mask_phone(phone), exc)
            return False

    logger.error("Unknown SMS backend %r", backend)
    return False
