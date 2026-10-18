"""API surface guarantees: versioned paths, base URL wiring and CORS.

These tests pin the deployment contract rather than any single endpoint:

* `/api/v1/...` and the legacy `/api/...` aliases resolve to the same view.
* the API base is rendered into the page from settings, so no hostname is
  compiled into the JavaScript.
* a cross-origin request is only answered when its origin is explicitly
  allowed, and the response never advertises credentials.
"""
from __future__ import annotations

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.tests.helpers import auth_client, make_verified_user


class VersionedApiTests(TestCase):
    def test_anonymous_summary_is_401_on_both_paths(self):
        for path in ("/api/loyalty/summary/", "/api/v1/loyalty/summary/"):
            with self.subTest(path=path):
                response = APIClient().get(path)
                self.assertEqual(response.status_code, 401)

    def test_versioned_and_legacy_paths_return_the_same_payload(self):
        user = make_verified_user()
        client = auth_client(user)

        legacy = client.get("/api/loyalty/summary/")
        versioned = client.get("/api/v1/loyalty/summary/")

        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(legacy.json(), versioned.json())

    def test_webhook_is_reachable_on_the_versioned_path(self):
        # A GET is rejected by the view, but routing must reach it: a 404 would
        # mean Paystack is posting into a dead URL.
        response = APIClient().get("/api/v1/webhooks/paystack/")
        self.assertNotEqual(response.status_code, 404)


class ApiBaseRenderingTests(TestCase):
    def test_meta_tag_carries_the_configured_base(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, '<meta name="api-base" content="%s"' % settings.API_BASE_URL, html=False
        )

    @override_settings(API_BASE_URL="/api/v1")
    def test_relative_base_has_no_hostname(self):
        response = self.client.get("/login/")
        body = response.content.decode()
        self.assertIn('name="api-base" content="/api/v1"', body)
        self.assertNotIn("localhost", body)


class CorsPolicyTests(TestCase):
    def test_allowed_origin_gets_a_cors_header_without_credentials(self):
        with override_settings(CORS_ALLOWED_ORIGINS=["https://app.example.test"]):
            response = self.client.get(
                "/api/loyalty/summary/", HTTP_ORIGIN="https://app.example.test"
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response["Access-Control-Allow-Origin"], "https://app.example.test"
        )
        self.assertNotIn("Access-Control-Allow-Credentials", response)

    def test_unknown_origin_is_not_reflected(self):
        with override_settings(CORS_ALLOWED_ORIGINS=["https://app.example.test"]):
            response = self.client.get(
                "/api/loyalty/summary/", HTTP_ORIGIN="https://evil.example.test"
            )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Access-Control-Allow-Origin", response)
