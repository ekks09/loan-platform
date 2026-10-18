"""Loyalty, savings vault and the M-Biashara credit health score.

Design rules enforced here:

* Nothing in this app creates money. Cash-back only accrues from a repayment
  that was confirmed as repaid, and repayment collection is not implemented in
  this build, so every balance starts — and stays — at zero.
* The tier ladder is derived from append-only :class:`RepaymentRecord` rows,
  never from a field a client can send.
* Moving money in and out of the vault is an internal ledger transfer guarded
  by ``SELECT … FOR UPDATE``; it can never exceed the recorded cash-back.
* The credit score is deterministic and explainable. Every point is attributed
  to a factor that is shown to the user.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Tier(models.TextChoices):
    BASE = "BASE", "Base"
    SILVER = "SILVER", "Silver"
    GOLD = "GOLD", "Gold"


TIER_ORDER = {Tier.BASE: 0, Tier.SILVER: 1, Tier.GOLD: 2}

# Three consecutive on-time repayments unlock Gold.
GOLD_STREAK = 3
SILVER_STREAK = 1
GOLD_FEE_DISCOUNT_BPS = 150
MAX_LIMIT_WITH_GOLD = 60000


class LoyaltyAccount(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loyalty_account",
    )
    tier = models.CharField(max_length=10, choices=Tier.choices, default=Tier.BASE)
    tier_since = models.DateTimeField(default=timezone.now)

    #: Consecutive on-time repayments. Reset to zero by a late repayment.
    streak = models.PositiveIntegerField(default=0)
    lifetime_repaid_kes = models.PositiveIntegerField(default=0)
    cashback_kes = models.PositiveIntegerField(default=0)
    vault_kes = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "loyalty accounts"

    def __str__(self) -> str:
        return f"Loyalty {self.user_id} ({self.tier})"

    @property
    def available_cashback_kes(self) -> int:
        return self.cashback_kes


class RepaymentRecord(models.Model):
    """A provider-confirmed repayment.

    Rows are written by the (future) repayment service only. There is no API
    that lets a user or the browser create one.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="repayments"
    )
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="repayment_records"
    )
    reference = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    due_on = models.DateField()
    paid_at = models.DateTimeField()
    on_time = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at"]
        indexes = [
            models.Index(fields=["user", "-paid_at"], name="repay_user_time_idx"),
            models.Index(fields=["on_time"], name="repay_on_time_idx"),
        ]

    def __str__(self) -> str:
        return f"Repayment {self.reference}"


class LoyaltyEvent(models.Model):
    """Append-only ledger for tier, cash-back and vault movements."""

    class Kind(models.TextChoices):
        REPAYMENT = "repayment", "Repayment recorded"
        CASHBACK = "cashback", "Cash-back credited"
        TIER_UP = "tier_up", "Tier upgraded"
        TIER_RESET = "tier_reset", "Streak reset"
        VAULT_LOCK = "vault_lock", "Locked into vault"
        VAULT_RELEASE = "vault_release", "Released from vault"

    account = models.ForeignKey(
        LoyaltyAccount, on_delete=models.CASCADE, related_name="events"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount_kes = models.IntegerField(default=0, help_text="Signed: credits positive")
    reference = models.CharField(max_length=64, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "-created_at"], name="loyalty_event_time_idx")]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount_kes:+d}"


class VaultEntry(models.Model):
    """One movement in or out of the savings vault."""

    class Direction(models.TextChoices):
        LOCK = "lock", "Locked into vault"
        RELEASE = "release", "Released to cash-back"

    account = models.ForeignKey(
        LoyaltyAccount, on_delete=models.CASCADE, related_name="vault_entries"
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    amount_kes = models.PositiveIntegerField()
    balance_after_kes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "-created_at"], name="vault_entry_time_idx")]

    def __str__(self) -> str:
        return f"Vault {self.direction} {self.amount_kes}"


class CreditSnapshot(models.Model):
    """A point-in-time copy of the credit health score and its factors."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_snapshots"
    )
    score = models.PositiveSmallIntegerField()
    band = models.CharField(max_length=16)
    factors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"], name="credit_user_time_idx")]

    def __str__(self) -> str:
        return f"Credit {self.score} ({self.band})"
