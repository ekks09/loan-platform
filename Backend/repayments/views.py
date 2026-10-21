from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from loans.models import Loan, RepaymentSchedule
from payments.models import Payment
from repayments.serializers import (
    RepaymentScheduleSerializer,
    RepaymentSummarySerializer,
)
from repayments.services import generate_schedule, check_loan_settlement


def _get_loan_for_user(request, loan_id: int) -> Loan:
    loan = Loan.objects.filter(pk=loan_id, user=request.user).first()
    if loan is None:
        from core.errors import LoanNotFound
        raise LoanNotFound()
    return loan


class RepaymentScheduleView(APIView):
    """Get repayment schedule for a loan."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)
        schedules = loan.repayment_schedules.all().order_by("installment_number")

        # Calculate summary
        total_outstanding = sum(s.outstanding_kes for s in schedules)
        next_schedule = schedules.filter(
            status__in=[RepaymentSchedule.Status.PENDING, RepaymentSchedule.Status.PARTIAL, RepaymentSchedule.Status.OVERDUE]
        ).first()

        return Response(
            RepaymentSummarySerializer({
                "total_outstanding_kes": total_outstanding,
                "next_due_on": next_schedule.due_on if next_schedule else None,
                "next_installment_number": next_schedule.installment_number if next_schedule else None,
                "schedules": schedules,
            }).data
        )


class InitiateRepaymentView(APIView):
    """Initialize a repayment payment for the next due installment."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)

        # Check if loan is in a state that allows repayment
        if loan.status != Loan.Status.DISBURSED:
            from core.errors import InvalidTransition
            raise InvalidTransition("Repayment only available for disbursed loans.")

        # Get next due schedule
        next_schedule = loan.repayment_schedules.filter(
            status__in=[RepaymentSchedule.Status.PENDING, RepaymentSchedule.Status.PARTIAL, RepaymentSchedule.Status.OVERDUE]
        ).first()

        if not next_schedule:
            from core.errors import InvalidTransition
            raise InvalidTransition("No outstanding installments.")

        # Initialize payment via payments service
        from payments.services import initialize_repayment_payment
        try:
            payment = initialize_repayment_payment(loan, next_schedule, actor=request.user)
        except Exception as exc:
            from core.errors import DomainError
            raise DomainError(str(exc))

        return Response({
            "ok": True,
            "payment": {
                "reference": payment.reference,
                "authorization_url": payment.authorization_url,
                "access_code": payment.access_code,
                "amount_kes": payment.amount_kes,
            },
            "schedule": RepaymentScheduleSerializer(next_schedule).data,
        }, status=status.HTTP_201_CREATED)


class RepaymentCallbackView(APIView):
    """Handle repayment payment callback (from Paystack redirect).

    This page is shown after the user completes payment on Paystack.
    It polls for payment status and shows the result.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)
        reference = request.query_params.get("reference")

        if not reference:
            return Response(
                {"error": "Missing reference parameter"},
                status=status.HTTP_400_BAD_REQUEST
            )

        payment = Payment.objects.filter(reference=reference, loan=loan, user=request.user).first()
        if not payment:
            from core.errors import LoanNotFound
            raise LoanNotFound()

        return Response({
            "payment": {
                "reference": payment.reference,
                "status": payment.status,
                "amount_kes": payment.amount_kes,
                "channel": payment.channel,
                "last_verified_at": payment.last_verified_at,
            },
            "loan": {
                "status": loan.status,
                "next_action": loan.next_action,
            },
        })
