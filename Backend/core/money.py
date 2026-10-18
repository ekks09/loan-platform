"""Money helpers.

The product prices in whole Kenyan shillings (KES). Paystack expects the
smallest currency unit (cents/kobo), so all conversions happen at the provider
boundary using integer arithmetic only. No floats are used for money anywhere.
"""
from __future__ import annotations

from core.errors import DomainError

MINOR_UNITS_PER_KES = 100


class InvalidMoney(DomainError, ValueError):
    code = "invalid_money"
    http_status = 400
    default_message = "Invalid monetary amount."


def as_kes(value: object) -> int:
    """Coerce a value to a non-negative whole-shilling integer."""
    if isinstance(value, bool):
        raise InvalidMoney()
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            raise InvalidMoney()
    try:
        amount = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise InvalidMoney() from exc
    if amount < 0:
        raise InvalidMoney()
    return amount


def to_minor(amount_kes: int) -> int:
    """Convert whole KES to the provider's minor unit."""
    return as_kes(amount_kes) * MINOR_UNITS_PER_KES


def from_minor(minor: int) -> int:
    """Convert provider minor units back to whole KES.

    Rejects amounts that are not a whole shilling so rounding errors surface
    instead of being silently swallowed.
    """
    if isinstance(minor, bool) or not isinstance(minor, int) or minor < 0:
        raise InvalidMoney("Provider amount must be a non-negative integer.")
    if minor % MINOR_UNITS_PER_KES != 0:
        raise InvalidMoney("Provider amount is not a whole shilling.")
    return minor // MINOR_UNITS_PER_KES


def format_kes(amount_kes: int) -> str:
    return f"KES {as_kes(amount_kes):,}"
