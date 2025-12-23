from __future__ import annotations

import re
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.urls import path

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from loans.models import Loan
from .models import Payment, Transfer
from .paystack import PaystackClient


def ensure_payment_record_created(loan: Loan) -> None:
    Payment.objects.get_or_create(
        reference=loan.paystack_reference,
        defaults={
            "loan_id": loan.id,
            "user_id": loan.user_id,
            "amount_kes": loan.service_fee,
        },
    )


def _ensure_transfer_record(loan: Loan) -> Transfer:
    ref = f"TR_{loan.paystack_reference}"
    t, _ = Transfer.objects.get_or_create(
        loan_id=loan.id,
        defaults={"reference": ref},
    )
    return t


class ConfirmPaymentView(APIView):
    def post(self, request):
        reference = (request.data.get("reference") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_=-]{8,64}", reference):
            raise ValidationError("Invalid reference")

        try:
            loan = Loan.objects.get(user=request.user, paystack_reference=reference)
        except Loan.DoesNotExist:
            raise ValidationError("Loan not found")

        with transaction.atomic():
            try:
                payment = Payment.objects.select_for_update().get(reference=reference)
            except Payment.DoesNotExist:
                raise ValidationError("Payment record missing")

            if payment.user_id != request.user.id:
                raise PermissionDenied("Forbidden")

            if payment.verified:
                return Response({"ok": True, "status": "already_verified"})

            client = PaystackClient()
            v = client.verify_transaction(reference)

            if v.get("status") != "success":
                raise ValidationError("Payment not successful")

            if int(v.get("amount", 0)) != loan.service_fee * 100:
                raise ValidationError("Invalid amount")

            payment.verified = True
            payment.paystack_transaction_id = str(v.get("id"))
            payment.paid_at = timezone.now()
            payment.save()

            loan.service_fee_paid = True
            loan.status = Loan.Status.APPROVED
            loan.last_event = "Service fee verified"
            loan.save()

            transfer = _ensure_transfer_record(loan)
            if not transfer.initiated:
                recipient = client.create_transfer_recipient(
                    name=f"LoanUser {request.user.phone}",
                    phone_254=loan.mpesa_phone,
                )

                transfer.recipient_code = recipient["recipient_code"]
                transfer.initiated = True
                transfer.status = "initiated"
                transfer.save()

                client.initiate_transfer(
                    amount_kobo=loan.amount * 100,
                    recipient_code=transfer.recipient_code,
                    reference=transfer.reference,
                    reason=f"Loan disbursement #{loan.id}",
                )

                loan.status = Loan.Status.DISBURSED
                loan.last_event = "Loan disbursed"
                loan.save()

        return Response({"ok": True, "status": "verified"})


urlpatterns = [
    path("confirm/", ConfirmPaymentView.as_view(), name="confirm-payment"),
]
