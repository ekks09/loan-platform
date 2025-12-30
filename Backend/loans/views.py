from __future__ import annotations

import secrets
import logging

from django.conf import settings
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from .models import Loan
from .serializers import LoanApplySerializer
from payments.paystack import PaystackClient, PaystackError
from payments.views import ensure_payment_record_created

logger = logging.getLogger(__name__)


def internal_email_for_phone(phone_254: str) -> str:
    return f"user-{phone_254}@{settings.INTERNAL_EMAIL_DOMAIN}"


class ApplyLoanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logger.info("Loan application received from user %s", request.user.id)

        serializer = LoanApplySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = serializer.validated_data["amount"]
        mpesa_phone = serializer.validated_data["mpesa_phone"]

        # BLOCK ONLY REAL ACTIVE LOANS
        if Loan.user_has_active_loan(request.user):
            return Response(
                {"error": "You already have an active loan."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service_fee = Loan.compute_service_fee(amount)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # GENERATE PAYSTACK REFERENCE FIRST
                reference = "LPF_" + secrets.token_hex(12)

                loan = Loan.objects.create(
                    user=request.user,
                    amount=amount,
                    service_fee=service_fee,
                    mpesa_phone=mpesa_phone,
                    status=Loan.Status.PENDING,
                    service_fee_paid=False,
                    last_event="Awaiting service fee payment",
                    paystack_reference=reference,  # assign here
                )

                # ENSURE PAYMENT RECORD
                ensure_payment_record_created(loan)

        except Exception:
            logger.exception("Loan creation failed")
            return Response(
                {"error": "Failed to create loan."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # INIT PAYSTACK (reference already generated)
        try:
            client = PaystackClient()
            email = internal_email_for_phone(request.user.phone)

            init = client.initialize_transaction(
                email=email,
                amount_kobo=service_fee * 100,
                reference=reference,
                currency=settings.APP_FEE_CURRENCY,
                metadata={
                    "loan_id": loan.id,
                    "phone": request.user.phone,
                    "purpose": "service_fee",
                },
            )

            return Response(
                {
                    "loan_id": loan.id,
                    "payment_reference": reference,
                    "service_fee": service_fee,
                    "amount_kobo": service_fee * 100,
                    "email": email,
                    "paystack_authorization_url": init.get("authorization_url"),
                    "paystack_access_code": init.get("access_code"),
                },
                status=status.HTTP_201_CREATED,
            )

        except PaystackError as e:
            logger.error("Paystack init failed: %s", e)
            return Response(
                {
                    "loan_id": loan.id,
                    "service_fee": service_fee,
                    "message": "Loan created. Payment initialization failed. Retry.",
                },
                status=status.HTTP_201_CREATED,
            )


class CurrentLoanView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        loan = (
            Loan.objects.filter(
                user=request.user,
                status__in=[Loan.Status.PENDING, Loan.Status.APPROVED],
            )
            .order_by("-created_at")
            .first()
        )

        if not loan:
            return Response({"has_loan": False})

        return Response(
            {
                "has_loan": True,
                "id": loan.id,
                "status": loan.status,
                "amount": loan.amount,
                "service_fee": loan.service_fee,
                "service_fee_paid": loan.service_fee_paid,
                "mpesa_phone": loan.mpesa_phone,
                "created_at": loan.created_at.isoformat(),
                "last_event": loan.last_event,
            }
        )
