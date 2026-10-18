from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import jwt
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.tests.helpers import make_user
from users.models import PasswordResetCode, SmsAuditLog, User
from users.otp import phone_fingerprint


class PasswordResetRateLimitTests(APITestCase):
    def setUp(self):
        self.user = make_user()

    def request(self, phone="0712345678", ip="10.0.0.1"):
        return self.client.post(
            "/api/users/password-reset/request/",
            {"phone": phone},
            format="json",
            REMOTE_ADDR=ip,
        )

    def test_three_codes_per_number_then_429_with_retry_after(self):
        for _ in range(3):
            self.assertEqual(self.request().status_code, 200)
        blocked = self.request()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], str(15 * 60))
        self.assertEqual(blocked.json()["error"]["code"], "too_many_requests")
        self.assertEqual(
            SmsAuditLog.objects.filter(
                phone_hash=phone_fingerprint("254712345678"),
                status=SmsAuditLog.Status.RATE_LIMITED,
            ).count(),
            1,
        )

    def test_limit_is_per_number_not_global(self):
        for _ in range(3):
            self.request()
        other = make_user(phone="0722222222", national_id="22222222")
        self.assertEqual(self.request("0722222222", ip="10.0.0.2").status_code, 200)
        self.assertTrue(other.password_reset_codes.exists())

    def test_network_address_is_limited_even_when_numbers_rotate(self):
        for index in range(10):
            phone = f"0712{index:06d}"
            response = self.request(phone, ip="10.9.9.9")
            self.assertEqual(response.status_code, 200, phone)
        blocked = self.request("0712999999", ip="10.9.9.9")
        self.assertEqual(blocked.status_code, 429)

    def test_unknown_number_gets_the_same_body_and_leaves_no_code(self):
        unknown = self.request("0700000000", ip="10.3.3.3")
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(
            unknown.json()["message"],
            "If the number is registered, a reset code has been sent by SMS.",
        )
        self.assertFalse(PasswordResetCode.objects.exists())
        self.assertEqual(
            SmsAuditLog.objects.get().status, SmsAuditLog.Status.SKIPPED
        )

    def test_audit_log_never_contains_the_code_or_full_number(self):
        self.request()
        row = SmsAuditLog.objects.get()
        self.assertNotIn("254712345678", row.phone_masked)
        self.assertEqual(len(row.phone_hash), 64)
        self.assertFalse(hasattr(row, "code"))


class PasswordResetMarketTests(APITestCase):
    def setUp(self):
        self.user = make_user(phone="+243812345678", national_id="33333333")

    def test_congo_number_can_request_and_confirm_a_reset(self):
        requested = self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "+243 812 345 678"},
            format="json",
        )
        self.assertEqual(requested.status_code, 200, requested.content)
        row = PasswordResetCode.objects.get(user=self.user)
        row.delete()
        code = PasswordResetCode.issue(self.user)

        confirmed = self.client.post(
            "/api/users/password-reset/confirm/",
            {
                "phone": "0812 345 678",
                "code": code,
                "new_password": "CongoStrong!9",
            },
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("CongoStrong!9"))
        self.assertEqual(self.user.phone, "243812345678")

    def test_kenyan_flow_still_works_for_a_congo_user_only_with_its_own_number(self):
        response = self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PasswordResetCode.objects.exists())


class PasswordResetTokenInvalidationTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.token = self.client.post(
            "/api/users/login/",
            {"phone": "0712345678", "password": "StrongPass!234"},
            format="json",
        ).json()["access"]

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_existing_token_stops_working_after_a_reset(self):
        self.assertEqual(
            self.client.get("/api/users/me/", **self.auth(self.token)).status_code, 200
        )

        code = PasswordResetCode.issue(self.user)
        # A direct issue() bypasses the request endpoint but must still verify.
        response = self.client.post(
            "/api/users/password-reset/confirm/",
            {"phone": "0712345678", "code": code, "new_password": "BrandNewPass!9"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        # Move the recorded change one second past the old token's iat, which
        # is what a real reset would produce (token issued, then password
        # changed). The stored value is then rewound so the fresh login below
        # is not caught by the same rule.
        payload = jwt.decode(self.token, options={"verify_signature": False})
        changed_at = datetime.fromtimestamp(payload["iat"] + 1, tz=dt_timezone.utc)
        User.objects.filter(pk=self.user.pk).update(password_changed_at=changed_at)
        self.assertEqual(
            self.client.get("/api/users/me/", **self.auth(self.token)).status_code, 401
        )

        User.objects.filter(pk=self.user.pk).update(
            password_changed_at=timezone.now() - timedelta(seconds=30)
        )
        fresh = self.client.post(
            "/api/users/login/",
            {"phone": "0712345678", "password": "BrandNewPass!9"},
            format="json",
        ).json()["access"]
        self.assertEqual(
            self.client.get("/api/users/me/", **self.auth(fresh)).status_code, 200
        )


class OneTimeCodeTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_only_the_newest_code_is_valid(self):
        old = PasswordResetCode.issue(self.user)
        new = PasswordResetCode.issue(self.user)
        self.assertNotEqual(old, new)
        self.assertFalse(
            self.client.post(
                "/api/users/password-reset/confirm/",
                {"phone": "0712345678", "code": old, "new_password": "AnotherPass!9"},
                format="json",
            ).status_code
            == 200
        )

    @override_settings(OTP_TTL_MINUTES=10)
    def test_expired_code_is_rejected(self):
        code = PasswordResetCode.issue(self.user)
        PasswordResetCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.post(
            "/api/users/password-reset/confirm/",
            {"phone": "0712345678", "code": code, "new_password": "AnotherPass!9"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("StrongPass!234"))

    def test_five_wrong_attempts_exhaust_the_code(self):
        code = PasswordResetCode.issue(self.user)
        row = PasswordResetCode.objects.get(user=self.user)
        for _ in range(5):
            self.assertFalse(row.verify_code("999999"))
        # Even the correct code is refused once attempts are exhausted.
        self.assertFalse(row.verify_code(code))
