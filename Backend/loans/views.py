from __future__ import annotations

import logging

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.errors import (
    ConsentRequired,
    DisbursementProviderError,
    DomainError,
    IdentityNotVerified,
    InvalidTransition,
    LoanNotFound,
)
from loans.models import Loan
from loans.serializers import (
    LoanApplySerializer,
    LoanSerializer,
    OfferAcceptSerializer,
    OfferExpirySerializer,
    StaffDecisionSerializer,
)
from loans.services import (
    accept_offer as accept_offer_service,
    approve as approve_service,
    cancel as cancel_service,
    create_application,
    reject as reject_service,
)

logger = logging.getLogger(__name__)


def _get_loan_for_user(request, loan_id: int) -> Loan:
    qs = Loan.objects.select_related("user")
    if request.user.is_staff:
        loan = qs.filter(pk=loan_id).first()
    else:
        loan = qs.filter(pk=loan_id, user=request.user).first()
    if loan is None:
        raise LoanNotFound()
    return loan


def _loan_payload(loan: Loan) -> dict:
    return LoanSerializer(loan).data


class QuoteView(APIView):
    """Public fee quote. Never requires an account to see the price."""

    permission_classes = [permissions.AllowAny]
    throttle_scope = "quote"

    def get(self, request):
        raw = request.query_params.get("amount", "")
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            return Response(
                {
                    "error": {
                        "code": "invalid_amount",
                        "message": "Provide a whole-shilling amount, e.g. ?amount=10000.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        fee = Loan.compute_service_fee(amount)
        return Response(
            {
                "amount": amount,
                "currency": "KES",
                "service_fee": fee,
                "fee_percent": Loan.fee_percent(amount),
                "fee_rate_percent": Loan.fee_rate_percent(amount),
                "min_amount": Loan.MIN_AMOUNT,
                "max_amount": Loan.MAX_AMOUNT,
                "fee_collection_mode": settings.FEE_COLLECTION_MODE,
                "upfront_fee_required": settings.FEE_COLLECTION_MODE == "upfront",
                "disclosure": (
                    "This is the processing fee for this amount. In this environment "
                    "no upfront fee is collected."
                    if settings.FEE_COLLECTION_MODE == "disabled"
                    else "The processing fee is collected before disbursement."
                ),
            }
        )


class ApplyLoanView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "user"

    def post(self, request):
        serializer = LoanApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.has_current_consent:
            raise ConsentRequired()
        if not user.has_uploaded_documents:
            raise IdentityNotVerified(
                "Upload your identity documents and wait for verification before applying."
            )
        if not user.is_verified:
            raise IdentityNotVerified()

        loan = create_application(
            user=user,
            amount=serializer.validated_data["amount"],
            mpesa_phone=serializer.validated_data["mpesa_phone"],
        )
        logger.info("Loan application created id=%s user=%s", loan.pk, user.pk)
        return Response(
            {"ok": True, "loan": _loan_payload(loan)},
            status=status.HTTP_201_CREATED,
        )


class ActiveLoanView(APIView):
    """Canonical dashboard contract.

    ``loan``          the single open application, or null
    ``latest``        the most recent application of any status, or null
    ``has_active_loan`` boolean convenience for the client
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        open_loan = Loan.open_for_user(request.user)
        latest = Loan.objects.filter(user=request.user).first()
        return Response(
            {
                "has_active_loan": open_loan is not None,
                "loan": _loan_payload(open_loan) if open_loan else None,
                "latest": _loan_payload(latest) if latest else None,
            }
        )


class CurrentLoanView(ActiveLoanView):
    """Backward-compatible alias for the dashboard endpoint."""


class LoanHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        queryset = Loan.objects.filter(user=request.user).order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        paginator = Paginator(queryset, 10)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "results": [_loan_payload(loan) for loan in page.object_list],
                "page": page.number,
                "pages": paginator.num_pages,
                "count": paginator.count,
            }
        )


class LoanDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)
        return Response(
            {
                "loan": _loan_payload(loan),
                "events": [
                    {
                        "from": event.from_status,
                        "to": event.to_status,
                        "reason": event.reason,
                        "created_at": event.created_at,
                    }
                    for event in loan.events.all()[:50]
                ],
            }
        )


class AcceptOfferView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)
        if loan.user_id != request.user.id:
            raise LoanNotFound()
        serializer = OfferAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        loan = accept_offer_service(
            loan,
            actor=request.user,
            version=serializer.validated_data["terms_version"],
        )
        return Response({"ok": True, "loan": _loan_payload(loan)})


class CancelLoanView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, loan_id: int):
        loan = _get_loan_for_user(request, loan_id)
        if loan.user_id != request.user.id:
            raise LoanNotFound()
        loan = cancel_service(loan, actor=request.user)
        return Response({"ok": True, "loan": _loan_payload(loan)})


# ---------------------------------------------------------------------
# Staff operations
# ---------------------------------------------------------------------


class StaffLoanMixin:
    permission_classes = [permissions.IsAdminUser]


class ApproveLoanView(StaffLoanMixin, APIView):
    def post(self, request, loan_id: int):
        serializer = OfferExpirySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        loan = _get_loan_for_user(request, loan_id)
        loan = approve_service(
            loan,
            actor=request.user,
            expiry_hours=serializer.validated_data.get("expiry_hours", 48),
        )
        logger.info("Loan %s approved by staff %s", loan.pk, request.user.pk)
        return Response({"ok": True, "loan": _loan_payload(loan)})


class RejectLoanView(StaffLoanMixin, APIView):
    def post(self, request, loan_id: int):
        serializer = StaffDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "").strip()
        if not reason:
            raise InvalidTransition("A rejection reason is required.")
        loan = _get_loan_for_user(request, loan_id)
        loan = reject_service(loan, actor=request.user, reason=reason)
        logger.info("Loan %s rejected by staff %s", loan.pk, request.user.pk)
        return Response({"ok": True, "loan": _loan_payload(loan)})


class DisburseLoanView(StaffLoanMixin, APIView):
    """Start a disbursement. Provider confirmation decides ``DISBURSED``."""

    def post(self, request, loan_id: int):
        from payments.services import start_disbursement

        loan = _get_loan_for_user(request, loan_id)
        if not loan.user.is_verified:
            raise IdentityNotVerified("The borrower's identity is not verified.")
        if not loan.can_be_disbursed:
            raise InvalidTransition(
                "This loan cannot be disbursed yet. Check acceptance, expiry and fee status."
            )
        from payments.providers import ProviderError

        try:
            transfer = start_disbursement(loan, actor=request.user)
        except ProviderError as exc:
            raise DisbursementProviderError(
                str(exc), details={"retryable": exc.retryable, "code": exc.code}
            ) from exc
        loan.refresh_from_db()
        return Response(
            {
                "ok": True,
                "loan": _loan_payload(loan),
                "transfer": {
                    "reference": transfer.reference,
                    "status": transfer.status,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )


class RetryDisbursementView(StaffLoanMixin, APIView):
    def post(self, request, loan_id: int):
        from payments.services import retry_disbursement

        loan = _get_loan_for_user(request, loan_id)
        from payments.providers import ProviderError

        try:
            transfer = retry_disbursement(loan, actor=request.user)
        except ProviderError as exc:
            raise DisbursementProviderError(
                str(exc), details={"retryable": exc.retryable, "code": exc.code}
            ) from exc
        loan.refresh_from_db()
        return Response(
            {
                "ok": True,
                "loan": _loan_payload(loan),
                "transfer": {
                    "reference": transfer.reference,
                    "status": transfer.status,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )


class OpsLoanListView(StaffLoanMixin, APIView):
    """Small operational feed for the ops console."""

    def get(self, request):
        queryset = Loan.objects.select_related("user").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        query = (request.query_params.get("q") or "").strip()[:64]
        if query:
            queryset = queryset.filter(
                Q(user__phone__icontains=query) | Q(paystack_reference__icontains=query)
            )
        paginator = Paginator(queryset, 25)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "results": [
                    {
                        **_loan_payload(loan),
                        "user": {"id": loan.user_id, "phone": loan.user.phone},
                    }
                    for loan in page.object_list
                ],
                "page": page.number,
                "pages": paginator.num_pages,
                "count": paginator.count,
            }
        )


__all__ = [
    "DomainError",
    "QuoteView",
    "ApplyLoanView",
    "ActiveLoanView",
    "CurrentLoanView",
    "LoanHistoryView",
    "LoanDetailView",
    "AcceptOfferView",
    "CancelLoanView",
    "ApproveLoanView",
    "RejectLoanView",
    "DisburseLoanView",
    "RetryDisbursementView",
    "OpsLoanListView",
]
