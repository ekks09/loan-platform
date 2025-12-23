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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["loan_id"]),
        ]


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
