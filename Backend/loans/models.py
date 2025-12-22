from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from users.models import User, normalize_ke_phone


class Loan(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        DISBURSED = "DISBURSED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans")
    amount = models.PositiveIntegerField()  # KES
    service_fee = models.PositiveIntegerField()  # KES
    mpesa_phone = models.CharField(max_length=12)  # 2547XXXXXXXX
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # payment/disbursement linkage
    service_fee_paid = models.BooleanField(default=False)
    paystack_reference = models.CharField(max_length=64, unique=True)
    last_event = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["paystack_reference"]),
        ]

    def clean(self):
        self.mpesa_phone = normalize_ke_phone(self.mpesa_phone)

    @staticmethod
    def compute_service_fee(amount: int) -> int:
        # Must match frontend tiers. Server is authoritative.
        a = int(amount)
        if a < 1000 or a > 60000:
            raise ValueError("Loan amount out of range.")

        tiers = [
            (1000, 1000, 200),
            (2000, 2000, 290),
            (3000, 5000, 680),
            (6000, 11000, 1200),
            (12000, 22000, 2200),
            (23000, 30000, 3200),
            (31000, 40000, 4200),
            (41000, 50000, 5200),
            (51000, 60000, 6200),
        ]
        for mn, mx, fee in tiers:
            if mn <= a <= mx:
                return fee
        raise ValueError("Service fee not configured for this amount.")

    @classmethod
    def user_has_active_loan(cls, user: User) -> bool:
        return cls.objects.filter(user=user).exclude(status=cls.Status.DISBURSED).exists()