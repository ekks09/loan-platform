"""Shared factories for the test suite.

These helpers deliberately go through the public model/service APIs rather
than raw inserts so the tests exercise the same invariants as production.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from loans.models import Loan
from loans.services import accept_offer, approve, create_application
from users.authentication import _jwt_encode
from users.models import ConsentRecord, User


def make_user(
    *,
    phone: str = "0712345678",
    national_id: str = "12345678",
    password: str = "StrongPass!234",
    **extra,
) -> User:
    return User.objects.create_user(
        phone=phone, national_id=national_id, password=password, **extra
    )


def make_staff(*, phone: str = "0700000001", national_id: str = "90000001") -> User:
    return User.objects.create_user(
        phone=phone,
        national_id=national_id,
        password="StaffPass!234",
        is_staff=True,
    )


def grant_current_consent(user: User) -> None:
    for purpose, version in (
        (ConsentRecord.Purpose.IDENTITY_PROCESSING, settings.CONSENT_VERSION),
        (ConsentRecord.Purpose.TERMS, settings.TERMS_VERSION),
    ):
        ConsentRecord.objects.get_or_create(
            user=user, purpose=purpose, version=version
        )


def attach_documents(user: User, prefix: str = "users/1") -> User:
    user.id_front_path = f"{prefix}/id_front/front.jpg"
    user.id_back_path = f"{prefix}/id_back/back.jpg"
    user.selfie_path = f"{prefix}/selfie/selfie.jpg"
    user.documents_uploaded_at = timezone.now()
    user.verification_status = User.VerificationStatus.PENDING
    user.save(
        update_fields=[
            "id_front_path",
            "id_back_path",
            "selfie_path",
            "documents_uploaded_at",
            "verification_status",
        ]
    )
    return user


def make_verified_user(
    *,
    phone: str = "0712345678",
    national_id: str = "12345678",
    consent: bool = True,
    documents: bool = True,
) -> User:
    user = make_user(phone=phone, national_id=national_id)
    if documents:
        attach_documents(user, prefix=f"users/{user.pk}")
    if consent:
        grant_current_consent(user)
    user.verification_status = User.VerificationStatus.VERIFIED
    user.verified_at = timezone.now()
    user.save(update_fields=["verification_status", "verified_at"])
    return user


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {_jwt_encode(user)}")
    return client


def make_loan(
    user: User,
    *,
    amount: int = 10_000,
    mpesa_phone: str | None = None,
    approve_offer: bool = False,
    accept: bool = False,
    expiry_hours: int = 48,
) -> Loan:
    loan = create_application(
        user=user,
        amount=amount,
        mpesa_phone=mpesa_phone or user.phone,
    )
    if approve_offer or accept:
        approve(loan, actor=user, expiry_hours=expiry_hours)
        loan.refresh_from_db()
    if accept:
        accept_offer(loan, actor=user, version=loan.offer_terms_version)
        loan.refresh_from_db()
    return loan


def expire_loan_offer(loan: Loan) -> Loan:
    Loan.objects.filter(pk=loan.pk).update(
        offer_expires_at=timezone.now() - timedelta(minutes=1)
    )
    loan.refresh_from_db()
    return loan


class DictMock:
    """Small helper for building provider response dictionaries."""

    @staticmethod
    def charge(reference: str, amount_kes: int, **overrides) -> dict:
        data = {
            "id": 12345,
            "status": "success",
            "reference": reference,
            "currency": "KES",
            "amount": amount_kes * 100,
            "channel": "mobile_money",
            "gateway_response": "Successful",
        }
        data.update(overrides)
        return data


class patched_provider:
    """Context manager patching ``payments.services.PaystackClient``."""

    def __init__(self, **methods):
        self.methods = methods
        self._patcher = None
        self.client = None

    def __enter__(self):
        self._patcher = patch("payments.services.PaystackClient")
        mocked_class = self._patcher.start()
        self.client = mocked_class.return_value
        for name, value in self.methods.items():
            method = getattr(self.client, name)
            if isinstance(value, Exception):
                method.side_effect = value
            elif callable(value):
                method.side_effect = value
            else:
                method.return_value = value
        return self.client

    def __exit__(self, exc_type, exc, tb):
        self._patcher.stop()
        return False
