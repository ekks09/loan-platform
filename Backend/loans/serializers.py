from __future__ import annotations

from rest_framework import serializers

from loans.models import Loan
from users.phone import InvalidPhoneNumber, normalize_ke_phone


class LoanSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    next_action = serializers.CharField(read_only=True)
    can_be_disbursed = serializers.BooleanField(read_only=True)
    offer_is_expired = serializers.BooleanField(read_only=True)
    offer_is_accepted = serializers.BooleanField(read_only=True)

    class Meta:
        model = Loan
        fields = [
            "id",
            "amount",
            "service_fee",
            "currency",
            "mpesa_phone",
            "status",
            "status_label",
            "next_action",
            "service_fee_paid",
            "paystack_reference",
            "last_event",
            "approved_at",
            "rejection_reason",
            "offer_expires_at",
            "offer_accepted_at",
            "offer_terms_version",
            "offer_is_expired",
            "offer_is_accepted",
            "can_be_disbursed",
            "disbursed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_status_label(self, obj: Loan) -> str:
        return obj.STATUS_LABELS.get(obj.status, obj.status)


class LoanApplySerializer(serializers.Serializer):
    amount = serializers.IntegerField(
        min_value=Loan.MIN_AMOUNT, max_value=Loan.MAX_AMOUNT
    )
    mpesa_phone = serializers.CharField(max_length=20)

    def validate_amount(self, value: int) -> int:
        # Surfaces the same deterministic schedule the model will use.
        Loan.compute_service_fee(value)
        return value

    def validate_mpesa_phone(self, value: str) -> str:
        try:
            return normalize_ke_phone(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError(str(exc)) from exc


class OfferAcceptSerializer(serializers.Serializer):
    terms_version = serializers.CharField(max_length=32)


class StaffDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OfferExpirySerializer(serializers.Serializer):
    expiry_hours = serializers.IntegerField(min_value=1, max_value=168, required=False)
