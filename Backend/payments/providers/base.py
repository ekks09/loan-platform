"""Provider-neutral payment contracts.

Business services (``payments/services.py``) depend only on the types in this
module — never on a provider's payload shape, status vocabulary, headers or
API paths. A provider adapter translates between these normalized models and
its own API.

Money crosses the boundary in **minor units** (Paystack: kobo/cents). The
platform prices in whole KES; conversion happens in the service layer via
``core.money`` with integer arithmetic only.

Safety vocabulary used by every adapter:

* ``retryable`` — the call failed before the provider accepted it, or is
  idempotent, so the caller may retry.
* ``uncertain`` — the provider may have accepted the operation before the
  failure was observed (e.g. a timeout on a non-idempotent transfer). The
  caller must reconcile with the provider before any retry; never retry
  blindly.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class ProviderError(Exception):
    """Normalized provider failure raised by every adapter.

    ``message`` is operator-facing and must never contain secrets, raw
    provider payloads or customer PII.
    """

    message: str
    code: str = "provider_error"
    http_status: int | None = None
    retryable: bool = False
    uncertain: bool = False

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ChargeStatus(str, enum.Enum):
    """Normalized final state of an inbound customer charge."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"
    ABANDONED = "abandoned"  # checkout closed without paying


class TransferStatus(str, enum.Enum):
    """Normalized state of an outbound disbursement."""

    PENDING = "pending"  # accepted/queued/processing/OTP — not settled
    SUCCESS = "success"
    FAILED = "failed"
    REVERSED = "reversed"


class RefundStatus(str, enum.Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"


class WebhookEventKind(str, enum.Enum):
    """Normalized event categories the platform reacts to.

    Raw provider event-type strings never reach the dispatch logic.
    """

    CHARGE_SUCCESS = "charge_success"
    TRANSFER_SUCCESS = "transfer_success"
    TRANSFER_FAILED = "transfer_failed"
    TRANSFER_REVERSED = "transfer_reversed"
    REFUND_PROCESSED = "refund_processed"
    REFUND_FAILED = "refund_failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CheckoutSession:
    """Normalized result of ``initialize``.

    ``authorization_url`` is the only field the frontend needs: it redirects
    the customer and the signed webhook (plus explicit server verification)
    confirms the payment. ``access_code`` is optional provider session state
    retained for compatibility with the stored payment record.
    """

    authorization_url: str
    access_code: str = ""
    provider_reference: str = ""


@dataclass(frozen=True)
class ChargeVerification:
    """Normalized result of ``verify`` for an inbound charge."""

    status: ChargeStatus
    reference: str
    amount_minor: int
    currency: str
    channel: str = ""
    transaction_id: str = ""
    gateway_response: str = ""


@dataclass(frozen=True)
class TransferRecipient:
    recipient_code: str


@dataclass(frozen=True)
class TransferInitiation:
    """Acceptance of a transfer request — **not** a settlement.

    The raw provider payload is retained out-of-band for audit fields; the
    service layer must confirm via webhook or lookup before marking SUCCESS.
    """

    transfer_code: str = ""
    provider_reference: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class TransferState:
    """Normalized provider-reported transfer state."""

    status: TransferStatus
    reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class RefundOutcome:
    status: RefundStatus
    refund_id: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class WebhookEnvelope:
    """Normalized provider webhook delivery.

    ``kind`` is the only field the dispatcher branches on; ``event_type``
    preserves the raw provider string for storage and replay diagnostics.
    """

    kind: WebhookEventKind
    event_type: str
    reference: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """Contract every payment adapter must satisfy.

    This is deliberately small: it covers exactly the operations the platform
    performs today, shaped so a credible second provider (another PSP) could
    implement it without touching business logic.
    """

    name: str

    # -- inbound charges ----------------------------------------------------
    def initialize(
        self,
        *,
        email: str,
        amount_minor: int,
        reference: str,
        currency: str,
        metadata: Mapping[str, Any],
        channels: Optional[Sequence[str]] = None,
    ) -> CheckoutSession:
        """Create (or reuse) a hosted checkout for ``reference``."""

    def verify(self, reference: str) -> ChargeVerification:
        """Authoritative server-side status of a charge."""

    def normalize_charge(self, payload: Mapping[str, Any]) -> ChargeVerification:
        """Normalize a provider charge payload (webhook data) for validation.

        Only used for payloads already authenticated by signature; the result
        must never upgrade trust (a webhook saying "success" is not trusted
        without provider re-verification when that is configured).
        """

    # -- outbound transfers --------------------------------------------------
    def create_transfer_recipient(self, *, name: str, phone: str) -> TransferRecipient:
        ...

    def initiate_transfer(
        self,
        *,
        amount_minor: int,
        recipient_code: str,
        reference: str,
        reason: str,
    ) -> TransferInitiation:
        """Request a transfer. Acceptance is not settlement."""

    def get_transfer(self, transfer_code: str) -> TransferState:
        ...

    def find_transfer_by_reference(self, reference: str) -> TransferState | None:
        """Look up a transfer whose acknowledgement was never received.

        Returns ``None`` when the provider has no transfer for the reference.
        This is the reconciliation path for ``uncertain`` initiation failures.
        """

    # -- refunds --------------------------------------------------------------
    def refund(
        self,
        *,
        transaction_reference: str,
        amount_minor: int,
        reason: str,
    ) -> RefundOutcome:
        ...

    # -- webhooks ---------------------------------------------------------------
    def verify_webhook(self, *, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Authenticate a raw webhook delivery.

        Proves authenticity of the message only — not that the payment is
        expected, belongs to a customer, or should be fulfilled.
        """

    def classify_webhook(self, payload: Mapping[str, Any]) -> WebhookEnvelope:
        """Map a parsed provider payload to a normalized envelope."""
