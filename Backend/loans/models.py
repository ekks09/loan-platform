from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.errors import InvalidLoanAmount, InvalidTransition
from users.phone import normalize_ke_phone


class Loan(models.Model):
    """A loan application and its lifecycle.

    The fee schedule is contiguous by construction and validated by a system
    check. ``INITIAL``/``APPROVED`` are applications; money only moves after an
    explicit staff disbursement action and provider confirmation.
    """

    MIN_AMOUNT = 100
    MAX_AMOUNT = 60000

    # DRAFT pricing proposal (2026-10). Absolute fees increase with principal
    # and the marginal rate rises across six bands. This is a commercial
    # decision: it is versioned, displayed publicly, validated by a system
    # check and gated behind FEE_COLLECTION_APPROVED. Enabling collection
    # requires the approvals documented in docs/CREDIT_PRODUCT_SPEC.md.
    FEE_SCHEDULE_VERSION = "2026-10-draft"
    FEE_MINIMUM = 45
    FEE_ROUNDING = 1  # truncate to whole shillings (round down)

    # (minimum amount, maximum amount, rate in basis points)
    FEE_RATE_BANDS = (
        (100, 999, 900),       # 9.0%
        (1000, 4999, 1000),    # 10.0%
        (5000, 9999, 1050),    # 10.5%
        (10000, 19999, 1100),  # 11.0%
        (20000, 34999, 1150),  # 11.5%
        (35000, 60000, 1200),  # 12.0%
    )

    # Representative amount for each band in the public fee table. Band 1 is
    # shown at KES 500 because it is a typical small amount; the rest use the
    # top of the band. Every fee is computed, never typed twice.
    FEE_EXAMPLE_AMOUNTS = (500, 4999, 9999, 19999, 34999, 60000)

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        APPROVED = "APPROVED", "Approved — offer issued"
        DISBURSEMENT_PENDING = "DISBURSEMENT_PENDING", "Disbursement pending"
        DISBURSED = "DISBURSED", "Disbursed"
        REVERSED = "REVERSED", "Disbursement reversed"
        REPAID = "REPAID", "Repaid"
        REJECTED = "REJECTED", "Rejected"
        EXPIRED = "EXPIRED", "Offer expired"
        CANCELLED = "CANCELLED", "Cancelled"
        FAILED = "FAILED", "Failed"

    OPEN_STATUSES = (Status.PENDING, Status.APPROVED, Status.DISBURSEMENT_PENDING)
    CLOSED_STATUSES = (
        Status.DISBURSED,
        Status.REVERSED,
        Status.REPAID,
        Status.REJECTED,
        Status.EXPIRED,
        Status.CANCELLED,
        Status.FAILED,
    )

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.APPROVED, Status.REJECTED, Status.CANCELLED},
        Status.APPROVED: {
            Status.DISBURSEMENT_PENDING,
            Status.EXPIRED,
            Status.CANCELLED,
            Status.REJECTED,
        },
        Status.DISBURSEMENT_PENDING: {
            Status.DISBURSED,
            Status.FAILED,
            Status.APPROVED,  # reconciliation reset; reason is mandatory
        },
        # A provider reversal after settlement must be representable; the
        # compensating ledger entry is posted by the payment service.
        Status.DISBURSED: {Status.REVERSED},
        Status.REVERSED: set(),
        Status.REPAID: set(),
        Status.REJECTED: set(),
        Status.EXPIRED: set(),
        Status.CANCELLED: set(),
        # A failed disbursement can be recovered when the provider later
        # confirms a successful transfer during reconciliation.
        Status.FAILED: {Status.DISBURSEMENT_PENDING, Status.DISBURSED},
    }

    STATUS_LABELS = {
        Status.PENDING: "Pending review",
        Status.APPROVED: "Approved — awaiting acceptance",
        Status.DISBURSEMENT_PENDING: "Disbursement pending",
        Status.DISBURSED: "Disbursed",
        Status.REVERSED: "Disbursement reversed",
        Status.REPAID: "Repaid",
        Status.REJECTED: "Not approved",
        Status.EXPIRED: "Offer expired",
        Status.CANCELLED: "Cancelled",
        Status.FAILED: "Failed",
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="loans"
    )
    amount = models.PositiveIntegerField(help_text="Loan principal in KES")
    service_fee = models.PositiveIntegerField(help_text="Fee in KES", editable=False)
    currency = models.CharField(max_length=3, default="KES")
    mpesa_phone = models.CharField(max_length=12)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING
    )

    # Deprecated mirror kept for reporting; the Payment record is authoritative.
    service_fee_paid = models.BooleanField(default=False)

    paystack_reference = models.CharField(
        max_length=64, blank=True, null=True, unique=True
    )
    paystack_authorization_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Paystack checkout URL for service fee payment",
    )
    paystack_access_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Paystack access code for inline payment",
    )
    last_event = models.CharField(max_length=255, blank=True, default="")

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loans_approved",
    )
    rejection_reason = models.TextField(blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    offer_accepted_at = models.DateTimeField(null=True, blank=True)
    offer_terms_version = models.CharField(max_length=32, blank=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="loans_user_status_idx"),
            models.Index(fields=["status", "created_at"], name="loans_status_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(
                    status__in=["PENDING", "APPROVED", "DISBURSEMENT_PENDING"]
                ),
                name="loans_one_open_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Loan #{self.pk} | KES {self.amount} | {self.status}"

    # ------------------------------------------------------------------
    # Validation / persistence
    # ------------------------------------------------------------------
    def clean(self):
        super().clean()
        if not (self.MIN_AMOUNT <= int(self.amount or 0) <= self.MAX_AMOUNT):
            raise ValidationError(
                {"amount": f"Loan amount must be between {self.MIN_AMOUNT:,} and "
                           f"{self.MAX_AMOUNT:,} KES."}
            )
        self.mpesa_phone = normalize_ke_phone(self.mpesa_phone)
        if self.currency != "KES":
            raise ValidationError({"currency": "Only KES is supported."})

    def save(self, *args, **kwargs):
        creating = self._state.adding
        self.mpesa_phone = normalize_ke_phone(self.mpesa_phone)
        if creating:
            if not (self.MIN_AMOUNT <= int(self.amount or 0) <= self.MAX_AMOUNT):
                raise ValidationError(
                    {"amount": f"Loan amount must be between {self.MIN_AMOUNT:,} and "
                               f"{self.MAX_AMOUNT:,} KES."}
                )
            if not self.service_fee:
                self.service_fee = self.compute_service_fee(self.amount)
            if self.currency != "KES":
                raise ValidationError({"currency": "Only KES is supported."})
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------
    @classmethod
    def _validated_amount(cls, amount) -> int:
        if isinstance(amount, bool) or not isinstance(amount, int):
            # ``bool`` is an int subclass; both are rejected explicitly.
            raise InvalidLoanAmount()
        if not (cls.MIN_AMOUNT <= amount <= cls.MAX_AMOUNT):
            raise InvalidLoanAmount(
                f"Loan amount must be between {cls.MIN_AMOUNT:,} and "
                f"{cls.MAX_AMOUNT:,} KES."
            )
        return amount

    @classmethod
    def _rate_bps(cls, amount: int) -> int:
        for minimum, maximum, rate_bps in cls.FEE_RATE_BANDS:
            if minimum <= amount <= maximum:
                return rate_bps
        raise InvalidLoanAmount(
            "No service fee is configured for this amount. Please contact support."
        )

    @classmethod
    def compute_service_fee(cls, amount: int) -> int:
        """Deterministic, rounded fee for every supported amount.

        ``rate * amount`` is computed in integer basis points, then rounded to
        down to the nearest ``FEE_ROUNDING`` shillings. There is no silent
        default: unsupported or non-integer input raises.
        """
        amount = cls._validated_amount(amount)
        rate_bps = cls._rate_bps(amount)
        # fee = amount * (bps / 10000), truncated down to the nearest
        # FEE_ROUNDING shillings. Truncation is deliberate: the published
        # examples quote KES 499 at KES 4,999, KES 1,049 at KES 9,999,
        # KES 2,199 at KES 19,999 and KES 4,024 at KES 34,999. The divisor is
        # expressed in the same units for any step, so changing FEE_ROUNDING
        # cannot silently scale the fee.
        step = cls.FEE_ROUNDING
        fee = ((amount * rate_bps) // (10_000 * step)) * step
        return max(cls.FEE_MINIMUM, fee)

    @classmethod
    def fee_percent(cls, amount: int) -> float:
        return round(cls.compute_service_fee(amount) / amount * 100, 2)

    @classmethod
    def fee_rate_percent(cls, amount: int) -> float:
        return round(cls._rate_bps(cls._validated_amount(amount)) / 100, 2)

    @classmethod
    def pricing_rows(cls) -> list[dict]:
        rows = []
        for index, (minimum, maximum, rate_bps) in enumerate(cls.FEE_RATE_BANDS):
            example_amount = cls.FEE_EXAMPLE_AMOUNTS[index]
            rows.append(
                {
                    "min": minimum,
                    "max": maximum,
                    "rate": round(rate_bps / 100, 2),
                    "example_amount": example_amount,
                    "example_fee": cls.compute_service_fee(example_amount),
                }
            )
        return rows

    @classmethod
    def validate_schedule(cls) -> list[str]:
        errors: list[str] = []
        bands = cls.FEE_RATE_BANDS
        if not bands:
            return ["Fee schedule is empty."]
        if bands[0][0] != cls.MIN_AMOUNT:
            errors.append("Fee schedule does not start at the minimum amount.")
        if bands[-1][1] != cls.MAX_AMOUNT:
            errors.append("Fee schedule does not end at the maximum amount.")
        previous_max = None
        previous_rate = None
        for minimum, maximum, rate_bps in bands:
            if minimum > maximum:
                errors.append(f"Invalid band {minimum}-{maximum}.")
            if rate_bps <= 0:
                errors.append(f"Non-positive rate in band {minimum}-{maximum}.")
            if previous_max is not None and minimum != previous_max + 1:
                errors.append(
                    f"Gap or overlap between {previous_max} and {minimum}."
                )
            if previous_rate is not None and rate_bps <= previous_rate:
                errors.append(
                    f"Rate does not increase at band {minimum}-{maximum}."
                )
            previous_max = maximum
            previous_rate = rate_bps
        if cls.FEE_MINIMUM <= 0:
            errors.append("Minimum fee must be positive.")
        if cls.FEE_ROUNDING <= 0:
            errors.append("Fee rounding must be positive.")
        # Absolute fees must increase at every band boundary.
        if not errors:
            boundary_fees = [
                cls.compute_service_fee(boundary)
                for boundary in (band[0] for band in bands)
            ]
            boundary_fees.append(cls.compute_service_fee(cls.MAX_AMOUNT))
            for earlier, later in zip(boundary_fees, boundary_fees[1:]):
                if later <= earlier:
                    errors.append(
                        "Absolute fee does not increase across a band boundary."
                    )
                    break
        return errors

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def can_transition_to(self, new_status: str) -> bool:
        if new_status == self.status:
            return True
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(
        self, new_status: str, *, actor=None, reason: str = "", save: bool = True
    ) -> "Loan":
        if new_status == self.status:
            return self
        if not self.can_transition_to(new_status):
            raise InvalidTransition(
                f"Cannot move loan from {self.status} to {new_status}.",
                details={"from": self.status, "to": new_status},
            )
        old_status = self.status
        self.status = new_status
        self.last_event = reason or self.STATUS_LABELS.get(new_status, new_status)
        update_fields = ["status", "last_event", "updated_at"]
        if new_status == self.Status.DISBURSED and self.disbursed_at is None:
            self.disbursed_at = timezone.now()
            update_fields.append("disbursed_at")
        if save:
            self.save(update_fields=update_fields)
        LoanEvent.objects.create(
            loan=self,
            from_status=old_status,
            to_status=new_status,
            actor=actor,
            reason=reason,
        )
        return self

    # ------------------------------------------------------------------
    # Offers
    # ------------------------------------------------------------------
    def quoted_service_fee(self) -> int:
        """The fee this borrower would pay today, including a Gold discount.

        The pricing model itself stays tier-agnostic: ``compute_service_fee``
        is the published schedule and nothing here changes it. The loyalty
        layer only decides whether a member discount applies, and the result
        is floored at the published minimum.
        """
        base_fee = self.compute_service_fee(self.amount)
        # Imported lazily: loyalty imports Loan for its own floor, so a
        # module-level import here would be circular.
        from loyalty.services import account_for, discounted_fee

        return discounted_fee(base_fee, account_for(self.user))

    def issue_offer(self, *, actor, expiry_hours: int = 48) -> "Loan":
        if self.status != self.Status.PENDING:
            raise InvalidTransition("Only pending applications can be approved.")
        # Re-quote at approval time. A member who reached Gold between
        # application and review keeps the discount; a member whose tier was
        # reset does not benefit from a stale quote.
        self.service_fee = self.quoted_service_fee()
        self.approved_at = timezone.now()
        self.approved_by = actor
        self.offer_expires_at = timezone.now() + timedelta(hours=expiry_hours)
        self.offer_terms_version = settings.TERMS_VERSION
        self.offer_accepted_at = None
        self.save(
            update_fields=[
                "service_fee",
                "approved_at",
                "approved_by",
                "offer_expires_at",
                "offer_terms_version",
                "offer_accepted_at",
                "updated_at",
            ]
        )
        self.transition_to(
            self.Status.APPROVED,
            actor=actor,
            reason="Offer issued",
        )
        return self

    @property
    def offer_is_expired(self) -> bool:
        return bool(
            self.offer_expires_at and timezone.now() >= self.offer_expires_at
        )

    @property
    def offer_is_accepted(self) -> bool:
        return self.offer_accepted_at is not None

    def accept_offer(self, *, version: str, actor=None) -> "Loan":
        if self.status != self.Status.APPROVED:
            raise InvalidTransition("There is no open offer to accept for this loan.")
        if self.offer_is_expired:
            self.transition_to(
                self.Status.EXPIRED, actor=actor, reason="Offer expired before acceptance"
            )
            raise InvalidTransition("This offer has expired. Please apply again.")
        if version != self.offer_terms_version:
            raise InvalidTransition(
                "The offer terms have changed. Reload the offer and review it again."
            )
        self.offer_accepted_at = timezone.now()
        self.save(update_fields=["offer_accepted_at", "updated_at"])
        LoanEvent.objects.create(
            loan=self,
            from_status=self.status,
            to_status=self.status,
            actor=actor,
            reason=f"Offer accepted (terms {version})",
        )
        return self

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @classmethod
    def user_has_active_loan(cls, user) -> bool:
        return cls.objects.filter(user=user, status__in=cls.OPEN_STATUSES).exists()

    @classmethod
    def open_for_user(cls, user):
        return cls.objects.filter(user=user, status__in=cls.OPEN_STATUSES).first()

    @property
    def can_be_disbursed(self) -> bool:
        if self.status != self.Status.APPROVED:
            return False
        if settings.REQUIRE_OFFER_ACCEPTANCE and not self.offer_is_accepted:
            return False
        if self.offer_is_expired:
            return False
        return True

    @property
    def next_action(self) -> str:
        if self.status == self.Status.PENDING:
            return "await_review"
        if self.status == self.Status.APPROVED:
            if self.offer_is_expired:
                return "offer_expired"
            if not self.offer_is_accepted:
                return "accept_offer"
            if settings.FEE_COLLECTION_MODE == "upfront" and not self.service_fee_paid:
                return "pay_service_fee"
            return "await_disbursement"
        return "none"


class LoanEvent(models.Model):
    """Append-only audit trail for loan state changes."""

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loan_events",
    )
    reason = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["loan", "created_at"], name="loans_event_loan_idx")]

    def __str__(self) -> str:
        return f"Loan {self.loan_id}: {self.from_status} -> {self.to_status}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("LoanEvent records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("LoanEvent records are immutable.")


class RepaymentSchedule(models.Model):
    """Planned installment for a disbursed loan.

    Created when a loan reaches DISBURSED status. Immutable once created;
    only the amount_paid and status fields change as payments are allocated.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARTIAL = "PARTIAL", "Partially paid"
        PAID = "PAID", "Paid"
        OVERDUE = "OVERDUE", "Overdue"
        WAIVED = "WAIVED", "Waived"

    loan = models.ForeignKey(
        "Loan", on_delete=models.CASCADE, related_name="repayment_schedules"
    )
    installment_number = models.PositiveIntegerField()
    due_on = models.DateField()
    expected_principal_kes = models.PositiveIntegerField()
    expected_interest_kes = models.PositiveIntegerField(default=0)
    expected_fees_kes = models.PositiveIntegerField(default=0)
    expected_total_kes = models.PositiveIntegerField()
    amount_paid_kes = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    late_fee_kes = models.PositiveIntegerField(default=0)
    late_fee_applied_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["loan", "installment_number"]
        indexes = [
            models.Index(
                fields=["loan", "status"], name="repay_sched_loan_status_idx"
            ),
            models.Index(fields=["due_on", "status"], name="repay_sched_due_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["loan", "installment_number"],
                name="repay_sched_unique_installment",
            ),
        ]

    def __str__(self) -> str:
        return f"Schedule #{self.installment_number} for Loan {self.loan_id}"

    @property
    def outstanding_kes(self) -> int:
        return max(0, self.expected_total_kes - self.amount_paid_kes)

    @property
    def is_overdue(self) -> bool:
        if self.status in {self.Status.PAID, self.Status.WAIVED}:
            return False
        return timezone.now().date() > self.due_on

    def can_allocate(self, amount_kes: int) -> bool:
        return self.outstanding_kes > 0 and amount_kes > 0


class RepaymentAllocation(models.Model):
    """Links a provider-confirmed payment to the schedule installments it paid.

    One payment (e.g., a Paystack charge.success) can cover multiple installments
    (partial payments, overpayments). Each allocation row is immutable.
    """

    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.PROTECT,
        related_name="repayment_allocations",
    )
    schedule = models.ForeignKey(
        RepaymentSchedule,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    amount_kes = models.PositiveIntegerField()
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-allocated_at"]
        indexes = [
            models.Index(fields=["payment"], name="repay_alloc_payment_idx"),
            models.Index(fields=["schedule"], name="repay_alloc_schedule_idx"),
        ]

    def __str__(self) -> str:
        return f"Allocation {self.amount_kes} from {self.payment.reference} to schedule {self.schedule_id}"
