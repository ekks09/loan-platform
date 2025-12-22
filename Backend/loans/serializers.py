from __future__ import annotations

from rest_framework import serializers
from users.models import normalize_ke_phone
from .models import Loan


class ApplyLoanSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1000, max_value=60000)
    mpesa_phone = serializers.CharField()

    def validate_mpesa_phone(self, value: str) -> str:
        try:
            return normalize_ke_phone(value)
        except ValueError as e:
            raise serializers.ValidationError(str(e))

    def validate_amount(self, value: int) -> int:
        # Will also be validated when computing service fee.
        return int(value)


class CurrentLoanSerializer(serializers.Serializer):
    has_loan = serializers.BooleanField()
    status = serializers.CharField(required=False)
    amount = serializers.IntegerField(required=False)
    service_fee = serializers.IntegerField(required=False)
    mpesa_phone = serializers.CharField(required=False)
    created_at = serializers.DateTimeField(required=False)
    last_event = serializers.CharField(required=False)