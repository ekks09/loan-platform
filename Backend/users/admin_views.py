"""Staff identity-verification queue.

Documents are never linked publicly. Each rendered page creates short-lived
signed URLs and writes an access-audit row for every document viewed.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.notifications import send_sms
from users.models import DocumentAccessLog, User
from users.storage import SupabaseStorage

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR")


def _signed_documents(request, user: User) -> dict:
    """Build signed URLs for the three documents and audit each generated link."""
    urls = {}
    for name, path_field in (
        ("id_front", "id_front_path"),
        ("id_back", "id_back_path"),
        ("selfie", "selfie_path"),
    ):
        path = getattr(user, path_field, "")
        url = SupabaseStorage.create_signed_url(path) if path else None
        if url:
            DocumentAccessLog.objects.create(
                staff_user=request.user,
                subject_user=user,
                document=name,
                purpose="verification_review",
                ip_address=_client_ip(request),
            )
        urls[name] = url
    return urls


def _base_queryset(status_filter: str, query: str):
    qs = User.objects.all()
    if status_filter in {
        User.VerificationStatus.PENDING,
        User.VerificationStatus.VERIFIED,
        User.VerificationStatus.REJECTED,
    }:
        # Accounts without documents are not reviewable; the dashboard shows
        # them only as the "awaiting documents" counter.
        qs = qs.filter(verification_status=status_filter).exclude(id_front_path="")
    if query:
        qs = qs.filter(Q(phone__icontains=query) | Q(national_id__icontains=query))
    return qs.order_by("-created_at")


def _counts() -> dict:
    with_docs = User.objects.exclude(id_front_path="")
    return {
        "pending": with_docs.filter(
            verification_status=User.VerificationStatus.PENDING
        ).count(),
        "verified": with_docs.filter(
            verification_status=User.VerificationStatus.VERIFIED
        ).count(),
        "rejected": with_docs.filter(
            verification_status=User.VerificationStatus.REJECTED
        ).count(),
        "awaiting_documents": User.objects.filter(id_front_path="").count(),
    }


@staff_member_required
def verification_dashboard(request):
    status_filter = request.GET.get("status", "pending")
    query = (request.GET.get("q") or "").strip()[:64]
    queryset = _base_queryset(status_filter, query)
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    rows = [
        {"user": user, "documents": _signed_documents(request, user)}
        for user in page.object_list
    ]

    return render(
        request,
        "admin/verification_dashboard.html",
        {
            "rows": rows,
            "current_filter": status_filter,
            "query": query,
            "counts": _counts(),
            "page": page,
            "page_size": PAGE_SIZE,
            "signed_url_ttl": settings.DOCUMENT_SIGNED_URL_TTL,
        },
    )


@staff_member_required
@csrf_protect
@require_http_methods(["POST"])
def verify_user(request, user_id):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    action = data.get("action")
    notes = (data.get("notes") or "").strip()[:2000]
    if action not in {"approve", "reject"}:
        return JsonResponse(
            {"error": "Action must be 'approve' or 'reject'."}, status=400
        )

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return JsonResponse({"error": "User not found."}, status=404)
    if not user.has_uploaded_documents:
        return JsonResponse(
            {"error": "This user has not uploaded all required documents."}, status=400
        )

    if action == "approve":
        user.verification_status = User.VerificationStatus.VERIFIED
        user.verified_at = timezone.now()
        user.verified_by = request.user
        user.verification_notes = notes or "Verification approved."
        message = (
            f"Your {settings.PLATFORM_NAME} identity has been verified. "
            "You can now apply for a loan."
        )
    else:
        user.verification_status = User.VerificationStatus.REJECTED
        user.verified_at = None
        user.verified_by = request.user
        user.verification_notes = notes or (
            "We could not verify your documents. Please upload clear photos."
        )
        message = (
            f"Your {settings.PLATFORM_NAME} verification was not successful. "
            "Open the app to see the reason and upload new photos."
        )

    user.save(
        update_fields=[
            "verification_status",
            "verified_at",
            "verified_by",
            "verification_notes",
        ]
    )
    send_sms(user.phone, message, purpose="verification")
    logger.info(
        "Verification %s for user id=%s by staff id=%s", action, user.pk, request.user.pk
    )

    return JsonResponse(
        {
            "success": True,
            "new_status": user.verification_status,
            "message": f"User verification {action}d.",
        }
    )


@staff_member_required
def verification_api(request):
    status_filter = request.GET.get("status", "pending")
    queryset = _base_queryset(status_filter, (request.GET.get("q") or "").strip()[:64])
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    users = []
    for user in page.object_list:
        documents = _signed_documents(request, user)
        users.append(
            {
                "id": user.id,
                "phone": user.phone,
                # The reviewer compares the document image; the API does not
                # need to repeat the full identifier outside the admin console.
                "national_id_masked": user.national_id_masked,
                "documents": documents,
                "verification_status": user.verification_status,
                "created_at": user.created_at.isoformat(),
            }
        )

    return JsonResponse(
        {
            "users": users,
            "page": page.number,
            "pages": paginator.num_pages,
            "count": paginator.count,
        }
    )
