from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.errors import (
    ConsentRequired,
    InvalidFileUpload,
    StorageUnavailable,
)
from core.notifications import send_sms
from users.authentication import _jwt_encode
from users.models import ConsentRecord, DocumentAccessLog, PasswordResetCode, User
from users.otp import issue_password_reset, verify_and_consume
from users.phone import InvalidPhoneNumber, normalize_msisdn
from users.serializers import (
    GENERIC_LOGIN_ERROR,
    ConsentGrantSerializer,
    LoginSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
)
from core.errors import StorageUnavailable
from core.storage import document_storage_configured, get_document_store

logger = logging.getLogger(__name__)

DOCUMENT_FIELDS = ("id_front", "id_back", "selfie")
DOCUMENT_PATH_FIELDS = {
    "id_front": "id_front_path",
    "id_back": "id_back_path",
    "selfie": "selfie_path",
}


def _client_meta(request) -> dict:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return {
        "ip_address": ip or None,
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
    }


def _consent_versions() -> tuple[tuple[str, str], ...]:
    """Version each purpose is actually granted at.

    The privacy/consent version and the terms version are separate settings;
    recording both at ``CONSENT_VERSION`` would make ``has_current_consent``
    fail as soon as the two versions diverge.
    """
    return (
        (ConsentRecord.Purpose.IDENTITY_PROCESSING, settings.CONSENT_VERSION),
        (ConsentRecord.Purpose.TERMS, settings.TERMS_VERSION),
    )


def _grant_consents(user: User, request) -> None:
    meta = _client_meta(request)
    for purpose, version in _consent_versions():
        ConsentRecord.objects.get_or_create(
            user=user,
            purpose=purpose,
            version=version,
            defaults=meta,
        )


class RegisterView(APIView):
    """Registration with optional identity documents.

    JSON registration creates the account only; documents can be uploaded later
    through ``POST /api/users/documents/``. Multipart registration requires all
    three documents and fails closed when storage is unavailable.
    """

    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        files = {name: request.FILES.get(name) for name in DOCUMENT_FIELDS}
        has_files = any(files.values())

        if has_files:
            missing = [name for name, file in files.items() if not file]
            if missing:
                raise InvalidFileUpload(
                    "Please upload all three documents: ID front, ID back and selfie.",
                    details={"missing": missing},
                )
            store = get_document_store()
            if not store.configured():
                raise StorageUnavailable(
                    "Document uploads are not available right now. "
                    "You can create your account and upload documents later."
                )
            for file in files.values():
                store.validate_file(file)

        uploaded_paths: list[str] = []
        try:
            # Upload first so a storage failure never creates a half account.
            paths: dict[str, str] = {}
            if has_files:
                import uuid as _uuid

                staging_id = _uuid.uuid4().hex
                store = get_document_store()
                for name, file in files.items():
                    try:
                        stored = store.upload(file, folder=name, user_identifier=staging_id)
                    except StorageUnavailable as exc:
                        # Ambiguous remote outcome: attempt a compensating
                        # delete of the exact server-generated key.
                        ambiguous = (exc.details or {}).get("path")
                        if ambiguous:
                            _cleanup([ambiguous])
                        raise
                    uploaded_paths.append(stored.path)
                    paths[DOCUMENT_PATH_FIELDS[name]] = stored.path

            with transaction.atomic():
                user = User.objects.create_user(
                    phone=data["phone"],
                    national_id=data["national_id"],
                    password=data["password"],
                )
                if paths:
                    for field, path in paths.items():
                        setattr(user, field, path)
                    user.documents_uploaded_at = timezone.now()
                    user.verification_status = User.VerificationStatus.PENDING
                    user.save(
                        update_fields=[*paths.keys(), "documents_uploaded_at", "verification_status"]
                    )
                _grant_consents(user, request)

        except IntegrityError:
            _cleanup(uploaded_paths)
            return Response(
                {
                    "error": {
                        "code": "account_exists",
                        "message": "This phone number or national ID is already registered.",
                        "details": {},
                        "request_id": getattr(request, "request_id", ""),
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            _cleanup(uploaded_paths)
            raise

        logger.info("User registered id=%s documents=%s", user.pk, bool(uploaded_paths))
        return Response(
            {
                "ok": True,
                "message": (
                    "Account created. Your documents are pending verification."
                    if uploaded_paths
                    else "Account created. Upload your identity documents to get verified."
                ),
                "verification_status": user.verification_status,
                "has_documents": user.has_uploaded_documents,
            },
            status=status.HTTP_201_CREATED,
        )


def _cleanup(paths: list[str]) -> None:
    if not paths:
        return
    store = get_document_store()
    for path in paths:
        if path:
            store.delete(path)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token = _jwt_encode(user)
        logger.info("User login id=%s", user.pk)
        return Response(
            {
                "access": token,
                "token_type": "Bearer",  # nosec B105 -- RFC 6750 token type, not a credential
                "expires_in": settings.JWT_ACCESS_TTL_MINUTES * 60,
                "verification_status": user.verification_status,
                "is_verified": user.is_verified,
                "has_uploaded_documents": user.has_uploaded_documents,
                "can_borrow": user.can_borrow,
            }
        )


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class VerificationStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "verification_status": user.verification_status,
                "is_verified": user.is_verified,
                "has_uploaded_documents": user.has_uploaded_documents,
                "can_borrow": user.can_borrow,
                "consent_current": user.has_current_consent,
                "verification_notes": (
                    user.verification_notes
                    if user.verification_status == User.VerificationStatus.REJECTED
                    else ""
                ),
            }
        )


class DocumentsUploadView(APIView):
    """Upload or replace identity documents for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "documents"

    def post(self, request):
        store = get_document_store()
        if not store.configured():
            raise StorageUnavailable(
                "Document uploads are not configured on this deployment."
            )
        files = {name: request.FILES.get(name) for name in DOCUMENT_FIELDS}
        missing = [name for name, file in files.items() if not file]
        if missing:
            raise InvalidFileUpload(
                "Please upload all three documents: ID front, ID back and selfie.",
                details={"missing": missing},
            )
        for file in files.values():
            store.validate_file(file)

        user = request.user
        new_paths: list[str] = []
        old_paths = [user.id_front_path, user.id_back_path, user.selfie_path]
        try:
            uploaded = {}
            for name, file in files.items():
                try:
                    stored = store.upload(file, folder=name, user_identifier=user.pk)
                except StorageUnavailable as exc:
                    ambiguous = (exc.details or {}).get("path")
                    if ambiguous:
                        _cleanup([ambiguous])
                    raise
                new_paths.append(stored.path)
                uploaded[DOCUMENT_PATH_FIELDS[name]] = stored.path

            with transaction.atomic():
                for field, path in uploaded.items():
                    setattr(user, field, path)
                user.documents_uploaded_at = timezone.now()
                user.verification_status = User.VerificationStatus.PENDING
                user.verification_notes = ""
                user.verified_at = None
                user.verified_by = None
                user.save(
                    update_fields=[
                        *uploaded.keys(),
                        "documents_uploaded_at",
                        "verification_status",
                        "verification_notes",
                        "verified_at",
                        "verified_by",
                    ]
                )
        except Exception:
            _cleanup(new_paths)
            raise

        transaction.on_commit(lambda: _cleanup([p for p in old_paths if p]))
        logger.info("Documents uploaded for user id=%s", user.pk)
        return Response(
            {
                "ok": True,
                "verification_status": user.verification_status,
                "message": "Documents uploaded. We will review them shortly.",
            }
        )


class ConsentView(APIView):
    """Record consent for an existing account (e.g. after terms change)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ConsentGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        purposes = serializer.validated_data.get("purposes") or [
            ConsentRecord.Purpose.IDENTITY_PROCESSING,
            ConsentRecord.Purpose.TERMS,
        ]
        meta = _client_meta(request)
        versions = dict(_consent_versions())
        for purpose in purposes:
            ConsentRecord.objects.get_or_create(
                user=request.user,
                purpose=purpose,
                version=versions.get(purpose, settings.CONSENT_VERSION),
                defaults=meta,
            )
        return Response({"ok": True, "consent_current": request.user.has_current_consent})


class PasswordResetRequestView(APIView):
    """Send a 6-digit SMS code when the number is registered.

    The response never changes based on account existence, and both the number
    and the network address are rate limited in the domain layer.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            phone = normalize_msisdn(serializer.validated_data["phone"])
        except InvalidPhoneNumber:
            phone = None

        user = (
            User.objects.filter(phone=phone, is_active=True).first() if phone else None
        )
        result = issue_password_reset(user, phone or "", request)

        if result.retry_after:
            retry_minutes = max(1, (result.retry_after + 59) // 60)
            response = Response(
                {
                    "ok": False,
                    "error": {
                        "code": "too_many_requests",
                        "message": (
                            "Too many codes were requested for this number. "
                            f"Try again in {retry_minutes} minute(s)."
                        ),
                        "details": {"retry_after_seconds": result.retry_after},
                        "request_id": getattr(request, "request_id", ""),
                    },
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(result.retry_after)
            return response

        return Response(
            {
                "ok": True,
                "message": "If the number is registered, a reset code has been sent by SMS.",
                "expires_in_seconds": settings.OTP_TTL_MINUTES * 60,
                "resend_after_seconds": settings.OTP_RESEND_COOLDOWN_SECONDS,
            }
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            phone = normalize_msisdn(data["phone"])
        except InvalidPhoneNumber:
            phone = None

        user = User.objects.filter(phone=phone, is_active=True).first() if phone else None
        if not user or not verify_and_consume(user, data["code"]):
            return Response(
                {
                    "error": {
                        "code": "invalid_reset_code",
                        "message": "The reset code is invalid or has expired.",
                        "details": {},
                        "request_id": getattr(request, "request_id", ""),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(data["new_password"])
        user.save(update_fields=["password", "password_changed_at"])
        logger.info("Password reset completed for user id=%s", user.pk)
        return Response(
            {
                "ok": True,
                "message": "Password updated. Every previous session has been signed out.",
            }
        )


__all__ = [
    "ConsentRequired",
    "DocumentAccessLog",
    "RegisterView",
    "LoginView",
    "MeView",
    "VerificationStatusView",
    "DocumentsUploadView",
    "ConsentView",
    "PasswordResetRequestView",
    "PasswordResetConfirmView",
    "GENERIC_LOGIN_ERROR",
]


class NotificationListView(APIView):
    """Owner-scoped in-app notifications (the SMS fallback channel)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from core.models import InAppNotification

        rows = InAppNotification.objects.filter(user=request.user)[:50]
        unread = InAppNotification.objects.filter(
            user=request.user, status=InAppNotification.Status.UNREAD
        ).count()
        return Response(
            {
                "results": [
                    {
                        "id": row.pk,
                        "purpose": row.purpose,
                        "body": row.body,
                        "status": row.status,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ],
                "unread": unread,
            }
        )


class NotificationReadAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from django.utils import timezone as _tz

        from core.models import InAppNotification

        updated = InAppNotification.objects.filter(
            user=request.user, status=InAppNotification.Status.UNREAD
        ).update(status=InAppNotification.Status.READ, read_at=_tz.now())
        return Response({"ok": True, "marked_read": updated})
