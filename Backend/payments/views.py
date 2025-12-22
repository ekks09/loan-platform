from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings
from django.db import models, transaction
from django.urls import path
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from loans.models import Loan
from .paystack import PaystackClient, PaystackError


# Models must live in models.py, but folder structure disallows it.
# Therefore we define Django models here is NOT acceptable in real Django.
# To keep code executable and within the fixed file list, we implement
# PaymentRecord via unmanaged table with migrations avoided? Not allowed.
# Instead: we store payment and transfer fields on Loan and ensure reference uniqueness,
# and keep idempotency on Loan.paystack_reference + service_fee_paid.
#
# Also store transfer reference and recipient code on Loan without adding files.
# We'll extend Loan via dynamic fields is impossible.
#
# Therefore: implement PaymentRecord as a separate table using Django's "models.Model"
# in this file and register app label "payments" so Django can migrate it.
# Django supports models declared in any imported module under the app; as long as
# module is imported on startup, migrations will detect it if makemigrations is run.
# core/urls imports payments.views, ensuring import at runtime.

class Payment(models.Model):
    class Meta:
        app_label = "payments"
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["loan_id"]),
        ]

    loan_id = models.IntegerField()
    user_id = models.IntegerField()
    reference = models.CharField(max_length=64, unique=True)
    amount_kes = models.PositiveIntegerField()
    verified = models.BooleanField(default=False)
    paystack_transaction_id = models.CharField(max_length=64, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Transfer(models.Model):
    class Meta:
        app_label = "payments"
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["loan_id"]),
        ]

    loan_id = models.IntegerField(unique=True)  # one transfer per loan
    reference = models.CharField(max_length=64, unique=True)
    recipient_code = models.CharField(max_length=64, blank=True, default="")
    initiated = models.BooleanField(default=False)
    status = models.CharField(max_length=32, blank=True, default="")  # success/failed/reversed/...
    raw_last_event = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


def ensure_payment_record_created(loan: Loan) -> None:
    Payment.objects.get_or_create(
        reference=loan.paystack_reference,
        defaults={
            "loan_id": loan.id,
            "user_id": loan.user_id,
            "amount_kes": loan.service_fee,
            "verified": False,
        },
    )


def _ensure_transfer_record(loan: Loan) -> Transfer:
    # Separate transfer reference from payment reference for clarity/idempotency
    ref = f"TR_{loan.paystack_reference}"
    t, _ = Transfer.objects.get_or_create(
        loan_id=loan.id,
        defaults={"reference": ref, "initiated": False, "status": ""},
    )
    return t


def _internal_email_for_phone(phone_254: str) -> str:
    return f"user-{phone_254}@{settings.INTERNAL_EMAIL_DOMAIN}"


class ConfirmPaymentSerializer:
    @staticmethod
    def validate_reference(reference: str) -> str:
        r = (reference or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_=-]{8,64}", r):
            raise ValidationError("Invalid reference.")
        return r


class ConfirmPaymentView(APIView):
    def post(self, request):
        ref = ConfirmPaymentSerializer.validate_reference(request.data.get("reference"))

        try:
            loan = Loan.objects.select_for_update().get(user=request.user, paystack_reference=ref)
        except Loan.DoesNotExist:
            raise ValidationError("Loan not found for this reference.")

        with transaction.atomic():
            # lock payment row
            try:
                pay = Payment.objects.select_for_update().get(reference=ref)
            except Payment.DoesNotExist:
                raise ValidationError("Payment record missing.")

            if pay.user_id != request.user.id:
                raise PermissionDenied("Not allowed.")

            if pay.verified and loan.service_fee_paid:
                return Response({"ok": True, "status": "already_verified"})

            client = PaystackClient()
            v = client.verify_transaction(ref)

            # Validate Paystack status and amount
            if v.get("status") != "success":
                raise ValidationError("Payment not successful.")
            amount_kobo = int(v.get("amount") or 0)
            currency = (v.get("currency") or "").upper()
            if currency != settings.APP_FEE_CURRENCY:
                raise ValidationError("Invalid payment currency.")
            expected_kobo = int(loan.service_fee) * 100
            if amount_kobo != expected_kobo:
                raise ValidationError("Invalid payment amount.")
            if v.get("reference") != ref:
                raise ValidationError("Reference mismatch.")

            # Mark verified idempotently
            pay.verified = True
            pay.paystack_transaction_id = str(v.get("id") or "")
            pay.paid_at = timezone.now()
            pay.save(update_fields=["verified", "paystack_transaction_id", "paid_at"])

            loan.service_fee_paid = True
            loan.last_event = "Service fee verified"
            loan.save(update_fields=["service_fee_paid", "last_event", "updated_at"])

            # Policy: approve automatically after fee verification, then disburse.
            if loan.status == Loan.Status.PENDING:
                loan.status = Loan.Status.APPROVED
                loan.last_event = "Loan approved"
                loan.save(update_fields=["status", "last_event", "updated_at"])

            # Initiate disbursement via Paystack Transfer API (idempotent via Transfer table)
            transfer = _ensure_transfer_record(loan)
            if not transfer.initiated:
                # Create recipient
                recipient = client.create_transfer_recipient(
                    name=f"LoanUser {request.user.phone}",
                    phone_254=loan.mpesa_phone,
                )
                recipient_code = recipient.get("recipient_code")
                if not recipient_code:
                    raise ValidationError("Recipient creation failed.")

                transfer.recipient_code = recipient_code
                transfer.save(update_fields=["recipient_code", "updated_at"])

                # Transfer principal amount to borrower (full amount)
                # Service fee is collected separately; disbursement is the loan amount.
                tr = client.initiate_transfer(
                    amount_kobo=int(loan.amount) * 100,
                    recipient_code=recipient_code,
                    reference=transfer.reference,
                    reason=f"Loan disbursement for loan #{loan.id}",
                )
                transfer.initiated = True
                transfer.status = (tr.get("status") or "")
                transfer.raw_last_event = tr
                transfer.save(update_fields=["initiated", "status", "raw_last_event", "updated_at"])

                loan.last_event = "Disbursement initiated"
                loan.save(update_fields=["last_event", "updated_at"])

            return Response({"ok": True, "status": "verified"})


def mark_transfer_event(event: str, transfer_reference: str, raw: dict) -> None:
    # Called from webhook with transaction.atomic around it
    try:
        transfer = Transfer.objects.select_for_update().get(reference=transfer_reference)
    except Transfer.DoesNotExist:
        return

    transfer.raw_last_event = raw
    if event == "transfer.success":
        transfer.status = "success"
    elif event == "transfer.failed":
        transfer.status = "failed"
    elif event == "transfer.reversed":
        transfer.status = "reversed"
    else:
        transfer.status = event.replace("transfer.", "")
    transfer.save(update_fields=["status", "raw_last_event", "updated_at"])

    # Update associated loan consistently
    try:
        loan = Loan.objects.select_for_update().get(id=transfer.loan_id)
    except Loan.DoesNotExist:
        return

    if transfer.status == "success":
        loan.status = Loan.Status.DISBURSED
        loan.last_event = "Disbursed to M-Pesa"
        loan.save(update_fields=["status", "last_event", "updated_at"])
    elif transfer.status in ("failed", "reversed"):
        # Keep it APPROVED but not disbursed; operational staff can retry transfer.
        if loan.status != Loan.Status.DISBURSED:
            loan.last_event = f"Disbursement {transfer.status}"
            loan.save(update_fields=["last_event", "updated_at"])


# URLConf for payments app (imported in core/urls.py)
urlpatterns = [
    path("confirm/", ConfirmPaymentView.as_view(), name="confirm-payment"),
]