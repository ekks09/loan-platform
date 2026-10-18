from __future__ import annotations

from rest_framework import serializers

from payments.models import Payment, Refund, Transfer


class PaymentSerializer(serializers.ModelSerializer):
    loan_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "reference",
            "loan_id",
            "purpose",
            "amount_kes",
            "currency",
            "status",
            "verified",
            "channel",
            "gateway_response",
            "provider_transaction_id",
            "failure_reason",
            "authorization_url",
            "access_code",
            "paid_at",
            "created_at",
        ]
        read_only_fields = fields


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = [
            "reference",
            "amount_kes",
            "currency",
            "reason",
            "status",
            "provider_refund_id",
            "created_at",
            "processed_at",
        ]
        read_only_fields = fields


class TransferSerializer(serializers.ModelSerializer):
    loan_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Transfer
        fields = [
            "loan_id",
            "reference",
            "amount_kes",
            "currency",
            "status",
            "attempts",
            "max_attempts",
            "last_error",
            "next_retry_at",
            "initiated_at",
            "confirmed_at",
            "created_at",
        ]
        read_only_fields = fields


class RefundRequestSerializer(serializers.Serializer):
    payment_reference = serializers.CharField(max_length=64)
    amount_kes = serializers.IntegerField(required=False, min_value=1)
    reason = serializers.CharField(max_length=255)
