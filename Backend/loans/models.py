from __future__ import annotations
from django.conf import settings
from django.db import models


def normalize_ke_phone(phone: str) -> str:
    """Normalize a Kenyan phone number to 2547XXXXXXXX format."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if phone.startswith("7"):
        phone = "254" + phone
    return phone


class Loan(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        DISBURSED = "DISBURSED"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans"
    )
    amount = models.PositiveIntegerField()  # KES
    service_fee = models.PositiveIntegerField()  # KES
    mpesa_phone = models.CharField(max_length=12)  # 2547XXXXXXXX
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Payment / disbursement linkage
    service_fee_paid = models.BooleanField(default=False)
    paystack_reference = models.CharField(max_length=64, unique=True, blank=True, null=True)
    last_event = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["paystack_reference"]),
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

        # Updated tiers to match frontend
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
        """Returns True if the user has any loan not yet disbursed."""
        return cls.objects.filter(user=user).exclude(status=cls.Status.DISBURSED).exists()

    def __str__(self):
        return f"Loan #{self.id} - {self.user} - KES {self.amount}"
