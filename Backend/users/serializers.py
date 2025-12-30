from rest_framework import serializers
from .models import User, phone_validator, national_id_validator


def normalize_phone(phone: str) -> str:
    """Normalize phone number to 2547XXXXXXXX or 2541XXXXXXXX format."""
    raw = (phone or "").strip().replace(" ", "").replace("-", "")
    
    if not raw:
        raise serializers.ValidationError("Phone number is required.")
    
    if raw.startswith("+"):
        raw = raw[1:]
    
    # Already in correct format
    if raw.startswith("254") and len(raw) == 12:
        if not (raw[3] == "7" or raw[3] == "1"):
            raise serializers.ValidationError("Invalid Kenyan phone number. Must start with 07 or 01.")
        return raw
    
    # Format: 07XXXXXXXX or 01XXXXXXXX
    if raw.startswith("0") and len(raw) == 10:
        if not (raw[1] == "7" or raw[1] == "1"):
            raise serializers.ValidationError("Invalid Kenyan phone number. Must start with 07 or 01.")
        return "254" + raw[1:]
    
    # Format: 7XXXXXXXX or 1XXXXXXXX (9 digits)
    if len(raw) == 9 and (raw[0] == "7" or raw[0] == "1"):
        return "254" + raw
    
    raise serializers.ValidationError("Invalid phone number format. Use 07XXXXXXXX or 2547XXXXXXXX.")


class RegisterSerializer(serializers.Serializer):
    phone = serializers.CharField()
    national_id = serializers.CharField()
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_phone(self, value: str) -> str:
        normalized = normalize_phone(value)
        # Check if already registered
        if User.objects.filter(phone=normalized).exists():
            raise serializers.ValidationError("This phone number is already registered. Please login instead.")
        return normalized

    def validate_national_id(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("National ID is required.")
        if not value.isdigit():
            raise serializers.ValidationError("National ID must contain only digits.")
        if len(value) < 6 or len(value) > 10:
            raise serializers.ValidationError("National ID must be 6-10 digits.")
        return value

    def validate_password(self, value: str) -> str:
        if not value or len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        return value

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

        if not phone:
            raise serializers.ValidationError({"phone": "Phone number is required."})
        
        if not password:
            raise serializers.ValidationError({"password": "Password is required."})

        # Find user by phone
        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            raise serializers.ValidationError({
                "non_field_errors": ["No account found with this phone number. Please register first."]
            })

        # Check password
        if not user.check_password(password):
            raise serializers.ValidationError({
                "non_field_errors": ["Incorrect password. Please try again."]
            })

        # Check if active
        if not user.is_active:
            raise serializers.ValidationError({
                "non_field_errors": ["Your account has been deactivated. Please contact support."]
            })

        attrs["user"] = user
        return attrs


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["phone", "national_id", "created_at"]
        read_only_fields = fields
