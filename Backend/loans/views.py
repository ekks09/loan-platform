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
from .serializers import LoanApplySerializer, LoanSerializer
from payments.paystack import PaystackClient, PaystackError
from payments.views import ensure_payment_record_created, internal_email_for_phone

logger = logging.getLogger(__name__)


class ApplyLoanView(APIView):
    """Apply for a new loan."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LoanApplySerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        amount = serializer.validated_data["amount"]
        mpesa_phone = serializer.validated_data["mpesa_phone"]

        # Check for existing active loan
        if Loan.user_has_active_loan(request.user):
            return Response(
                {"error": "You already have an active loan."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            service_fee = Loan.compute_service_fee(amount)
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                reference = "LPF_" + secrets.token_hex(12)

                loan = Loan.objects.create(
                    user=request.user,
                    amount=amount,
                    service_fee=service_fee,
                    mpesa_phone=mpesa_phone,
                    status=Loan.Status.PENDING,
                    service_fee_paid=False,
                    paystack_reference=reference,
                    last_event="Awaiting service fee payment",
                )

                # Create payment record
                ensure_payment_record_created(loan)

            # Initialize Paystack transaction
            client = PaystackClient()
            
            # Get user's phone for email generation
            user_phone = getattr(request.user, 'phone', mpesa_phone)
            email = internal_email_for_phone(user_phone)

            try:
                init_data = client.initialize_transaction(
                    email=email,
                    amount_kobo=service_fee * 100,
                    reference=reference,
                    currency=getattr(settings, 'APP_FEE_CURRENCY', 'KES'),
                    metadata={
                        "loan_id": loan.id,
                        "phone": user_phone,
                        "purpose": "service_fee",
                    },
                )

                return Response({
                    "loan_id": loan.id,
                    "payment_reference": reference,
                    "amount_kobo": service_fee * 100,
                    "service_fee": service_fee,
                    "email": email,
                    "paystack_authorization_url": init_data.get("authorization_url"),
                    "paystack_access_code": init_data.get("access_code"),
                })

            except PaystackError as e:
                logger.error(f"Paystack initialization failed: {e}")
                # Still return loan info even if Paystack fails
                # User can retry payment later
                return Response({
                    "loan_id": loan.id,
                    "payment_reference": reference,
                    "amount_kobo": service_fee * 100,
                    "service_fee": service_fee,
                    "email": email,
                    "paystack_error": str(e),
                    "message": "Loan created. Payment initialization failed, please retry.",
                }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Error creating loan: {e}")
            return Response(
                {"error": "Failed to create loan. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CurrentLoanView(APIView):
    """Get the current user's active loan."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        loan = (
            Loan.objects
            .filter(user=request.user)
            .exclude(status=Loan.Status.DISBURSED)
            .order_by("-created_at")
            .first()
        )

        if not loan:
            return Response({"has_loan": False})

        return Response({
            "has_loan": True,
            "id": loan.id,
            "status": loan.status,
            "amount": loan.amount,
            "service_fee": loan.service_fee,
            "service_fee_paid": loan.service_fee_paid,
            "mpesa_phone": loan.mpesa_phone,
            "paystack_reference": loan.paystack_reference,
            "created_at": loan.created_at.isoformat(),
            "last_event": loan.last_event,
        })
