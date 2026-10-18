from __future__ import annotations

import logging

from django.conf import settings
from django.core.paginator import Paginator
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.errors import (
    DomainError,
    InvalidTransition,
    PaymentVerificationFailed,
    PaymentsDisabled,
)
from loans.models import Loan
from payments.models import Payment, ReconciliationRun, Transfer
from payments.paystack import ProviderError
from payments.serializers import (
    PaymentSerializer,
    RefundRequestSerializer,
    RefundSerializer,
    TransferSerializer,
)
from payments.services import (
    initialize_fee_payment,
    internal_email,
    process_charge_success,
    request_refund,
    run_reconciliation,
)

logger = logging.getLogger(__name__)


class InitPaymentView(APIView):
    """Create (or reuse) the Paystack checkout for an approved loan's fee."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_init"

    def post(self, request):
        loan_id = request.data.get("loan_id")
        loan = Loan.objects.filter(pk=loan_id, user=request.user).first()
        if loan is None:
            raise InvalidTransition("Loan not found.")
        if not settings.PAYMENTS_ENABLED:
            raise PaymentsDisabled()

        try:
            payment = initialize_fee_payment(loan, actor=request.user)
        except ProviderError as exc:
            return Response(
                {
                    "error": {
                        "code": "payment_provider_error",
                        "message": str(exc),
                        "details": {"retryable": exc.retryable},
                        "request_id": getattr(request, "request_id", ""),
                    }
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "paystack_public_key": settings.PAYSTACK_PUBLIC_KEY,
                "email": internal_email(loan.user.phone),
                "mode": "live" if settings.PAYMENTS_LIVE_MODE else "test",
                "amount_kes": payment.amount_kes,
                "reference": payment.reference,
                "authorization_url": payment.authorization_url,
                "access_code": payment.access_code,
                "fee_collection_mode": settings.FEE_COLLECTION_MODE,
            }
        )


class VerifyPaymentView(APIView):
    """User-initiated verification.

    This is a convenience only: the webhook remains the source of truth and
    both paths call the same idempotent service.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_init"

    def post(self, request):
        reference = str(request.data.get("reference") or "").strip()
        if not reference or len(reference) > 64:
            raise InvalidTransition("A valid payment reference is required.")

        payment = Payment.objects.filter(reference=reference, user=request.user).first()
        if payment is None:
            raise InvalidTransition("Payment not found.")

        if payment.status == Payment.Status.SUCCEEDED:
            return Response(
                {"ok": True, "status": "already_verified", "payment": PaymentSerializer(payment).data}
            )

        try:
            payment = process_charge_success(reference, {}, verify=True)
        except ProviderError as exc:
            raise PaymentVerificationFailed(str(exc)) from exc
        except DomainError:
            raise
        except Exception as exc:
            logger.warning("Verification failed for payment %s", payment.pk, exc_info=True)
            raise PaymentVerificationFailed(
                "We could not confirm this payment yet. It will update automatically."
            ) from exc

        return Response(
            {"ok": True, "status": "verified", "payment": PaymentSerializer(payment).data}
        )


class PaystackReferenceStatusView(APIView):
    """Read-only status for one Paystack reference.

    ``GET /api/paystack/verify/<reference>/`` is the polling endpoint the
    dashboard uses while a prompt is open. It never calls the provider and
    never changes state: verification happens in the signed webhook or in the
    explicit ``POST /api/payments/verify/`` action.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference: str):
        reference = str(reference or "").strip()[:64]
        payment = Payment.objects.filter(reference=reference, user=request.user).first()
        if payment is None:
            raise NotFound("Payment not found.")
        return Response({"ok": True, "payment": PaymentSerializer(payment).data})


class PaymentHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        queryset = Payment.objects.filter(user=request.user).order_by("-created_at")
        paginator = Paginator(queryset, 10)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "results": [PaymentSerializer(p).data for p in page.object_list],
                "page": page.number,
                "pages": paginator.num_pages,
                "count": paginator.count,
            }
        )


# ---------------------------------------------------------------------
# Staff operations
# ---------------------------------------------------------------------


class StaffPaymentMixin:
    permission_classes = [permissions.IsAdminUser]


class RefundPaymentView(StaffPaymentMixin, APIView):
    def post(self, request):
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = Payment.objects.filter(
            reference=serializer.validated_data["payment_reference"]
        ).first()
        if payment is None:
            raise InvalidTransition("Payment not found.")
        try:
            refund = request_refund(
                payment,
                amount_kes=serializer.validated_data.get("amount_kes"),
                reason=serializer.validated_data["reason"],
                actor=request.user,
            )
        except ProviderError as exc:
            return Response(
                {
                    "error": {
                        "code": "refund_provider_error",
                        "message": str(exc),
                        "request_id": getattr(request, "request_id", ""),
                    }
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        logger.info("Refund %s requested by staff %s", refund.pk, request.user.pk)
        return Response(
            {"ok": True, "refund": RefundSerializer(refund).data},
            status=status.HTTP_202_ACCEPTED,
        )


class TransferListView(StaffPaymentMixin, APIView):
    def get(self, request):
        queryset = Transfer.objects.select_related("loan", "loan__user").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        paginator = Paginator(queryset, 25)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "results": [
                    {
                        **TransferSerializer(t).data,
                        "user_phone": t.loan.user.phone,
                    }
                    for t in page.object_list
                ],
                "page": page.number,
                "pages": paginator.num_pages,
                "count": paginator.count,
            }
        )


class ReconciliationView(StaffPaymentMixin, APIView):
    """Run reconciliation. Dry-run by default; pass dry_run=false to correct."""

    def post(self, request):
        raw = str(request.data.get("dry_run", "true")).lower()
        dry_run = raw not in {"false", "0", "no"}
        run = run_reconciliation(dry_run=dry_run, actor=request.user)
        return Response(
            {
                "run": {
                    "id": run.pk,
                    "status": run.status,
                    "dry_run": run.dry_run,
                    "summary": run.summary,
                    "issues": run.issues,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                }
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ReconciliationListView(StaffPaymentMixin, APIView):
    def get(self, request):
        runs = ReconciliationRun.objects.all()[:20]
        return Response(
            {
                "results": [
                    {
                        "id": run.pk,
                        "status": run.status,
                        "dry_run": run.dry_run,
                        "summary": run.summary,
                        "started_at": run.started_at,
                        "finished_at": run.finished_at,
                    }
                    for run in runs
                ]
            }
        )
