from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import User, phone_validator, national_id_validator

def normalize_phone(phone: str) -> str:
    raw = (phone or "").strip().replace(" ", "")
    if raw.startswith("+"):
        raw = raw[1:]
    if raw.startswith("254"):
        phone_validator(raw)
        return raw
    if raw.startswith("0") and len(raw) == 10:
        raw2 = "254" + raw[1:]
        phone_validator(raw2)
        return raw2
    phone_validator(raw)  # will raise
    return raw

class RegisterSerializer(serializers.Serializer):
    phone = serializers.CharField()
    national_id = serializers.CharField()
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_phone(self, value: str) -> str:
        return normalize_phone(value)

    def validate_national_id(self, value: str) -> str:
        value = (value or "").strip()
        national_id_validator(value)
        return value

    def validate(self, attrs):
        phone = attrs["phone"]
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError({"phone": "Phone number already registered."})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            phone=validated_data["phone"],
            national_id=validated_data["national_id"],
            password=validated_data["password"],
        )

class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_phone(self, value: str) -> str:
        return normalize_phone(value)

    def validate(self, attrs):
        phone = attrs.get("phone")
        password = attrs.get("password")
        user = authenticate(phone=phone, password=password)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        if not user.is_active:
            raise serializers.ValidationError("Account disabled.")
        attrs["user"] = user
        return attrs

class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["phone", "national_id", "created_at"]
        read_only_fields = fields