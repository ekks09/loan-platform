import re
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator


# Utility to normalize Kenyan phone numbers to 254XXXXXXXXX format
def normalize_ke_phone(phone: str) -> str:
    if not phone:
        return ""
    p = re.sub(r"\s+", "", str(phone)).lstrip("+")
    if p.startswith("0"):
        p = "254" + p[1:]
    elif len(p) == 9 and (p.startswith("7") or p.startswith("1")):
        p = "254" + p
    return p


# Validators
KENYAN_PHONE_REGEX = r"^254(7|1)\d{8}$"
phone_validator = RegexValidator(
    regex=KENYAN_PHONE_REGEX,
    message="Phone number must be in format 2547XXXXXXXX or 2541XXXXXXXX.",
)

national_id_validator = RegexValidator(
    regex=r"^\d{6,10}$",
    message="National ID must be 6-10 digits.",
)


# Custom User Manager
class UserManager(BaseUserManager):
    def create_user(self, phone: str, national_id: str, password: str | None = None, **extra_fields):
        if not phone:
            raise ValueError("Phone is required")
        if not national_id:
            raise ValueError("National ID is required")

        phone = phone.strip()
        national_id = national_id.strip()

        phone_validator(phone)
        national_id_validator(national_id)

        user = self.model(phone=phone, national_id=national_id, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, national_id: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone=phone, national_id=national_id, password=password, **extra_fields)


# Custom User Model
class User(AbstractBaseUser, PermissionsMixin):
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    phone = models.CharField(max_length=12, unique=True, validators=[phone_validator])
    national_id = models.CharField(max_length=10, validators=[national_id_validator])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Photo verification fields
    id_front_url = models.URLField(max_length=500, blank=True, null=True, help_text="Front of National ID")
    id_front_path = models.CharField(max_length=500, blank=True, help_text="Storage path for ID front")
    
    id_back_url = models.URLField(max_length=500, blank=True, null=True, help_text="Back of National ID")
    id_back_path = models.CharField(max_length=500, blank=True, help_text="Storage path for ID back")
    
    selfie_url = models.URLField(max_length=500, blank=True, null=True, help_text="Selfie photo")
    selfie_path = models.CharField(max_length=500, blank=True, help_text="Storage path for selfie")
    
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        help_text="ID verification status"
    )
    verification_notes = models.TextField(blank=True, help_text="Admin notes on verification")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verifications_done'
    )

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["national_id"]

    class Meta:
        indexes = [
            models.Index(fields=["verification_status"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self) -> str:
        return self.phone

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED

    @property
    def has_uploaded_documents(self) -> bool:
        return all([self.id_front_url, self.id_back_url, self.selfie_url])
