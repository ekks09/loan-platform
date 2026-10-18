"""Backward-compatible import location for the Paystack integration.

The implementation moved to the provider layer:

* :class:`payments.providers.paystack.PaystackClient` — low-level HTTP client;
* :class:`payments.providers.paystack.PaystackProvider` — the adapter business
  services actually use via :func:`payments.providers.get_payment_provider`;
* :class:`payments.providers.base.ProviderError` — the shared error type.

This shim exists so older imports (``from payments.paystack import ...``) keep
working. New code should import from ``payments.providers``. Do not add logic
here.
"""
from payments.providers.base import ProviderError
from payments.providers.paystack import PaystackClient, PaystackProvider

__all__ = ["PaystackClient", "PaystackProvider", "ProviderError"]
