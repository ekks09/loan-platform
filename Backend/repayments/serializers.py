from __future__ import annotations

from rest_framework import serializers

from loans.models import RepaymentSchedule, RepaymentAllocation


class RepaymentScheduleSerializer(serializers.ModelSerializer):
    outstanding_kes = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = RepaymentSchedule
        fields = [
            "id",
            "installment_number",
            "due_on",
            "expected_principal_kes",
            "expected_interest_kes",
            "expected_fees_kes",
            "expected_total_kes",
            "amount_paid_kes",
            "outstanding_kes",
            "status",
            "late_fee_kes",
            "late_fee_applied_at",
            "paid_at",
            "is_overdue",
            "created_at",
        ]
        read_only_fields = fields


class RepaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RepaymentAllocation
        fields = ["id", "amount_kes", "allocated_at"]
        read_only_fields = fields


class RepaymentSummarySerializer(serializers.Serializer):
    total_outstanding_kes = serializers.IntegerField()
    next_due_on = serializers.DateField(allow_null=True)
    next_installment_number = serializers.IntegerField(allow_null=True)
    schedules = RepaymentScheduleSerializer(many=True)
