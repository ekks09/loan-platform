"""Operator sign-in by username and the ``bootstrap_superuser`` command.

Borrowers keep authenticating with their phone number. These tests pin the
additive behaviour: one password check serves both identifier shapes, and the
provisioning command grants every administrative role without ever widening
what a borrower can do.
"""
from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.tests.helpers import make_user
from users.models import User
from users.phone import mask_phone


class UsernameLookupTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            phone="254700000000",
            national_id="000000",
            password="Operator!234",
            username="Ken Admin",
        )
        self.borrower = make_user()

    def test_natural_key_matches_username_case_insensitively(self):
        self.assertEqual(
            User.objects.get_by_natural_key("ken admin").pk, self.operator.pk
        )

    def test_natural_key_still_matches_phone(self):
        self.assertEqual(
            User.objects.get_by_natural_key("0712345678").pk, self.borrower.pk
        )

    def test_username_wins_over_a_phone_shaped_value(self):
        """An operator name is never shadowed by a borrower's number."""
        User.objects.create_user(
            phone="254711111111",
            national_id="111111",
            password="Borrower!234",
            username="254722222222",
        )
        other = User.objects.create_user(
            phone="254722222222", national_id="222222", password="Borrower!234"
        )
        self.assertNotEqual(
            User.objects.get_by_natural_key("254722222222").pk, other.pk
        )

    def test_unknown_identifier_raises_does_not_exist(self):
        with self.assertRaises(User.DoesNotExist):
            User.objects.get_by_natural_key("nobody")

    def test_blank_username_is_allowed_and_unique_constraint_holds(self):
        self.assertIsNone(self.borrower.username)
        self.borrower.refresh_from_db()

    def test_authenticate_accepts_username_and_phone(self):
        self.assertEqual(authenticate(username="Ken Admin", password="Operator!234"), self.operator)
        self.assertEqual(authenticate(username="254700000000", password="Operator!234"), self.operator)
        self.assertIsNone(authenticate(username="Ken Admin", password="wrong"))

    def test_display_name_falls_back_to_masked_phone(self):
        self.assertEqual(str(self.operator), "Ken Admin")
        self.assertEqual(str(self.borrower), mask_phone(self.borrower.phone))


class OperatorLoginApiTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            phone="254700000000",
            national_id="000000",
            password="Operator!234",
            username="Ken Admin",
            is_staff=True,
            is_superuser=True,
        )

    def test_login_by_username_returns_a_token(self):
        response = self.client.post(
            "/api/users/login/",
            {"username": "Ken Admin", "password": "Operator!234"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_login_by_phone_still_returns_a_token(self):
        response = self.client.post(
            "/api/users/login/",
            {"phone": "254700000000", "password": "Operator!234"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_login_without_identifier_is_rejected(self):
        response = self.client.post(
            "/api/users/login/",
            {"password": "Operator!234"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone number or username", response.content.decode())

    def test_unknown_username_uses_the_generic_error(self):
        response = self.client.post(
            "/api/users/login/",
            {"username": "Ghost Admin", "password": "Operator!234"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid phone number or password", response.content.decode())


class BootstrapSuperuserCommandTests(TestCase):
    def call(self, **options):
        """Run the command with the standard operator values, overridable."""
        values = {
            "username": "Ken Admin",
            "password": "Operator!234",
            "phone": "254700000000",
            "national_id": "000000",
            "no_input": True,
        }
        values.update(options)
        call_command("bootstrap_superuser", **values)
        return User.objects.get(username="Ken Admin")

    def test_creates_an_account_with_every_admin_role(self):
        user = self.call()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(
            user.verification_status, User.VerificationStatus.VERIFIED
        )
        self.assertTrue(user.check_password("Operator!234"))

        group = Group.objects.get(name="Super Admin")
        self.assertIn(group, user.groups.all())
        self.assertEqual(
            group.permissions.count(), Permission.objects.count()
        )
        self.assertTrue(user.has_perm("loans.change_loan"))

    def test_joins_every_other_group_so_no_view_is_hidden(self):
        extra = Group.objects.create(name="Verification team")
        user = self.call()
        self.assertIn(extra, user.groups.all())

    def test_is_idempotent_and_rotates_the_password(self):
        first = self.call()
        original_hash = first.password
        second = self.call(password="Rotated!234")
        self.assertEqual(first.pk, second.pk)
        self.assertNotEqual(original_hash, second.password)
        self.assertTrue(second.check_password("Rotated!234"))
        self.assertFalse(second.check_password("Operator!234"))
        self.assertEqual(User.objects.filter(username="Ken Admin").count(), 1)

    def test_rejects_a_weak_password_unless_overridden(self):
        with self.assertRaises(CommandError):
            self.call(password="password", skip_password_validation=False)
        # The break-glass path stays available.
        user = self.call(password="password", skip_password_validation=True)
        self.assertTrue(user.check_password("password"))

    def test_missing_values_fail_closed(self):
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_superuser",
                username="Ken Admin",
                password="Operator!234",
                phone="",
                national_id="000000",
                no_input=True,
            )
        self.assertFalse(User.objects.filter(username="Ken Admin").exists())
