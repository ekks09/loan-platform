from rest_framework import serializers
from .models import Loan


class LoanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Loan
        fields = [
            "id",
            "amount",
            "service_fee",
            "mpesa_phone",
            "status",
            "service_fee_paid",
            "paystack_reference",
            "last_event",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "service_fee",
            "status",
            "service_fee_paid",
            "paystack_reference",
            "last_event",
            "created_at",
            "updated_at",
        ]


class LoanApplySerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1000, max_value=60000)
    mpesa_phone = serializers.CharField(max_length=12)

    def validate_mpesa_phone(self, value):
        if not value.startswith("254") or not value.isdigit():
            raise serializers.ValidationError("Invalid Kenyan phone number")
        return value
