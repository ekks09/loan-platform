from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from users.models import ConsentRecord, PasswordResetCode, User
from users.phone import (
    InvalidPhoneNumber,
    country_of,
    international,
    is_valid_ke_phone,
    is_valid_msisdn,
    mask_phone,
    national,
    normalize_ke_phone,
    normalize_msisdn,
)
from core.tests.helpers import (
    attach_documents,
    grant_current_consent,
    make_user,
)


class PhoneNormalizationTests(TestCase):
    def test_all_supported_formats_normalize_to_one_canonical_form(self):
        expected = "254712345678"
        for value in (
            "0712345678",
            "0712 345 678",
            "0712-345-678",
            "+254712345678",
            "254712345678",
            "712345678",
            "+254 712 345 678",
            "(0712) 345 678",
            "254-712-345-678",
        ):
            self.assertEqual(normalize_ke_phone(value), expected, value)

    def test_airtel_prefix_is_supported(self):
        # 0100… and 0730… are Airtel blocks and must be accepted like any
        # other valid Kenyan mobile number.
        self.assertEqual(normalize_ke_phone("0100 123 456"), "254100123456")
        self.assertEqual(normalize_ke_phone("254100123456"), "254100123456")
        self.assertEqual(normalize_ke_phone("0730123456"), "254730123456")
        self.assertEqual(normalize_ke_phone("+254 730 123 456"), "254730123456")

    def test_invalid_numbers_raise_domain_error(self):
        for value in (
            "",
            None,
            "12345",
            "071234567",       # too short
            "07123456789",     # too long
            "+254812345678",
            "25471234567a",
            "0999",            # Congo block, but far too short
            "2547123456789",
        ):
            with self.assertRaises(InvalidPhoneNumber, msg=repr(value)):
                normalize_ke_phone(value)

    def test_congo_numbers_are_accepted_and_kept_canonical(self):
        for value in ("0812345678", "+243812345678", "243812345678", "00243812345678"):
            self.assertEqual(normalize_msisdn(value), "243812345678", value)
        self.assertEqual(normalize_msisdn("+243 991 234 567"), "243991234567")
        self.assertEqual(country_of("243812345678"), "CD")
        self.assertEqual(country_of("254712345678"), "KE")
        self.assertEqual(national("243812345678"), "0812 345 678")
        self.assertEqual(international("243812345678"), "+243 812 345 678")
        self.assertTrue(is_valid_msisdn("+243 991 234 567"))

    def test_kenyan_normalizer_refuses_congo_numbers(self):
        # Loans and payouts stay Kenya-only, so the strict helper must not
        # silently accept a Congolese number.
        with self.assertRaises(InvalidPhoneNumber):
            normalize_ke_phone("+243812345678")

    def test_is_valid_helper_matches_normalizer(self):
        self.assertTrue(is_valid_ke_phone("0712345678"))
        self.assertFalse(is_valid_ke_phone("nope"))

    def test_mask_hides_middle_digits(self):
        self.assertEqual(mask_phone("254712345678"), "2547***78")
        self.assertEqual(mask_phone("123"), "***")


class UserModelTests(TestCase):
    def test_create_user_normalizes_phone_and_hashes_password(self):
        user = make_user(phone="0712 345 678")
        self.assertEqual(user.phone, "254712345678")
        self.assertNotEqual(user.password, "StrongPass!234")
        self.assertTrue(user.check_password("StrongPass!234"))
        self.assertEqual(user.verification_status, User.VerificationStatus.PENDING)

    def test_phone_must_be_unique(self):
        make_user(phone="0712345678", national_id="11111111")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_user(phone="0712345678", national_id="22222222")

    def test_national_id_must_be_unique(self):
        make_user(phone="0712345678", national_id="33333333")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_user(phone="0723456789", national_id="33333333")

    def test_national_id_is_masked_in_output(self):
        user = make_user(national_id="12345678")
        self.assertEqual(user.national_id_masked, "****5678")
        self.assertNotIn("12345678", user.national_id_masked)

    def test_can_borrow_requires_every_gate(self):
        user = make_user()
        self.assertFalse(user.can_borrow)

        attach_documents(user, prefix=f"users/{user.pk}")
        self.assertFalse(user.can_borrow)  # not verified

        grant_current_consent(user)
        self.assertFalse(user.can_borrow)  # still not verified

        user.verification_status = User.VerificationStatus.VERIFIED
        user.save(update_fields=["verification_status"])
        self.assertTrue(user.can_borrow)

        user.deletion_requested_at = timezone.now()
        user.save(update_fields=["deletion_requested_at"])
        self.assertFalse(user.can_borrow)

    def test_consent_is_version_scoped(self):
        user = make_user()
        self.assertFalse(user.has_current_consent)

        ConsentRecord.objects.create(
            user=user,
            purpose=ConsentRecord.Purpose.IDENTITY_PROCESSING,
            version="old-version",
        )
        ConsentRecord.objects.create(
            user=user,
            purpose=ConsentRecord.Purpose.TERMS,
            version="old-version",
        )
        self.assertFalse(user.has_current_consent)

        grant_current_consent(user)
        self.assertTrue(user.has_current_consent)

    def test_withdrawn_consent_does_not_count(self):
        user = make_user()
        grant_current_consent(user)
        user.consents.update(withdrawn_at=timezone.now())
        self.assertFalse(user.has_current_consent)


class PasswordResetCodeTests(TestCase):
    def test_code_is_six_digits_and_hashes_are_not_plaintext(self):
        user = make_user()
        code = PasswordResetCode.issue(user)
        self.assertRegex(code, r"^\d{6}$")
        row = user.password_reset_codes.get()
        self.assertNotIn(code, row.code_hash)

    def test_correct_code_verifies_and_consumes_once(self):
        user = make_user()
        code = PasswordResetCode.issue(user)
        row = user.password_reset_codes.get()
        self.assertTrue(row.verify_code(code))
        row.consume()
        row.refresh_from_db()
        self.assertIsNotNone(row.used_at)
        self.assertFalse(row.verify_code(code))

    def test_wrong_code_increments_attempts_and_locks_out(self):
        user = make_user()
        code = PasswordResetCode.issue(user)
        row = user.password_reset_codes.get()
        for _ in range(PasswordResetCode.MAX_ATTEMPTS):
            self.assertFalse(row.verify_code("000000" if code != "000000" else "111111"))
        row.refresh_from_db()
        self.assertEqual(row.attempts, PasswordResetCode.MAX_ATTEMPTS)
        self.assertFalse(row.verify_code(code))

    def test_expired_code_is_rejected(self):
        user = make_user()
        code = PasswordResetCode.issue(user)
        row = user.password_reset_codes.get()
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        self.assertFalse(row.verify_code(code))
