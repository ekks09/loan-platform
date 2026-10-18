"""Mobile number handling for the supported markets.

One implementation is shared by the user model, serializers, the SMS OTP flow
and the browser contract. A number is stored in *canonical* form: country
calling code followed by the national significant number, with no ``+`` and
no spaces.

    Kenya   254 7XXXXXXXX | 254 1XXXXXXXX    (12 digits)
    DR Congo 243 8XXXXXXXX | 243 9XXXXXXXX   (12 digits)

Kenya is the default market and remains the only market with M-Pesa payouts.
DR Congo is accepted so that accounts and SMS password resets work there; the
interface states the payout limitation rather than implying otherwise.
"""
from __future__ import annotations

import re

from core.errors import DomainError

KENYA = "KE"
DRC = "CD"
DEFAULT_COUNTRY = KENYA

COUNTRIES = {
    KENYA: {
        "name": "Kenya",
        "dial_code": "254",
        "prefix": "+254",
        "regex": re.compile(r"^254(7|1)\d{8}$"),
        "example": "0712 345 678",
    },
    DRC: {
        "name": "DR Congo",
        "dial_code": "243",
        "prefix": "+243",
        "regex": re.compile(r"^243[89]\d{8}$"),
        "example": "0812 345 678",
    },
}

# Backwards-compatible names used across the existing code base.
CANONICAL_RE = COUNTRIES[KENYA]["regex"]
_STRIP_RE = re.compile(r"[\s\-().]")


class InvalidPhoneNumber(DomainError, ValueError):
    code = "invalid_phone_number"
    http_status = 400
    default_message = (
        "Enter a valid mobile number, for example 0712345678, +254712345678 "
        "or +243812345678."
    )


def _digits(value: object) -> str:
    if value is None:
        raise InvalidPhoneNumber()
    raw = _STRIP_RE.sub("", str(value).strip())
    if not raw:
        raise InvalidPhoneNumber()
    if raw.startswith("+"):
        raw = raw[1:]
    elif raw.startswith("00"):
        raw = raw[2:]
    if not raw.isdigit():
        raise InvalidPhoneNumber()
    return raw


def normalize_msisdn(value: object, *, default_country: str = DEFAULT_COUNTRY) -> str:
    """Return canonical digits for ``value`` or raise ``InvalidPhoneNumber``.

    Explicit international forms (``+243…``, ``00243…``, ``243…``) always win.
    A national form (``07…``, ``08…``, ``09…``, ``01…``) is resolved with
    ``default_country``, and the Kenyan ``07``/``01`` and Congolese ``08``/``09``
    blocks are distinguished when the country is not stated.
    """
    raw = _digits(value)

    for code in COUNTRIES.values():
        dial = code["dial_code"]
        if raw.startswith(dial) and code["regex"].match(raw):
            return raw

    # National formats: 0 + 9 digits. The second digit identifies the market.
    if len(raw) == 10 and raw.startswith("0"):
        national = raw[1:]
        if national[0] in {"7", "1"}:
            candidate = "254" + national
        elif national[0] in {"8", "9"}:
            candidate = "243" + national
        else:
            raise InvalidPhoneNumber()
        if COUNTRIES[KENYA]["regex"].match(candidate) or COUNTRIES[DRC]["regex"].match(candidate):
            return candidate
        raise InvalidPhoneNumber()

    # Bare 9-digit national numbers, only when the market is stated.
    if len(raw) == 9 and raw[0] in {"7", "1", "8", "9"}:
        country = COUNTRIES.get(default_country)
        if country and country["regex"].match(country["dial_code"] + raw):
            return country["dial_code"] + raw
        raise InvalidPhoneNumber()

    raise InvalidPhoneNumber()


def normalize_ke_phone(value: object) -> str:
    """Return the canonical ``254XXXXXXXXX`` form or raise InvalidPhoneNumber.

    Accepted inputs: ``07XXXXXXXX``, ``01XXXXXXXX``, ``7XXXXXXXX``,
    ``1XXXXXXXX``, ``2547XXXXXXXX``, ``2541XXXXXXXX`` and the same with a
    leading ``+``, spaces, dashes or parentheses.
    """
    canonical = normalize_msisdn(value, default_country=KENYA)
    if not COUNTRIES[KENYA]["regex"].match(canonical):
        raise InvalidPhoneNumber(
            "Enter a valid Kenyan mobile number, for example 0712345678."
        )
    return canonical


def is_valid_ke_phone(value: object) -> bool:
    try:
        normalize_ke_phone(value)
        return True
    except InvalidPhoneNumber:
        return False


def is_valid_msisdn(value: object, *, default_country: str = DEFAULT_COUNTRY) -> bool:
    try:
        normalize_msisdn(value, default_country=default_country)
        return True
    except InvalidPhoneNumber:
        return False


def country_of(canonical: str) -> str | None:
    for key, country in COUNTRIES.items():
        if country["regex"].match(str(canonical or "")):
            return key
    return None


def country_name(canonical: str) -> str:
    key = country_of(canonical)
    return COUNTRIES[key]["name"] if key else "Unknown"


def to_e164(canonical: str) -> str:
    return f"+{canonical}" if canonical and str(canonical).isdigit() else ""


def national(canonical: str) -> str:
    """``254712345678`` -> ``0712 345 678``."""
    canonical = str(canonical or "")
    if len(canonical) != 12 or not canonical.isdigit():
        return ""
    return f"0{canonical[3:6]} {canonical[6:9]} {canonical[9:12]}"


def international(canonical: str) -> str:
    canonical = str(canonical or "")
    if len(canonical) != 12 or not canonical.isdigit():
        return ""
    return f"+{canonical[:3]} {canonical[3:6]} {canonical[6:9]} {canonical[9:12]}"


def mask_phone(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) < 6:
        return "***"
    return f"{phone[:4]}***{phone[-2:]}"
