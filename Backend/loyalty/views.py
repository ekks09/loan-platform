"""Read-mostly loyalty endpoints.

The browser can read its own loyalty state and move cash-back in or out of the
vault. It can never create a repayment, a cash-back credit or a score snapshot
for another account; those are internal service calls.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from loyalty.models import LoyaltyAccount, LoyaltyEvent, Tier, VaultEntry
from loyalty.services import (
    GOLD_STREAK,
    account_for,
    credit_factors,
    discounted_fee,
    lock_into_vault,
    max_limit_kes,
    release_from_vault,
    score_for,
    tier_for_streak,
)
from loans.models import Loan


def _payload(user, account: LoyaltyAccount) -> dict:
    score, band, factors = score_for(user, account)
    sample = Loan.compute_service_fee(10_000)
    return {
        "tier": account.tier,
        "tier_label": Tier(account.tier).label,
        "tier_since": account.tier_since,
        "streak": account.streak,
        "streak_target": GOLD_STREAK,
        "next_tier": tier_for_streak(account.streak + 1),
        "lifetime_repaid_kes": account.lifetime_repaid_kes,
        "cashback_kes": account.cashback_kes,
        "vault_kes": account.vault_kes,
        "fee_discount_bps": 150 if account.tier == Tier.GOLD else 0,
        "fee_example": {
            "amount_kes": 10_000,
            "standard_fee_kes": sample,
            "member_fee_kes": discounted_fee(sample, account),
        },
        "max_limit_kes": max_limit_kes(account),
        "credit": {
            "score": score,
            "band": band,
            "min": 300,
            "max": 850,
            "factors": [f.as_dict() for f in factors],
            "portable": False,
            "disclosure": (
                "This score is built from activity recorded on M-Biashara only. "
                "It is not a credit bureau score, is not shared with other lenders "
                "and cannot be transferred to another provider."
            ),
        },
        "vault": {
            "yield_bps": 0,
            "yield_note": (
                "The vault holds cash-back only. No yield is accrued or promised "
                "in this build, and it is not lent out."
            ),
        },
    }


class LoyaltySummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        account = account_for(request.user)
        payload = _payload(request.user, account)
        payload["events"] = [
            {
                "kind": event.kind,
                "amount_kes": event.amount_kes,
                "note": event.note,
                "reference": event.reference,
                "created_at": event.created_at,
            }
            for event in account.events.all()[:20]
        ]
        payload["vault_entries"] = [
            {
                "direction": entry.direction,
                "amount_kes": entry.amount_kes,
                "balance_after_kes": entry.balance_after_kes,
                "created_at": entry.created_at,
            }
            for entry in account.vault_entries.all()[:20]
        ]
        return Response(payload)


class VaultMoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "vault"

    def post(self, request):
        raw = request.data.get("amount_kes")
        direction = str(request.data.get("direction") or "").lower()
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            return Response(
                {"error": {"code": "invalid_amount", "message": "Enter a whole shilling amount."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        account = account_for(request.user)
        try:
            if direction == "lock":
                lock_into_vault(account, amount)
            elif direction == "release":
                release_from_vault(account, amount)
            else:
                return Response(
                    {"error": {"code": "invalid_direction", "message": "Direction must be lock or release."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as exc:
            return Response(
                {"error": {"code": "vault_rejected", "message": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        account.refresh_from_db()
        return Response(_payload(request.user, account))
