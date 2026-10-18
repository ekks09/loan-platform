"""Provider-neutral notification contracts.

Delivery semantics differ per provider, so states are named precisely:

* ``sent`` — the provider **accepted** the message (an API 2xx). It is *not*
  a handset delivery confirmation; no caller may treat it as one.
* ``skipped`` — no capable provider is configured; nothing was attempted.
* ``failed`` — the provider rejected the message or retries were exhausted.

Message contents can contain one-time codes, so neither results nor logs may
carry them: only the purpose and a masked recipient.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class DeliveryStatus(str, enum.Enum):
    SENT = "sent"  # accepted by the provider (never "delivered to handset")
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class NotificationResult:
    status: DeliveryStatus
    provider: str
    attempts: int = 0
    # Operator-safe detail only (error class), never message contents.
    detail: str = ""

    @property
    def sent(self) -> bool:
        return self.status is DeliveryStatus.SENT


class NotificationError(RuntimeError):
    """Transient provider failure eligible for a bounded retry."""

    transient = True


class PermanentNotificationError(NotificationError):
    """Failure that retrying cannot fix (rejected credentials/payload)."""

    transient = False


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:4]}***{phone[-2:]}"


@runtime_checkable
class NotificationProvider(Protocol):
    """Contract for notification delivery adapters (SMS today)."""

    name: str

    def available(self) -> bool:
        """True when the provider is fully configured (never logs secrets)."""

    def send(self, phone: str, message: str, *, purpose: str = "general") -> NotificationResult:
        """Attempt one delivery. Raises nothing; results carry the outcome."""
