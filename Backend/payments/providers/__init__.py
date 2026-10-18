"""Payment provider abstraction.

Public surface: the contract (``base``) and the selector (``registry``).
Adapters (e.g. ``paystack``) are imported lazily so unused providers never
affect startup.
"""
from payments.providers.base import (
    ChargeStatus,
    ChargeVerification,
    CheckoutSession,
    PaymentProvider,
    ProviderError,
    RefundOutcome,
    RefundStatus,
    TransferInitiation,
    TransferRecipient,
    TransferState,
    TransferStatus,
    WebhookEnvelope,
    WebhookEventKind,
)
from payments.providers.registry import get_payment_provider

__all__ = [
    "ChargeStatus",
    "ChargeVerification",
    "CheckoutSession",
    "PaymentProvider",
    "ProviderError",
    "RefundOutcome",
    "RefundStatus",
    "TransferInitiation",
    "TransferRecipient",
    "TransferState",
    "TransferStatus",
    "WebhookEnvelope",
    "WebhookEventKind",
    "get_payment_provider",
]
