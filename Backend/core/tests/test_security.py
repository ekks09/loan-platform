from __future__ import annotations

import time

import jwt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.tests.helpers import auth_client, make_user, make_verified_user
from loans.models import Loan
from loans.services import create_application
from users.authentication import _jwt_encode


class TokenSecurityTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = auth_client(self.user)

    def _token(self, **overrides) -> str:
        now = int(time.time())
        payload = {
            "sub": str(self.user.pk),
            "iat": now,
            "nbf": now,
            "exp": now + 600,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "type": "access",
        }
        payload.update(overrides)
        return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

    def assert_rejected(self, token: str):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/users/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_failed")

    def test_expired_token_is_rejected(self):
        self.assert_rejected(self._token(exp=int(time.time()) - 1))

    def test_wrong_audience_is_rejected(self):
        self.assert_rejected(self._token(aud="some-other-app"))

    def test_wrong_issuer_is_rejected(self):
        self.assert_rejected(self._token(iss="attacker"))

    def test_token_signed_with_another_key_is_rejected(self):
        forged = jwt.encode(
            {
                "sub": str(self.user.pk),
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "type": "access",
            },
            "attacker-controlled-key-that-is-long-enough",
            algorithm="HS256",
        )
        self.assert_rejected(forged)

    def test_refresh_or_malformed_type_is_rejected(self):
        self.assert_rejected(self._token(type="refresh"))

    def test_unsigned_token_is_rejected(self):
        unsigned = jwt.encode(
            {"sub": str(self.user.pk), "exp": int(time.time()) + 600},
            key="",
            algorithm="none",
        )
        self.assert_rejected(unsigned)

    def test_deleted_user_token_is_rejected(self):
        token = _jwt_encode(self.user)
        self.user.delete()
        self.assert_rejected(token)

    def test_inactive_user_token_is_rejected(self):
        token = _jwt_encode(self.user)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assert_rejected(token)


class LoanCurrencyTests(TestCase):
    def test_only_kes_is_accepted(self):
        user = make_verified_user()
        with self.assertRaises(ValidationError):
            Loan.objects.create(
                user=user,
                amount=10_000,
                service_fee=1200,
                mpesa_phone="254712345678",
                currency="USD",
            )


class ErrorEnvelopeTests(TestCase):
    def test_validation_errors_use_the_documented_envelope(self):
        response = self.client.post(
            "/api/users/register/",
            {"phone": "bad"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertIn("code", body["error"])
        self.assertIn("message", body["error"])
        self.assertIn("details", body["error"])
        self.assertIn("request_id", body["error"])

    def test_unknown_api_route_is_json_not_html(self):
        response = self.client.get("/api/does-not-exist/")
        self.assertEqual(response.status_code, 404)
