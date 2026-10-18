from __future__ import annotations

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from users.models import ConsentRecord, User
from users.phone import InvalidPhoneNumber, normalize_msisdn

# Used to keep login timing similar when an account does not exist.
_DUMMY_HASH = make_password("not-a-real-password-for-timing")

GENERIC_LOGIN_ERROR = "Invalid phone number or password."


class RegisterSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    national_id = serializers.CharField(max_length=10)
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    consent = serializers.BooleanField()
    consent_version = serializers.CharField(required=False, allow_blank=True)

    def validate_phone(self, value: str) -> str:
        try:
            normalized = normalize_msisdn(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if User.objects.filter(phone=normalized).exists():
            raise serializers.ValidationError(
                "This phone number is already registered. Please log in instead."
            )
        return normalized

    def validate_national_id(self, value: str) -> str:
        value = (value or "").strip()
        if not value.isdigit() or not 6 <= len(value) <= 10:
            raise serializers.ValidationError("National ID must be 6-10 digits.")
        if User.objects.filter(national_id=value).exists():
            raise serializers.ValidationError(
                "This national ID is already registered. Contact support if this is an error."
            )
        return value

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate_consent(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "You must accept the privacy notice and terms to create an account."
            )
        return value

    def validate_consent_version(self, value: str) -> str:
        if value and value != settings.CONSENT_VERSION:
            raise serializers.ValidationError(
                "The privacy notice has changed. Reload the page and review it again."
            )
        return value


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True, max_length=128)

    def validate_phone(self, value: str) -> str:
        try:
            return normalize_msisdn(value)
        except InvalidPhoneNumber as exc:
            # Do not reveal whether the number format or the account was wrong.
            raise serializers.ValidationError(GENERIC_LOGIN_ERROR) from exc

    def validate(self, attrs):
        user = User.objects.filter(phone=attrs["phone"]).first()
        if user is None:
            check_password(attrs["password"], _DUMMY_HASH)
            raise serializers.ValidationError(GENERIC_LOGIN_ERROR)
        if not user.check_password(attrs["password"]):
            raise serializers.ValidationError(GENERIC_LOGIN_ERROR)
        if not user.is_active:
            raise serializers.ValidationError(
                "This account is disabled. Please contact support."
            )
        attrs["user"] = user
        return attrs


class MeSerializer(serializers.ModelSerializer):
    is_verified = serializers.BooleanField(read_only=True)
    has_uploaded_documents = serializers.BooleanField(read_only=True)
    can_borrow = serializers.BooleanField(read_only=True)
    national_id_masked = serializers.CharField(read_only=True)
    consent_current = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "national_id_masked",
            "created_at",
            "verification_status",
            "verification_notes",
            "is_verified",
            "has_uploaded_documents",
            "consent_current",
            "can_borrow",
        ]
        read_only_fields = fields

    def get_consent_current(self, obj: User) -> bool:
        return obj.has_current_consent


class ConsentGrantSerializer(serializers.Serializer):
    consent_version = serializers.CharField(required=False, allow_blank=True)
    purposes = serializers.ListField(
        child=serializers.ChoiceField(choices=ConsentRecord.Purpose.choices),
        required=False,
    )

    def validate_consent_version(self, value: str) -> str:
        if value and value != settings.CONSENT_VERSION:
            raise serializers.ValidationError(
                "The privacy notice has changed. Reload the page and review it again."
            )
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)


class PasswordResetConfirmSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(min_length=8, max_length=128, write_only=True)

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value
