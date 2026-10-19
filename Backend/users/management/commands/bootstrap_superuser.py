"""Create or refresh the platform superuser (idempotent).

Borrowers authenticate with a phone number, but back-office operators sign in
with a display name. This command provisions that operator account and grants
it every administrative role the platform understands:

  * ``is_staff`` / ``is_superuser`` / ``is_active`` (Django admin access and
    every model permission through ``PermissionsMixin``)
  * membership of the ``Super Admin`` group, which is created if missing and
    kept in sync with the full permission table
  * membership of every other existing group, so group-gated views cannot
    hide an action from the operator
  * a verified identity record (verification is a borrower gate, but an
    operator account must never be blocked by its own review state)

Values are read from the command line first, then from the environment::

    PLATFORM_SUPERUSER_USERNAME      default "Ken Admin"
    PLATFORM_SUPERUSER_PASSWORD      required unless --password is passed
    PLATFORM_SUPERUSER_PHONE         required unless --phone is passed
    PLATFORM_SUPERUSER_NATIONAL_ID   required unless --national-id is passed

Usage::

    python manage.py bootstrap_superuser \
        --username "Ken Admin" --password "…" \
        --phone 254700000000 --national-id 000000

The command is safe to re-run: it updates the existing account in place and
reports what changed. Password rotation is included, so the same command
doubles as the documented recovery path.
"""
from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from users.models import User

SUPER_ADMIN_GROUP = "Super Admin"


class Command(BaseCommand):
    help = "Create or update the platform superuser and grant every admin role."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="Operator sign-in name.")
        parser.add_argument("--password", default="", help="Operator password.")
        parser.add_argument(
            "--phone", default="", help="Unique MSISDN stored on the account."
        )
        parser.add_argument(
            "--national-id", default="", help="Unique national ID stored on the account."
        )
        parser.add_argument(
            "--skip-password-validation",
            action="store_true",
            help="Store the password even if it fails the platform validators.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Never prompt; fail instead when a value is missing.",
        )

    # ---------------------------------------------------------------- helpers
    def _value(self, options: dict, key: str, env_key: str, secret: bool = False) -> str:
        import os

        value = (options.get(key) or os.environ.get(env_key) or "").strip()
        if value or options.get("no_input"):
            return value
        prompt = key.replace("_", " ").title() + (f" ({env_key})" if env_key else "")
        from getpass import getpass

        return (getpass(f"{prompt}: ") if secret else input(f"{prompt}: ")).strip()

    def _assign_roles(self, user: User) -> list[str]:
        changes: list[str] = []

        group, created = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP)
        if created:
            changes.append(f"created the '{SUPER_ADMIN_GROUP}' group")
        group.permissions.set(Permission.objects.all())
        if not user.groups.filter(pk=group.pk).exists():
            user.groups.add(group)
            changes.append(f"added to '{SUPER_ADMIN_GROUP}'")

        # Group-gated views (Django admin, staff dashboards) must not hide
        # actions from the operator, so join every other group as well.
        for other in Group.objects.exclude(pk=group.pk):
            if not user.groups.filter(pk=other.pk).exists():
                user.groups.add(other)
                changes.append(f"added to '{other.name}'")

        return changes

    # ------------------------------------------------------------------- main
    @transaction.atomic
    def handle(self, *args, **options):
        username = self._value(options, "username", "PLATFORM_SUPERUSER_USERNAME")
        password = self._value(
            options, "password", "PLATFORM_SUPERUSER_PASSWORD", secret=True
        )
        phone = self._value(options, "phone", "PLATFORM_SUPERUSER_PHONE")
        national_id = self._value(
            options, "national_id", "PLATFORM_SUPERUSER_NATIONAL_ID"
        )

        for label, value in (
            ("username", username),
            ("password", password),
            ("phone", phone),
            ("national_id", national_id),
        ):
            if not value:
                raise CommandError(f"Missing required value: {label}")

        if not options.get("skip_password_validation"):
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise CommandError(
                    "Password rejected: "
                    + " ".join(exc.messages)
                    + " Re-run with --skip-password-validation to override."
                ) from exc

        user = User.objects.filter(username__iexact=username).first()
        created = user is None
        if created:
            user = User.objects.create_superuser(
                phone=phone,
                national_id=national_id,
                password=password,
                username=username,
                verification_status=User.VerificationStatus.VERIFIED,
            )
            user.verified_at = timezone.now()
            user.save(update_fields=["verified_at"])
            self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
        else:
            changed = []
            if user.phone != phone:
                user.phone = phone
                changed.append("phone")
            if user.national_id != national_id:
                user.national_id = national_id
                changed.append("national_id")
            if not user.check_password(password):
                user.set_password(password)
                changed.append("password")
            if not user.is_staff or not user.is_superuser or not user.is_active:
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                changed.append("admin flags")
            if user.verification_status != User.VerificationStatus.VERIFIED:
                user.verification_status = User.VerificationStatus.VERIFIED
                user.verified_at = user.verified_at or timezone.now()
                changed.append("verification")
            if changed:
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated superuser '{username}': {', '.join(changed)}."
                    )
                )
            else:
                self.stdout.write(f"Superuser '{username}' already up to date.")

        for line in self._assign_roles(user):
            self.stdout.write(f"  · {line}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Superuser '{username}' (id={user.pk}) holds every admin role: "
                "is_staff, is_superuser, all model permissions and all groups."
            )
        )
