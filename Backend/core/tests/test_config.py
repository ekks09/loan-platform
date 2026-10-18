from __future__ import annotations

import os
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.config import (
    env_bool,
    env_int,
    env_list,
    normalise_hosts,
    normalise_origins,
    validate_secret,
)


class EnvironmentParsingTests(SimpleTestCase):
    def test_env_bool_accepts_documented_truthy_values(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {"FLAG": value}):
                self.assertTrue(env_bool("FLAG"))
        for value in ("0", "false", "no", "", "off"):
            with patch.dict(os.environ, {"FLAG": value}):
                self.assertFalse(env_bool("FLAG"))

    def test_env_int_rejects_non_numeric_values(self):
        with patch.dict(os.environ, {"PORT": "not-a-number"}):
            with self.assertRaises(ImproperlyConfigured):
                env_int("PORT", 8000)

    def test_env_list_trims_empty_entries(self):
        with patch.dict(os.environ, {"HOSTS": " a.example.com, ,b.example.com "}):
            self.assertEqual(env_list("HOSTS"), ["a.example.com", "b.example.com"])


class HostAndOriginValidationTests(SimpleTestCase):
    def test_hosts_reject_scheme_and_path(self):
        for value in ("https://example.com", "example.com/path"):
            with self.assertRaises(ImproperlyConfigured):
                normalise_hosts([value], debug=False)

    def test_wildcard_host_rejected_outside_debug(self):
        with self.assertRaises(ImproperlyConfigured):
            normalise_hosts(["*"], debug=False)

    def test_wildcard_host_allowed_in_debug(self):
        self.assertEqual(normalise_hosts(["*"], debug=True), ["*"])

    def test_partial_wildcard_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            normalise_hosts(["*.example.com"], debug=True)

    def test_origins_must_have_scheme_and_no_path(self):
        self.assertEqual(
            normalise_origins(["https://example.com/"]), ["https://example.com"]
        )
        for value in ("example.com", "https://example.com/path"):
            with self.assertRaises(ImproperlyConfigured):
                normalise_origins([value])


class SecretValidationTests(SimpleTestCase):
    def test_missing_secret_always_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_secret("DJANGO_SECRET_KEY", "", debug=False, allow_insecure=False)

    def test_placeholder_secret_fails_in_production(self):
        for value in ("change-me", "<YOUR_DJANGO_SECRET_KEY>", "secret"):
            with self.assertRaises(ImproperlyConfigured):
                validate_secret(
                    "DJANGO_SECRET_KEY", value, debug=False, allow_insecure=False
                )

    def test_short_secret_fails_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_secret(
                "DJANGO_SECRET_KEY", "a" * 31, debug=False, allow_insecure=False
            )

    def test_long_secret_accepted(self):
        value = "a" * 40
        self.assertEqual(
            validate_secret(
                "DJANGO_SECRET_KEY", value, debug=False, allow_insecure=False
            ),
            value,
        )

    def test_insecure_override_is_explicit(self):
        self.assertEqual(
            validate_secret(
                "DJANGO_SECRET_KEY", "short", debug=True, allow_insecure=True
            ),
            "short",
        )


@override_settings(
    DEBUG=False,
    SECRET_KEY="test-secret-key-that-is-long-enough-1234",
    ALLOWED_HOSTS=["example.test"],
    CSRF_TRUSTED_ORIGINS=["https://example.test"],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    PAYMENTS_ENABLED=True,
    PAYMENTS_LIVE_MODE=False,
    PAYSTACK_SECRET_KEY="sk_test_platform_test_secret_key",
    FEE_COLLECTION_MODE="disabled",
    REQUIRE_DOCUMENT_STORAGE=False,
    SMS_BACKEND="console",
)
class CheckConfigCommandTests(TestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("check_config", *args, stdout=out)
        return out.getvalue()

    def test_valid_staging_configuration_passes_with_warnings(self):
        output = self._run()
        self.assertIn("Configuration OK", output)
        self.assertIn("WARN", output)  # test keys warning

    def test_insecure_cookies_fail_in_production(self):
        with override_settings(SESSION_COOKIE_SECURE=False):
            with self.assertRaises(CommandError):
                self._run()

    def test_live_mode_requires_legal_approval(self):
        # Production settings refuse this combination at import time; the
        # command is the second line of defence for an already-running app.
        with override_settings(PAYMENTS_LIVE_MODE=True, LEGAL_TERMS_APPROVED=False):
            with self.assertRaises(CommandError):
                self._run()

    def test_live_mode_with_test_keys_fails_even_with_allow_flag(self):
        with override_settings(PAYMENTS_LIVE_MODE=True, LEGAL_TERMS_APPROVED=True):
            with self.assertRaises(CommandError):
                self._run("--allow-test-mode")

    def test_upfront_fee_without_approval_fails(self):
        with override_settings(
            FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=False
        ):
            with self.assertRaises(CommandError):
                self._run()

    def test_missing_storage_when_required_fails(self):
        with override_settings(
            REQUIRE_DOCUMENT_STORAGE=True, SUPABASE_URL="", SUPABASE_SERVICE_KEY=""
        ):
            with self.assertRaises(CommandError):
                self._run()
