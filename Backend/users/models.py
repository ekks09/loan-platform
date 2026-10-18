"""User, consent, and password-reset models.

The phone number is the account identifier. National ID values are unique and
are never emitted in logs or non-staff API responses.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from users.phone import mask_phone, normalize_msisdn

# Supported markets: Kenya (254) and DR Congo (243). Stored without a "+".
MSISDN_REGEX = r"^(254(7|1)\d{8}|243[89]\d{8})$"
phone_validator = RegexValidator(
    regex=MSISDN_REGEX,
    message=(
        "Phone number must be a valid Kenyan (2547XXXXXXXX / 2541XXXXXXXX) "
        "or Congolese (2438XXXXXXXX / 2439XXXXXXXX) mobile number."
    ),
)
national_id_validator = RegexValidator(
    regex=r"^\d{6,10}$",
    message="National ID must be 6-10 digits.",
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(
        self,
        phone: str,
        national_id: str,
        password: str | None = None,
        **extra_fields,
    ):
        if not phone:
            raise ValueError("Phone is required")
        if not national_id:
            raise ValueError("National ID is required")

        phone = normalize_msisdn(phone)
        national_id = str(national_id).strip()
        national_id_validator(national_id)
        phone_validator(phone)

        user = self.model(phone=phone, national_id=national_id, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, phone: str, national_id: str, password: str | None = None, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(phone, national_id, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    phone = models.CharField(max_length=12, unique=True, validators=[phone_validator])
    national_id = models.CharField(max_length=10, validators=[national_id_validator])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Identity documents: only private storage paths are persisted. The legacy
    # *_url columns are retained for existing rows but are never written again.
    id_front_path = models.CharField(
        max_length=500, blank=True, help_text="Storage path for ID front"
    )
    id_back_path = models.CharField(
        max_length=500, blank=True, help_text="Storage path for ID back"
    )
    selfie_path = models.CharField(
        max_length=500, blank=True, help_text="Storage path for selfie"
    )
    id_front_url = models.URLField(
        max_length=500, blank=True, null=True, help_text="Front of National ID"
    )
    id_back_url = models.URLField(
        max_length=500, blank=True, null=True, help_text="Back of National ID"
    )
    selfie_url = models.URLField(
        max_length=500, blank=True, null=True, help_text="Selfie photo"
    )
    documents_uploaded_at = models.DateTimeField(null=True, blank=True)

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        help_text="ID verification status",
    )
    verification_notes = models.TextField(blank=True, help_text="Admin notes on verification")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verifications_done",
    )

    deletion_requested_at = models.DateTimeField(null=True, blank=True)

    # Every password change bumps this. Access tokens issued before the change
    # are rejected, so a password reset ends any session the old password
    # opened. Null means "never changed since registration".
    password_changed_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["national_id"]

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.password_changed_at = timezone.now()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["national_id"], name="users_user_national_id_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["verification_status"], name="users_verif_status_idx"),
            models.Index(fields=["created_at"], name="users_created_at_idx"),
        ]

    def __str__(self) -> str:
        return mask_phone(self.phone)

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED

    @property
    def has_uploaded_documents(self) -> bool:
        return all([self.id_front_path, self.id_back_path, self.selfie_path])

    @property
    def national_id_masked(self) -> str:
        value = self.national_id or ""
        if len(value) <= 4:
            return "*" * len(value)
        return "*" * (len(value) - 4) + value[-4:]

    def has_consent(self, purpose: str, version: str | None = None) -> bool:
        qs = self.consents.filter(purpose=purpose, withdrawn_at__isnull=True)
        if version:
            qs = qs.filter(version=version)
        return qs.exists()

    @property
    def has_current_consent(self) -> bool:
        return self.has_consent(
            ConsentRecord.Purpose.IDENTITY_PROCESSING, settings.CONSENT_VERSION
        ) and self.has_consent(ConsentRecord.Purpose.TERMS, settings.TERMS_VERSION)

    @property
    def can_borrow(self) -> bool:
        return (
            self.is_active
            and self.is_verified
            and self.has_uploaded_documents
            and self.has_current_consent
            and self.deletion_requested_at is None
        )


class ConsentRecord(models.Model):
    """Append-only record of user consent, with withdrawal support."""

    class Purpose(models.TextChoices):
        IDENTITY_PROCESSING = "identity_processing", "Identity document processing"
        TERMS = "terms", "Terms of service"
        MARKETING = "marketing", "Marketing communications"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consents")
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    version = models.CharField(max_length=32)
    granted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-granted_at"]
        indexes = [models.Index(fields=["user", "purpose"], name="users_consent_user_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "purpose", "version"],
                name="users_consent_unique_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.purpose}:{self.version}"


class PasswordResetCode(models.Model):
    """Hashed, expiring password reset code delivered over SMS."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_reset_codes"
    )
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    MAX_ATTEMPTS = 5
    TTL = timedelta(minutes=10)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def _hash(user_id: int, code: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode(),
            f"{user_id}:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def issue(cls, user: "User") -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        cls.objects.create(
            user=user,
            code_hash=cls._hash(user.pk, code),
            expires_at=timezone.now() + cls.TTL,
        )
        return code

    def verify_code(self, code: str) -> bool:
        if self.used_at or self.attempts >= self.MAX_ATTEMPTS:
            return False
        if timezone.now() >= self.expires_at:
            return False
        candidate = self._hash(self.user_id, str(code).strip())
        if not hmac.compare_digest(candidate, self.code_hash):
            self.attempts += 1
            self.save(update_fields=["attempts"])
            return False
        return True

    def consume(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])


class SmsAuditLog(models.Model):
    """Append-only record of every SMS the platform attempted to send.

    The message body (which can contain a one-time code) is never stored. The
    recipient is kept as a non-reversible HMAC so rate limiting can count
    requests per number without keeping a second copy of the phone number.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Handed to provider"
        SKIPPED = "skipped", "No provider configured"
        FAILED = "failed", "Provider rejected"
        RATE_LIMITED = "rate_limited", "Rate limited"

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs"
    )
    phone_masked = models.CharField(max_length=16)
    phone_hash = models.CharField(max_length=64)
    purpose = models.CharField(max_length=32)
    provider = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices)
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_hash", "created_at"], name="sms_phone_time_idx"),
            models.Index(fields=["ip_hash", "created_at"], name="sms_ip_time_idx"),
            models.Index(fields=["purpose", "created_at"], name="sms_purpose_time_idx"),
        ]

    def __str__(self) -> str:
        return f"SMS {self.purpose} to {self.phone_masked} ({self.status})"


class DocumentAccessLog(models.Model):
    """Audit trail for every staff view of an identity document."""

    staff_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="document_views"
    )
    subject_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="document_access_events"
    )
    document = models.CharField(max_length=16)  # id_front | id_back | selfie
    purpose = models.CharField(max_length=64, default="verification_review")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["subject_user"], name="users_docaccess_subject_idx")]
