"""Test settings: SQLite, fast hashing, no external services required.

Used by ``manage.py test`` and by CI. Production settings validation is still
exercised separately by the ``check_config`` management command and by tests
that import the validation helpers directly.
"""
from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-that-is-long-enough-1234")
os.environ["DJANGO_ALLOW_INSECURE_SECRETS"] = "1"
os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("PAYMENTS_ENABLED", "true")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_platform_test_secret_key")
os.environ.setdefault("PAYSTACK_PUBLIC_KEY", "pk_test_platform_test_public_key")
os.environ.setdefault("PAYMENTS_LIVE_MODE", "false")
os.environ.setdefault("SMS_BACKEND", "console")
os.environ.setdefault("REQUIRE_DOCUMENT_STORAGE", "false")
os.environ.setdefault("DJANGO_DEV_SQLITE", "1")
os.environ.setdefault("DJANGO_SQLITE_PATH", "/tmp/loan-platform-dev.sqlite3")  # nosec B108 -- test-only sqlite path

# A developer's local Backend/.env is loaded by core.settings below. Anything
# security-relevant that the suite asserts on is pinned here first, with a hard
# assignment, so a local preview file cannot change what the tests prove. The
# iframe-relaxation flag is the one that bit: with it on, the CSP test that
# requires frame-ancestors 'none' fails for the wrong reason.
os.environ["DJANGO_PREVIEW_EMBED"] = "0"
os.environ["DJANGO_SECURE_SSL"] = "0"
os.environ["DJANGO_HSTS_SECONDS"] = "0"

# Posture surface — hard assignments, NOT setdefaults. python-dotenv loads
# with override=False, so a setdefault only wins when the key was absent; a
# demo/preview .env (payments on, upfront fees, s3 document store, required
# storage...) would otherwise leak through and silently change what the suite
# proves. The suite must produce identical results on every machine.
os.environ.update(
    {
        "DEPLOY_ENVIRONMENT": "development",
        "FEE_COLLECTION_MODE": "disabled",
        "FEE_COLLECTION_APPROVED": "false",
        "LEGAL_TERMS_APPROVED": "false",
        "DOCUMENT_STORE": "supabase",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_KEY": "",
        "SUPABASE_STORAGE_BUCKET": "",
        "SUPABASE_DB_HOST": "",
        "S3_ENDPOINT_URL": "",
        "S3_PUBLIC_ENDPOINT_URL": "",
        "S3_ACCESS_KEY": "",
        "S3_SECRET_KEY": "",  # nosec B105 -- deliberately empty test posture
        "S3_BUCKET": "",
        "S3_REGION": "us-east-1",
        "NOTIFICATION_MAX_RETRIES": "1",
        "OTP_RESEND_COOLDOWN_SECONDS": "60",
    }
)

from core.settings import *  # noqa: E402,F401,F403

# Belt and braces: settings are re-derived from the environment above, so pin
# the resolved values too. Every test that cares about framing runs against a
# non-embeddable build.
PREVIEW_EMBED_ALLOWED = False

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ["DJANGO_SQLITE_PATH"],
        # Test runs use an isolated in-memory database automatically.
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = "/tmp/loan-platform-test-media"  # nosec B108 -- test-only media root
LOGGING = {"version": 1, "disable_existing_loggers": True}
SILENCED_SYSTEM_CHECKS = ["core.W004", "core.W006"]

# The test suite issues many requests from a single client address, so the
# production rate limits would otherwise throttle unrelated tests. Individual
# tests that exercise throttling override these values explicitly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "anon": "10000/hour",
        "quote": "10000/hour",
        "user": "10000/hour",
        "login": "10000/hour",  # nosec B105 -- test throttle rate, not a credential
        "register": "10000/hour",
        "password_reset": "10000/hour",
        "payment_init": "10000/hour",
        "documents": "10000/hour",
        "verify": "10000/hour",
        "vault": "10000/hour",
    },
}
