from __future__ import annotations

import secrets
from django.conf import settings
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from .models import Loan
from .serializers import LoanApplySerializer, LoanSerializer
from payments.paystack import PaystackClient
from payments.views import ensure_payment_record_created


def internal_email_for_phone(phone_254: str) -> str:
    return f"user-{phone_254}@{settings.INTERNAL_EMAIL_DOMAIN}"


class ApplyLoanView(APIView):
    def post(self, request):
        serializer = LoanApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        mpesa_phone = serializer.validated_data["mpesa_phone"]

        if Loan.user_has_active_loan(request.user):
            raise ValidationError("You already have an active loan.")

        service_fee = Loan.compute_service_fee(amount)

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

            ensure_payment_record_created(loan)

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

        return Response({
            "loan_id": loan.id,
            "payment_reference": reference,
            "amount_kobo": service_fee * 100,
            "email": email,
            "paystack_authorization_url": init.get("authorization_url"),
            "paystack_access_code": init.get("access_code"),
        })


class CurrentLoanView(APIView):
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

        data = {
            "has_loan": True,
            "status": loan.status,
            "amount": loan.amount,
            "service_fee": loan.service_fee,
            "mpesa_phone": loan.mpesa_phone,
            "created_at": loan.created_at,
            "last_event": loan.last_event,
        }

        serializer = LoanSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.data)
