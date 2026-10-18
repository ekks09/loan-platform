"""Deterministic loyalty, vault and credit-health rules.

Every public function is pure with respect to external services: it reads and
writes only the database, and it never invents a repayment, a cash-back credit
or a score. Anything the user sees can be recomputed from
:class:`loyalty.models.RepaymentRecord` and :class:`loyalty.models.LoyaltyEvent`.
"""
from __future__ import annotations

from django.conf import settings

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from loyalty.models import (
    GOLD_FEE_DISCOUNT_BPS,
    GOLD_STREAK,
    MAX_LIMIT_WITH_GOLD,
    SILVER_STREAK,
    CreditSnapshot,
    LoyaltyAccount,
    LoyaltyEvent,
    RepaymentRecord,
    Tier,
    VaultEntry,
)

#: Cash-back rate on each confirmed, on-time repayment (basis points).
CASHBACK_RATE_BPS = 100  # 1.0%
#: Gold members earn double.
GOLD_CASHBACK_MULTIPLIER = 2

SCORE_MIN = 300
SCORE_MAX = 850


@dataclass(frozen=True)
class CreditFactor:
    key: str
    label: str
    points: int
    detail: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "points": self.points,
            "detail": self.detail,
        }


def account_for(user) -> LoyaltyAccount:
    account, _ = LoyaltyAccount.objects.get_or_create(user=user)
    return account


def tier_for_streak(streak: int) -> str:
    if streak >= GOLD_STREAK:
        return Tier.GOLD
    if streak >= SILVER_STREAK:
        return Tier.SILVER
    return Tier.BASE


def fee_discount_bps(account: LoyaltyAccount) -> int:
    return GOLD_FEE_DISCOUNT_BPS if account.tier == Tier.GOLD else 0


def max_limit_kes(account: LoyaltyAccount) -> int:
    if account.tier == Tier.GOLD:
        return MAX_LIMIT_WITH_GOLD
    return 20000


def discounted_fee(base_fee: int, account: LoyaltyAccount) -> int:
    """Apply the Gold discount, rounded down to a whole shilling.

    The floor is never lowered: the discount cannot take a fee below the
    published KES 45 minimum, so a Gold member still pays the same minimum as
    everybody else.
    """
    discount = fee_discount_bps(account)
    if not discount:
        return base_fee
    from loans.models import Loan

    reduced = (base_fee * (10_000 - discount)) // 10_000
    return max(Loan.FEE_MINIMUM, reduced)


@transaction.atomic
def record_repayment(
    *,
    user,
    loan,
    reference: str,
    amount_kes: int,
    due_on,
    paid_at=None,
    on_time: bool = True,
) -> RepaymentRecord:
    """Record one provider-confirmed repayment and apply its loyalty effects.

    Idempotent on ``reference``: replaying the same provider event returns the
    original row and changes no balances.
    """
    existing = RepaymentRecord.objects.filter(reference=reference).first()
    if existing is not None:
        return existing

    paid_at = paid_at or timezone.now()
    record = RepaymentRecord.objects.create(
        user=user,
        loan=loan,
        reference=reference,
        amount_kes=int(amount_kes),
        due_on=due_on,
        paid_at=paid_at,
        on_time=bool(on_time),
    )

    account = account_for(user)
    account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)
    if on_time:
        account.streak += 1
        account.lifetime_repaid_kes += record.amount_kes
        rate = CASHBACK_RATE_BPS * (
            GOLD_CASHBACK_MULTIPLIER if account.tier == Tier.GOLD else 1
        )
        cashback = (record.amount_kes * rate) // 10_000
        account.cashback_kes += cashback
        LoyaltyEvent.objects.create(
            account=account,
            kind=LoyaltyEvent.Kind.REPAYMENT,
            amount_kes=record.amount_kes,
            reference=reference,
        )
        if cashback:
            LoyaltyEvent.objects.create(
                account=account,
                kind=LoyaltyEvent.Kind.CASHBACK,
                amount_kes=cashback,
                reference=reference,
            )
    else:
        if account.streak:
            LoyaltyEvent.objects.create(
                account=account,
                kind=LoyaltyEvent.Kind.TIER_RESET,
                amount_kes=0,
                reference=reference,
                note=f"Late repayment reset a {account.streak}-payment streak.",
            )
        account.streak = 0

    new_tier = tier_for_streak(account.streak)
    if new_tier != account.tier:
        previous = account.tier
        account.tier = new_tier
        account.tier_since = timezone.now()
        if new_tier != Tier.BASE:
            LoyaltyEvent.objects.create(
                account=account,
                kind=LoyaltyEvent.Kind.TIER_UP,
                amount_kes=0,
                reference=reference,
                note=f"{previous} to {new_tier}",
            )
    account.save()
    return record


@transaction.atomic
def lock_into_vault(account: LoyaltyAccount, amount_kes: int) -> VaultEntry:
    """Move cash-back into the vault. Never touches loan principal."""
    amount_kes = int(amount_kes)
    if amount_kes <= 0:
        raise ValueError("Lock a positive amount.")
    account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)
    if amount_kes > account.cashback_kes:
        raise ValueError("The vault can only hold cash-back you have earned.")
    account.cashback_kes -= amount_kes
    account.vault_kes += amount_kes
    account.save(update_fields=["cashback_kes", "vault_kes", "updated_at"])
    entry = VaultEntry.objects.create(
        account=account,
        direction=VaultEntry.Direction.LOCK,
        amount_kes=amount_kes,
        balance_after_kes=account.vault_kes,
    )
    LoyaltyEvent.objects.create(
        account=account,
        kind=LoyaltyEvent.Kind.VAULT_LOCK,
        amount_kes=-amount_kes,
    )
    return entry


@transaction.atomic
def release_from_vault(account: LoyaltyAccount, amount_kes: int) -> VaultEntry:
    amount_kes = int(amount_kes)
    if amount_kes <= 0:
        raise ValueError("Release a positive amount.")
    account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)
    if amount_kes > account.vault_kes:
        raise ValueError("That is more than the vault holds.")
    account.vault_kes -= amount_kes
    account.cashback_kes += amount_kes
    account.save(update_fields=["cashback_kes", "vault_kes", "updated_at"])
    entry = VaultEntry.objects.create(
        account=account,
        direction=VaultEntry.Direction.RELEASE,
        amount_kes=amount_kes,
        balance_after_kes=account.vault_kes,
    )
    LoyaltyEvent.objects.create(
        account=account,
        kind=LoyaltyEvent.Kind.VAULT_RELEASE,
        amount_kes=amount_kes,
    )
    return entry


def credit_factors(user, account: LoyaltyAccount | None = None) -> list[CreditFactor]:
    """Explain the score with the exact points each factor contributes."""
    from loans.models import Loan

    account = account or account_for(user)

    identity_points = 220 if user.is_verified else 60
    identity = CreditFactor(
        key="identity",
        label="Identity verified",
        points=identity_points,
        detail=(
            "National ID and selfie reviewed and approved."
            if user.is_verified
            else "Verification is still pending or needs a new upload."
        ),
    )

    lifetime = account.lifetime_repaid_kes
    repaid_points = min(180, lifetime // 500)
    repaid = CreditFactor(
        key="repaid",
        label="Repayment history",
        points=repaid_points,
        detail=(
            f"KES {lifetime:,} repaid on time." if lifetime else "No repayment recorded yet."
        ),
    )

    streak_points = min(120, account.streak * 40)
    streak = CreditFactor(
        key="streak",
        label="On-time streak",
        points=streak_points,
        detail=(
            f"{account.streak} consecutive on-time repayment(s)."
            if account.streak
            else "A late or missing repayment resets this to zero."
        ),
    )

    settled = Loan.objects.filter(
        user=user, status__in=[Loan.Status.DISBURSED, Loan.Status.REPAID]
    ).count()
    settled_points = min(90, settled * 30)
    track_record = CreditFactor(
        key="track_record",
        label="Completed applications",
        points=settled_points,
        detail=(
            f"{settled} application(s) reached disbursement."
            if settled
            else "No completed application yet."
        ),
    )

    vault_points = 30 if account.vault_kes > 0 else 0
    vault = CreditFactor(
        key="vault",
        label="Savings vault",
        points=vault_points,
        detail=(
            f"KES {account.vault_kes:,} held in your vault."
            if account.vault_kes
            else "Lock cash-back in the vault to add this factor."
        ),
    )

    tenure_days = 0
    if user.created_at:
        tenure_days = max(0, (timezone.now() - user.created_at).days)
    tenure_points = min(60, tenure_days // 5)
    tenure = CreditFactor(
        key="tenure",
        label="Account age",
        points=tenure_points,
        detail=f"{tenure_days} day(s) with {settings.PLATFORM_NAME}.",
    )

    base = SCORE_MIN
    factors = [identity, repaid, streak, track_record, vault, tenure]
    capped_total = min(SCORE_MAX - SCORE_MIN, sum(f.points for f in factors))
    # Keep the arithmetic visible: base + sum == score.
    factors.append(
        CreditFactor(key="base", label="Starting score", points=base, detail="Every new account starts at 300.")
    )
    if base + capped_total > SCORE_MAX:
        raise RuntimeError("credit score factor arithmetic invariant violated")
    return factors


def score_for(user, account: LoyaltyAccount | None = None) -> tuple[int, str, list[CreditFactor]]:
    factors = credit_factors(user, account)
    total = sum(f.points for f in factors)
    total = max(SCORE_MIN, min(SCORE_MAX, total))
    if total >= 750:
        band = "Excellent"
    elif total >= 650:
        band = "Good"
    elif total >= 500:
        band = "Building"
    else:
        band = "Starting"
    return total, band, factors


def snapshot_score(user) -> CreditSnapshot:
    score, band, factors = score_for(user)
    return CreditSnapshot.objects.create(
        user=user,
        score=score,
        band=band,
        factors=[f.as_dict() for f in factors],
    )
