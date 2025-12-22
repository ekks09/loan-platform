import re
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator

def normalize_ke_phone(phone: str) -> str:
    """Normalizes a Kenyan phone number to 254... format."""
    if not phone:
        return ""
    p = re.sub(r"\s+", "", str(phone)).lstrip("+")
    if p.startswith("0"):
        # Handles 07... and 01...
        p = "254" + p[1:]
    elif len(p) == 9 and (p.startswith("7") or p.startswith("1")):
        # Handles 7... and 1...
        p = "254" + p
    return p

KENYAN_PHONE_REGEX = r"^254(7|1)\d{8}$"
phone_validator = RegexValidator(
    regex=KENYAN_PHONE_REGEX,
    message="Phone number must be in format 2547XXXXXXXX or 2541XXXXXXXX.",
)

national_id_validator = RegexValidator(
    regex=r"^\d{6,10}$",
    message="National ID must be 6-10 digits.",
)

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
        return self.create_user(phone=phone, national_id=national_id, password=password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=12, unique=True, validators=[phone_validator])
    national_id = models.CharField(max_length=10, validators=[national_id_validator])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["national_id"]

    def __str__(self) -> str:
        return self.phone