from __future__ import annotations

import re
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, PermissionDenied

from loans.models import Loan
from .models import Payment, Transfer
from .paystack import PaystackClient, PaystackError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def ensure_payment_record_created(loan: Loan) -> Payment:
    """Ensure a payment record exists for the loan's service fee."""
    payment, created = Payment.objects.get_or_create(
        reference=loan.paystack_reference,
        defaults={
            "loan_id": loan.id,
            "user_id": loan.user_id,
            "amount_kes": loan.service_fee,
        },
    )
    if created:
        logger.info(f"Payment record created for loan {loan.id}")
    return payment


def _ensure_transfer_record(loan: Loan) -> Transfer:
    """Ensure a transfer record exists for loan disbursement."""
    ref = f"TR_{loan.paystack_reference}"
    transfer, created = Transfer.objects.get_or_create(
        loan_id=loan.id,
        defaults={"reference": ref},
    )
    if created:
        logger.info(f"Transfer record created for loan {loan.id}")
    return transfer


def _internal_email(phone_254: str) -> str:
    """Generate internal email for Paystack (users don't have real emails)."""
    return f"user-{phone_254}@{settings.INTERNAL_EMAIL_DOMAIN}"


def mark_transfer_event(event: str, transfer_reference: str, raw: dict) -> None:
    """
    Handle transfer webhook events from Paystack.
    Updates transfer and loan status based on the event type.
    """
    logger.info(f"Processing transfer event: {event} for reference: {transfer_reference}")
    
    try:
        transfer = Transfer.objects.select_for_update().get(reference=transfer_reference)
    except Transfer.DoesNotExist:
        logger.warning(f"Transfer not found for reference: {transfer_reference}")
        return

    transfer.raw_last_event = raw
    transfer.updated_at = timezone.now()

    try:
        loan = Loan.objects.get(id=transfer.loan_id)
    except Loan.DoesNotExist:
        logger.error(f"Loan not found for transfer: {transfer_reference}")
        transfer.status = "error_loan_missing"
        transfer.save()
        return

    if event == "transfer.success":
        transfer.status = "success"
        loan.status = Loan.Status.DISBURSED
        loan.last_event = "Loan disbursed successfully"
        logger.info(f"Transfer successful for loan {loan.id}")

    elif event == "transfer.failed":
        transfer.status = "failed"
        loan.last_event = f"Disbursement failed: {raw.get('reason', 'Unknown reason')}"
        logger.warning(f"Transfer failed for loan {loan.id}: {raw.get('reason')}")

    elif event == "transfer.reversed":
        transfer.status = "reversed"
        loan.last_event = "Disbursement reversed"
        logger.warning(f"Transfer reversed for loan {loan.id}")

    else:
        transfer.status = event.replace("transfer.", "")
        loan.last_event = f"Transfer event: {event}"
        logger.info(f"Transfer event {event} for loan {loan.id}")

    transfer.save()
    loan.save()


# ---------------------------------------------------------------------
# 1️⃣ INIT SERVICE FEE PAYMENT  (frontend: /payments/init/)
# ---------------------------------------------------------------------

class InitPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        loan_id = request.data.get("loan_id")
        if not loan_id:
            raise ValidationError("loan_id is required")

        try:
            loan = Loan.objects.get(id=loan_id, user=request.user)
        except Loan.DoesNotExist:
            raise ValidationError("Loan not found")

        if loan.service_fee_paid:
            raise ValidationError("Service fee already paid")

        # Get or create the payment record
        payment = ensure_payment_record_created(loan)
        
        email = _internal_email(request.user.phone)
        metadata = {
            "loan_id": loan.id,
            "phone": request.user.phone,
            "purpose": "service_fee",
        }

        # --- FIX: Check if we already have the URL from ApplyLoanView ---
        if payment.authorization_url and payment.access_code:
            logger.info(f"Returning existing Paystack URL for loan {loan.id}")
            return Response({
                "paystack_public_key": settings.PAYSTACK_PUBLIC_KEY,
                "email": email,
                "amount_kes": loan.service_fee,
                "reference": loan.paystack_reference,
                "authorization_url": payment.authorization_url,
                "access_code": payment.access_code,
                "metadata": metadata,
            })
        # ----------------------------------------------------------------

        try:
            client = PaystackClient()
            
            # Only initialize if we don't have it yet
            init = client.initialize_transaction(
                email=email,
                amount_kobo=loan.service_fee * 100,
                reference=loan.paystack_reference,
                currency=settings.APP_FEE_CURRENCY,
                metadata=metadata,
            )

            authorization_url = init.get("authorization_url")
            access_code = init.get("access_code")

            # Update Payment Model
            payment.authorization_url = authorization_url
            payment.access_code = access_code
            payment.save()
            
            # Update Loan Model (to keep them in sync)
            loan.paystack_authorization_url = authorization_url
            loan.paystack_access_code = access_code
            loan.save(update_fields=["paystack_authorization_url", "paystack_access_code"])

            return Response({
                "paystack_public_key": settings.PAYSTACK_PUBLIC_KEY,
                "email": email,
                "amount_kes": loan.service_fee,
                "reference": loan.paystack_reference,
                "authorization_url": authorization_url,
                "access_code": access_code,
                "metadata": init.get("metadata", {}),
            })

        except PaystackError as e:
            logger.error(f"Paystack init failed for loan {loan_id}: {e}")
            return Response(
                {"error": f"Payment initialization failed: {str(e)}"},
                status=502
            )


# ---------------------------------------------------------------------
# 2️⃣ VERIFY PAYMENT (frontend: /payments/verify/)
# ---------------------------------------------------------------------

class VerifyPaymentView(APIView):
    permission_classes = [IsAuthenticated]

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

            try:
                client = PaystackClient()
                result = client.verify_transaction(reference)
            except PaystackError as e:
                logger.error(f"Paystack verify failed for {reference}: {e}")
                return Response(
                    {"ok": False, "error": f"Verification failed: {str(e)}"},
                    status=502
                )

            if result.get("status") != "success":
                logger.warning(f"Payment not successful for {reference}: {result.get('status')}")
                raise ValidationError("Payment not successful")

            if int(result.get("amount", 0)) != loan.service_fee * 100:
                logger.warning(f"Amount mismatch for {reference}")
                raise ValidationError("Invalid payment amount")

            # Mark payment as verified
            payment.verified = True
            payment.paystack_transaction_id = str(result.get("id"))
            payment.paid_at = timezone.now()
            payment.save()

            # Update loan status
            loan.service_fee_paid = True
            loan.status = Loan.Status.APPROVED
            loan.last_event = "Service fee verified"
            loan.save()

            logger.info(f"Payment verified for loan {loan.id}")

            # Initiate disbursement
            transfer = _ensure_transfer_record(loan)
            if not transfer.initiated:
                try:
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

                    logger.info(f"Loan {loan.id} disbursed successfully")

                except PaystackError as e:
                    logger.error(f"Disbursement failed for loan {loan.id}: {e}")
                    loan.last_event = f"Disbursement pending: {str(e)}"
                    loan.save()
                    # Don't fail the response - payment was successful

        return Response({"ok": True, "status": "verified"})
