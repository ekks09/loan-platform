"""SMS providers.

* ``console`` — local/staging only: prints the message to the server log.
* ``africastalking`` — Africa's Talking Messaging API.
* ``none`` — delivery disabled (fail-visible: callers see ``skipped``).

Provider credentials stay in settings on the server. Message bodies are only
logged by the console backend, which must never run in production
(``check_config`` warns on exactly that combination).
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings

from core.notifications.base import (
    DeliveryStatus,
    NotificationResult,
    mask_phone,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15
_AT_ENDPOINT = "https://api.africastalking.com/version1/messaging"


class NullSMSProvider:
    name = "none"

    def available(self) -> bool:
        return False

    def send(self, phone: str, message: str, *, purpose: str = "general") -> NotificationResult:
        return NotificationResult(
            status=DeliveryStatus.SKIPPED,
            provider=self.name,
            detail="no_sms_provider",
        )


class ConsoleSMSProvider:
    """Development provider: the code/message reaches the server log only."""

    name = "console"

    def available(self) -> bool:
        return True

    def send(self, phone: str, message: str, *, purpose: str = "general") -> NotificationResult:
        logger.info("SMS(%s) to %s: %s", purpose, mask_phone(phone), message)
        return NotificationResult(
            status=DeliveryStatus.SENT, provider=self.name, attempts=1
        )


class AfricasTalkingSMSProvider:
    name = "africastalking"

    def available(self) -> bool:
        return bool(settings.AFRICASTALKING_USERNAME and settings.AFRICASTALKING_API_KEY)

    def send(self, phone: str, message: str, *, purpose: str = "general") -> NotificationResult:
        if not self.available():
            return NotificationResult(
                status=DeliveryStatus.SKIPPED,
                provider=self.name,
                detail="missing_credentials",
            )
        try:
            response = requests.post(
                _AT_ENDPOINT,
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
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            return NotificationResult(
                status=DeliveryStatus.FAILED, provider=self.name, detail="timeout"
            )
        except requests.RequestException:
            return NotificationResult(
                status=DeliveryStatus.FAILED, provider=self.name, detail="network_error"
            )
        # A 2xx means the provider accepted the message, nothing more.
        if response.status_code in (200, 201):
            return NotificationResult(
                status=DeliveryStatus.SENT, provider=self.name, attempts=1
            )
        return NotificationResult(
            status=DeliveryStatus.FAILED,
            provider=self.name,
            detail="provider_5xx" if response.status_code >= 500 else (
                "rate_limited" if response.status_code == 429 else "rejected"
            ),
        )


_PROVIDERS = {
    "console": ConsoleSMSProvider,
    "africastalking": AfricasTalkingSMSProvider,
    "none": NullSMSProvider,
}


def get_sms_provider() -> NullSMSProvider | ConsoleSMSProvider | AfricasTalkingSMSProvider:
    """Return the SMS provider selected by the validated SMS_BACKEND setting."""
    backend = settings.SMS_BACKEND
    provider_class = _PROVIDERS.get(backend)
    if provider_class is None:
        # settings validates SMS_BACKEND at boot; defence in depth.
        logger.error("Unknown SMS_BACKEND %r", backend)
        return NullSMSProvider()
    return provider_class()
