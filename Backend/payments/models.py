from __future__ import annotations

from django.db import models


class Payment(models.Model):
    loan_id = models.IntegerField()
    user_id = models.IntegerField()
    reference = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    verified = models.BooleanField(default=False)
    paystack_transaction_id = models.CharField(max_length=64, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    
    # Paystack initialization response fields
    authorization_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Paystack checkout URL",
    )
    access_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Paystack access code for inline payment",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["loan_id"]),
        ]

    def __str__(self):
        return f"Payment {self.reference} | Loan #{self.loan_id}"


class Transfer(models.Model):
    loan_id = models.IntegerField(unique=True)
    reference = models.CharField(max_length=64, unique=True)
    recipient_code = models.CharField(max_length=64, blank=True, default="")
    initiated = models.BooleanField(default=False)
    status = models.CharField(max_length=32, blank=True, default="")
    raw_last_event = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "payments"
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["loan_id"]),
        ]

    def __str__(self):
        return f"Transfer {self.reference} | Loan #{self.loan_id}"
