from __future__ import annotations

from django.conf import settings
from django.db import models


def normalize_ke_phone(phone: str) -> str:
    """Normalize a Kenyan phone number to 2547XXXXXXXX format."""
    if not phone:
        return phone
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if phone.startswith("7"):
        phone = "254" + phone
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

    amount = models.PositiveIntegerField()  # KES
    service_fee = models.PositiveIntegerField()  # KES
    mpesa_phone = models.CharField(max_length=12)  # 2547XXXXXXXX

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )

    service_fee_paid = models.BooleanField(default=False)

    # NOTE:
    # Paystack reference should ideally live on a Payment model.
    # Kept here for backward compatibility, but NOT used to block retries.
    paystack_reference = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        null=True,
    )

    last_event = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def save(self, *args, **kwargs):
        self.mpesa_phone = normalize_ke_phone(self.mpesa_phone)

        if not self.service_fee:
            self.service_fee = self.compute_service_fee(self.amount)

        super().save(*args, **kwargs)

    @staticmethod
    def compute_service_fee(amount: int) -> int:
        """Compute service fee based on loan amount tiers."""
        a = int(amount)

        if a < 1000 or a > 60000:
            raise ValueError("Loan amount out of range.")

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

        for mn, mx, fee in tiers:
            if mn <= a <= mx:
                return fee

        raise ValueError("Service fee not configured for this amount.")

    @classmethod
    def user_has_active_loan(cls, user) -> bool:
        """
        A user is considered to have an ACTIVE loan ONLY if:
        - service fee is paid
        - loan is approved (or later)
        """
        return cls.objects.filter(
            user=user,
            status__in=[cls.Status.APPROVED],
            service_fee_paid=True,
        ).exists()

    def __str__(self):
        return f"Loan #{self.id} - {self.user} - KES {self.amount}"
