from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def normalize_ke_phone(phone: str) -> str:
    """
    Normalize a Kenyan phone number to strict 2547XXXXXXXX format.
    Raises ValidationError if invalid.
    """
    if not phone:
        raise ValidationError("Phone number is required.")

    phone = str(phone).strip().replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("0"):
        phone = "254" + phone[1:]

    if phone.startswith("7"):
        phone = "254" + phone

    if not phone.startswith("2547") or len(phone) != 12:
        raise ValidationError("Invalid Kenyan phone number format.")

    return phone


class Loan(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"       # created, service fee not yet paid
        APPROVED = "APPROVED"     # service fee paid, awaiting disbursement
        DISBURSED = "DISBURSED"   # money sent, loan completed

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loans",
    )

    amount = models.PositiveIntegerField(help_text="Loan amount in KES")
    service_fee = models.PositiveIntegerField(
        help_text="Service fee in KES",
        editable=False,
    )

    mpesa_phone = models.CharField(
        max_length=12,
        help_text="Kenyan phone number in 2547XXXXXXXX format",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )

    service_fee_paid = models.BooleanField(default=False)

    # NOTE:
    # Paystack reference should live on a Payment model.
    # NOT unique to avoid webhook retries breaking production.
    paystack_reference = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        db_index=True,
    )

    last_event = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
        ]
        ordering = ["-created_at"]

    def clean(self):
        if not self.amount:
            raise ValidationError("Loan amount is required.")

        self.mpesa_phone = normalize_ke_phone(self.mpesa_phone)

        if self.amount < 1000 or self.amount > 60000:
            raise ValidationError("Loan amount must be between 1,000 and 60,000 KES.")

    def save(self, *args, **kwargs):
        self.full_clean()

        if not self.service_fee:
            self.service_fee = self.compute_service_fee(self.amount)

        super().save(*args, **kwargs)

    @staticmethod
    def compute_service_fee(amount: int) -> int:
        """
        Compute service fee based on loan amount tiers.
        """
        tiers = [
            (1000, 1000, 200),
            (2000, 2000, 290),
            (3000, 5000, 680),
            (6000, 11000, 1200),
            (12000, 22000, 2200),
            (23000, 32000, 3200),
            (33000, 42000, 4200),
            (43000, 52000, 5200),
            (53000, 60000, 6000),
        ]

        for minimum, maximum, fee in tiers:
            if minimum <= amount <= maximum:
                return fee

        raise ValidationError("Service fee not configured for this amount.")

    @classmethod
    def user_has_active_loan(cls, user) -> bool:
        """
        A user has an ACTIVE loan if:
        - service fee is paid
        - loan is approved or disbursed
        """
        return cls.objects.filter(
            user=user,
            status__in=[cls.Status.APPROVED, cls.Status.DISBURSED],
            service_fee_paid=True,
        ).exists()

    def __str__(self):
        return f"Loan #{self.id} | {self.user} | KES {self.amount}"
