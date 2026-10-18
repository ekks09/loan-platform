"""Provider selection.

The active provider is chosen by the validated ``PAYMENT_PROVIDER`` setting.
Business services call :func:`get_payment_provider`; they never import an
adapter directly. Adding a provider means implementing
:class:`payments.providers.base.PaymentProvider`, extending
``SUPPORTED_PAYMENT_PROVIDERS`` in ``core.settings`` and adding a branch here —
nothing else changes.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.errors import PaymentsDisabled
from payments.providers.base import PaymentProvider


def get_payment_provider() -> PaymentProvider:
    """Return the configured payment provider, failing closed.

    Raises :class:`PaymentsDisabled` when payment processing is off — the
    same behaviour the old direct-client construction had — so callers keep
    one guard path. An unsupported provider name is a configuration error,
    never a silent fallback.
    """
    if not settings.PAYMENTS_ENABLED:
        raise PaymentsDisabled()
    name = settings.PAYMENT_PROVIDER
    if name == "paystack":
        from payments.providers.paystack import PaystackProvider

        return PaystackProvider()
    # settings validates PAYMENT_PROVIDER at boot; this is defence in depth.
    raise ImproperlyConfigured(
        f"Unsupported PAYMENT_PROVIDER {name!r}. "
        "Supported providers: paystack."
    )
