"""Payment, transfer, refund, webhook and ledger models.

Money is stored in whole Kenyan shillings (the product prices in whole
shillings) and converted to minor units only at the Paystack boundary. Status
changes go through validated transitions; every consequential event is
recorded in an append-only audit table.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.errors import InvalidTransition


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending initialisation"
        INITIALIZED = "INITIALIZED", "Checkout created"
        SUCCEEDED = "SUCCEEDED", "Paid"
        FAILED = "FAILED", "Failed"
        ABANDONED = "ABANDONED", "Abandoned"
        REFUND_PENDING = "REFUND_PENDING", "Refund pending"
        REFUNDED = "REFUNDED", "Refunded"

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.INITIALIZED, Status.SUCCEEDED, Status.FAILED, Status.ABANDONED},
        Status.INITIALIZED: {
            Status.SUCCEEDED,
            Status.FAILED,
            Status.ABANDONED,
            Status.PENDING,  # re-initialisation after abandon
        },
        Status.SUCCEEDED: {Status.REFUND_PENDING, Status.REFUNDED},
        Status.FAILED: {Status.INITIALIZED, Status.PENDING},
        Status.ABANDONED: {Status.INITIALIZED, Status.PENDING},
        Status.REFUND_PENDING: {Status.REFUNDED, Status.SUCCEEDED},
        Status.REFUNDED: set(),
    }

    class Purpose(models.TextChoices):
        SERVICE_FEE = "service_fee", "Service fee"
        REPAYMENT = "repayment", "Loan repayment"  # reserved; not implemented

    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="payments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    purpose = models.CharField(
        max_length=16, choices=Purpose.choices, default=Purpose.SERVICE_FEE
    )
    reference = models.CharField(max_length=64, unique=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Kept for reporting compatibility; always mirrors status == SUCCEEDED.
    verified = models.BooleanField(default=False)

    provider = models.CharField(max_length=32, default="paystack")
    provider_transaction_id = models.CharField(max_length=64, blank=True, default="")
    provider_reference = models.CharField(max_length=64, blank=True, default="")
    channel = models.CharField(max_length=32, blank=True, default="")
    gateway_response = models.CharField(max_length=255, blank=True, default="")
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    authorization_url = models.URLField(
        max_length=500, blank=True, null=True, help_text="Paystack checkout URL"
    )
    access_code = models.CharField(
        max_length=100, blank=True, null=True, help_text="Paystack access code for inline payment"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="payments_status_created_idx"),
            models.Index(fields=["loan"], name="payments_loan_idx"),
            # Webhook reconciliation looks a charge up by the reference the
            # provider echoes back. Without this it is a full table scan on the
            # one path that must answer inside Paystack's 30-second budget.
            models.Index(
                fields=["provider", "provider_reference"],
                name="payments_provider_ref_idx",
            ),
            # Same lookup for the provider's own transaction id.
            models.Index(
                fields=["provider_transaction_id"], name="payments_provider_txn_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Payment {self.reference} ({self.status})"

    def can_transition_to(self, new_status: str) -> bool:
        if new_status == self.status:
            return True
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(
        self, new_status: str, *, reason: str = "", actor=None, save: bool = True
    ) -> "Payment":
        if new_status == self.status:
            return self
        if not self.can_transition_to(new_status):
            raise InvalidTransition(
                f"Cannot move payment from {self.status} to {new_status}.",
                details={"from": self.status, "to": new_status},
            )
        old_status = self.status
        self.status = new_status
        self.verified = new_status == self.Status.SUCCEEDED
        if new_status == self.Status.SUCCEEDED and self.paid_at is None:
            self.paid_at = timezone.now()
        if save:
            self.save()
        PaymentEvent.objects.create(
            payment=self,
            from_status=old_status,
            to_status=new_status,
            reason=reason[:255],
            actor=actor,
        )
        return self


class PaymentEvent(models.Model):
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="events"
    )
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    reason = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["payment"], name="payments_event_payment_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("PaymentEvent records are immutable.")
        super().save(*args, **kwargs)


class Transfer(models.Model):
    """Outbound loan disbursement. ``SUCCESS`` requires provider confirmation."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Not started"
        RECIPIENT_CREATED = "RECIPIENT_CREATED", "Recipient created"
        INITIATING = "INITIATING", "Request in flight"
        PENDING_PROVIDER = "PENDING_PROVIDER", "Accepted by provider, awaiting confirmation"
        SUCCESS = "SUCCESS", "Confirmed paid"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"
        UNKNOWN = "UNKNOWN", "Outcome unknown — reconcile before retry"
        CANCELLED = "CANCELLED", "Cancelled"

    RETRYABLE = {Status.PENDING, Status.RECIPIENT_CREATED, Status.FAILED, Status.UNKNOWN}
    TERMINAL = {Status.SUCCESS, Status.REVERSED, Status.CANCELLED}

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.RECIPIENT_CREATED, Status.CANCELLED, Status.FAILED},
        Status.RECIPIENT_CREATED: {Status.INITIATING, Status.CANCELLED, Status.FAILED},
        Status.INITIATING: {Status.PENDING_PROVIDER, Status.FAILED, Status.UNKNOWN},
        Status.PENDING_PROVIDER: {Status.SUCCESS, Status.FAILED, Status.REVERSED, Status.UNKNOWN},
        Status.UNKNOWN: {
            Status.PENDING_PROVIDER,
            Status.SUCCESS,
            Status.FAILED,
            Status.INITIATING,
            Status.CANCELLED,
        },
        Status.FAILED: {
            Status.RECIPIENT_CREATED,
            Status.INITIATING,
            Status.SUCCESS,  # reconciliation may discover a late success
            Status.CANCELLED,
        },
        Status.SUCCESS: {Status.REVERSED},
        Status.REVERSED: set(),
        Status.CANCELLED: set(),
    }

    loan = models.OneToOneField(
        "loans.Loan", on_delete=models.PROTECT, related_name="transfer"
    )
    reference = models.CharField(max_length=64, unique=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    recipient_code = models.CharField(max_length=64, blank=True, default="")
    provider_transfer_code = models.CharField(max_length=64, blank=True, default="")
    provider_reference = models.CharField(max_length=64, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    last_error = models.CharField(max_length=255, blank=True, default="")
    next_retry_at = models.DateTimeField(null=True, blank=True)
    initiated_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    raw_last_event = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"], name="transfers_status_retry_idx"),
            models.Index(fields=["loan"], name="transfers_loan_idx"),
            # transfer.* webhooks arrive with the provider's reference; the
            # dispatcher filters on it before any state change.
            models.Index(fields=["provider_reference"], name="transfers_provider_ref_idx"),
            models.Index(fields=["provider_transfer_code"], name="transfers_provider_code_idx"),
        ]

    def __str__(self) -> str:
        return f"Transfer {self.reference} ({self.status})"

    def can_transition_to(self, new_status: str) -> bool:
        if new_status == self.status:
            return True
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(
        self, new_status: str, *, reason: str = "", actor=None, save: bool = True
    ) -> "Transfer":
        if new_status == self.status:
            return self
        if not self.can_transition_to(new_status):
            raise InvalidTransition(
                f"Cannot move transfer from {self.status} to {new_status}.",
                details={"from": self.status, "to": new_status},
            )
        old_status = self.status
        self.status = new_status
        if new_status == self.Status.SUCCESS and self.confirmed_at is None:
            self.confirmed_at = timezone.now()
        if save:
            self.save()
        TransferEvent.objects.create(
            transfer=self,
            from_status=old_status,
            to_status=new_status,
            reason=reason[:255],
            actor=actor,
        )
        return self


class TransferEvent(models.Model):
    transfer = models.ForeignKey(
        Transfer, on_delete=models.CASCADE, related_name="events"
    )
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    reason = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["transfer"], name="transfers_event_transfer_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("TransferEvent records are immutable.")
        super().save(*args, **kwargs)


class Refund(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Requested"
        PROCESSING = "PROCESSING", "Submitted to provider"
        SUCCEEDED = "SUCCEEDED", "Refunded"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="refunds"
    )
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="refunds"
    )
    reference = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    reason = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    provider_refund_id = models.CharField(max_length=64, blank=True, default="")
    provider_response = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds_requested",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"], name="refunds_status_idx")]

    def __str__(self) -> str:
        return f"Refund {self.reference} ({self.status})"


class WebhookEvent(models.Model):
    """Every provider delivery, stored before processing for replay and audit."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        IGNORED = "IGNORED", "Ignored"
        FAILED = "FAILED", "Failed"

    provider = models.CharField(max_length=32, default="paystack")
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64, blank=True, default="")
    reference = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECEIVED
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status"], name="webhook_status_idx"),
            models.Index(fields=["reference"], name="webhook_reference_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} #{self.event_id} ({self.status})"


class LedgerEntry(models.Model):
    """Immutable double-entry ledger line.

    Each ``entry_group`` must balance to zero (sum of debits == sum of
    credits). Corrections are posted as compensating entries, never edits.
    """

    class Account(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        PLATFORM_REVENUE = "PLATFORM_REVENUE", "Platform revenue"
        PROVIDER_CLEARING = "PROVIDER_CLEARING", "Payment provider clearing"
        LOAN_RECEIVABLE = "LOAN_RECEIVABLE", "Loan receivable"
        BORROWER_PAYABLE = "BORROWER_PAYABLE", "Borrower payable"
        REFUNDS = "REFUNDS", "Refunds"

    class Direction(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"

    entry_group = models.UUIDField(default=uuid.uuid4, db_index=True)
    account = models.CharField(max_length=24, choices=Account.choices)
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    reference = models.CharField(max_length=64, blank=True, default="")
    memo = models.CharField(max_length=255, blank=True, default="")
    loan = models.ForeignKey(
        "loans.Loan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    transfer = models.ForeignKey(
        Transfer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    refund = models.ForeignKey(
        Refund,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entry_group"], name="ledger_group_idx"),
            models.Index(fields=["reference"], name="ledger_reference_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Ledger entries are immutable; post a compensating entry.")
        if self.amount_kes <= 0:
            raise ValueError("Ledger amount must be positive.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Ledger entries are immutable.")


class ReconciliationRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    dry_run = models.BooleanField(default=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Reconciliation {self.pk} ({self.status})"
