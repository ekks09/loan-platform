"""SMS one-time-code issuing, rate limiting and audit.

The reset flow accepts a number in any supported market (Kenya ``+254``, DR
Congo ``+243``) and never reveals whether an account exists. Every request is
recorded in :class:`users.models.SmsAuditLog` with a keyed fingerprint of the
number instead of the number itself, so the limit can be counted without
keeping another copy of personal data.

Limits (both must pass):

* 3 successful sends per number per 15 minutes;
* 10 attempts per network address per 15 minutes.

The DRF throttle in front of the view is a second, coarser layer. These checks
live in the domain layer because a throttle keyed only on IP is bypassed by a
proxy pool, and a limit keyed only on the number is bypassed by spraying.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.notifications import send_sms, sms_available
from users.models import PasswordResetCode, SmsAuditLog, User
from users.phone import mask_phone

PASSWORD_RESET = "password_reset"


@dataclass(frozen=True)
class OtpResult:
    sent: bool
    retry_after: int = 0
    reason: str = ""


def _fingerprint(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"sms:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def phone_fingerprint(canonical: str) -> str:
    return _fingerprint(str(canonical or ""))


def client_ip(request) -> str:
    """Return the caller's address, honouring the proxy header when enabled."""
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    return request.META.get("REMOTE_ADDR", "") or ""


def ip_fingerprint(request) -> str:
    ip = client_ip(request)
    return _fingerprint(f"ip:{ip}") if ip else ""


def _window_start(minutes: int):
    return timezone.now() - timedelta(minutes=minutes)


def recent_sends(phone_hash: str, *, minutes: int | None = None) -> int:
    minutes = minutes or settings.OTP_WINDOW_MINUTES
    return SmsAuditLog.objects.filter(
        phone_hash=phone_hash,
        purpose=PASSWORD_RESET,
        status__in=[SmsAuditLog.Status.SENT, SmsAuditLog.Status.SKIPPED],
        created_at__gte=_window_start(minutes),
    ).count()


def recent_ip_attempts(ip_hash: str, *, minutes: int | None = None) -> int:
    if not ip_hash:
        return 0
    minutes = minutes or settings.OTP_WINDOW_MINUTES
    return SmsAuditLog.objects.filter(
        ip_hash=ip_hash,
        purpose=PASSWORD_RESET,
        created_at__gte=_window_start(minutes),
    ).count()


def _audit(
    *,
    user: User | None,
    phone: str,
    status: str,
    request,
    purpose: str = PASSWORD_RESET,
) -> SmsAuditLog:
    return SmsAuditLog.objects.create(
        user=user,
        phone_masked=mask_phone(phone),
        phone_hash=phone_fingerprint(phone),
        purpose=purpose,
        provider=settings.SMS_BACKEND,
        status=status,
        ip_hash=ip_fingerprint(request),
    )


def issue_password_reset(user: User | None, phone: str, request) -> OtpResult:
    """Issue a reset code for ``user`` when the limits allow it.

    ``user`` may be ``None``: the caller still gets a uniform response, and the
    attempt is still counted so the limit cannot be used to probe for accounts.
    """
    phone_hash = phone_fingerprint(phone)
    ip_hash = ip_fingerprint(request)

    if recent_sends(phone_hash) >= settings.OTP_MAX_REQUESTS:
        _audit(user=user, phone=phone, status=SmsAuditLog.Status.RATE_LIMITED, request=request)
        return OtpResult(
            sent=False,
            retry_after=settings.OTP_WINDOW_MINUTES * 60,
            reason="phone_limit",
        )

    if recent_ip_attempts(ip_hash) >= settings.OTP_MAX_PER_IP:
        _audit(user=user, phone=phone, status=SmsAuditLog.Status.RATE_LIMITED, request=request)
        return OtpResult(
            sent=False,
            retry_after=settings.OTP_WINDOW_MINUTES * 60,
            reason="ip_limit",
        )

    if user is None:
        # Count the attempt but send nothing. The response is identical, so an
        # attacker cannot tell a registered number from an unknown one.
        _audit(user=None, phone=phone, status=SmsAuditLog.Status.SKIPPED, request=request)
        return OtpResult(sent=False, reason="unknown_number")

    # Only the newest code may be valid.
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    code = PasswordResetCode.issue(user)
    message = (
        f"Your {settings.PLATFORM_NAME} password reset code is {code}. "
        f"It expires in {settings.OTP_TTL_MINUTES} minutes. "
        "If you did not request this, ignore it and keep your password."
    )
    delivered = send_sms(user.phone, message, purpose=PASSWORD_RESET)
    status = (
        SmsAuditLog.Status.SENT
        if delivered
        else SmsAuditLog.Status.SKIPPED
        if not sms_available()
        else SmsAuditLog.Status.FAILED
    )
    _audit(user=user, phone=user.phone, status=status, request=request)
    # The code is never logged. In the console backend the operator sees the
    # message text in the server log; that backend must not run in production.
    return OtpResult(sent=delivered, reason="" if delivered else "provider_unavailable")


def verify_and_consume(user: User, code: str) -> bool:
    """Check the newest unused code and consume it on success."""
    row = user.password_reset_codes.filter(used_at__isnull=True).first()
    if row is None or not row.verify_code(code):
        return False
    row.consume()
    # A used code invalidates every older one for the same account.
    user.password_reset_codes.filter(used_at__isnull=True).update(used_at=timezone.now())
    return True
