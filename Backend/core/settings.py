"""Django settings for the loan platform.

Every value that differs between environments is read from the environment.
Production refuses to boot with missing or placeholder secrets; nothing here
falls back to a localhost API or a development database in production.
"""
from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from core.config import (
    env,
    env_bool,
    env_float,
    env_int,
    env_list,
    normalise_hosts,
    normalise_origins,
    validate_secret,
)
from core.pwa import resolve_cache_version

# =========================================================================
# BASE
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent  # .../Backend

# Local development convenience: read Backend/.env if it exists. Real
# environment variables always win (override=False), so this cannot mask a
# platform-provided secret.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for production images
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env", override=False)

DEBUG = env_bool("DJANGO_DEBUG", False)
# Deployment posture label: development (local), staging (pre-production
# rehearsal), production (live). Used only for guardrails — behaviour must
# otherwise be identical so staging exercises the production build.
DEPLOY_ENVIRONMENT = env("DEPLOY_ENVIRONMENT", "development").strip().lower()
if DEPLOY_ENVIRONMENT not in {"development", "staging", "production"}:
    raise ImproperlyConfigured(
        f"DEPLOY_ENVIRONMENT must be development|staging|production, got {DEPLOY_ENVIRONMENT!r}"
    )
ALLOW_INSECURE_SECRETS = env_bool("DJANGO_ALLOW_INSECURE_SECRETS", False)

# =========================================================================
# SECURITY / SECRETS
# =========================================================================
SECRET_KEY = validate_secret(
    "DJANGO_SECRET_KEY",
    env("DJANGO_SECRET_KEY", "") or "",
    debug=DEBUG,
    allow_insecure=ALLOW_INSECURE_SECRETS,
)

# Render injects RENDER_EXTERNAL_HOSTNAME for every web service. Using it as a
# fallback keeps the deploy correct without copying a hostname (which changes
# when the service is renamed) into the repository. An explicit
# DJANGO_ALLOWED_HOSTS always wins.
_RENDER_HOSTNAME = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip()
_host_values = env_list("DJANGO_ALLOWED_HOSTS")
if not _host_values and _RENDER_HOSTNAME:
    _host_values = [_RENDER_HOSTNAME]

ALLOWED_HOSTS = normalise_hosts(_host_values, debug=DEBUG)
if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
    else:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS is required when DEBUG is false. "
            "Set it to the exact hostname(s) Render serves (comma separated)."
        )

_origin_values = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not _origin_values and _RENDER_HOSTNAME:
    _origin_values = [f"https://{_RENDER_HOSTNAME}"]

CSRF_TRUSTED_ORIGINS = normalise_origins(_origin_values)
CORS_ALLOWED_ORIGINS = normalise_origins(env_list("CORS_ALLOWED_ORIGINS"))
# The API authenticates with a bearer token, never with cookies, so credentialed
# CORS is not needed. Leaving it off also stops a browser from attaching
# session cookies to a cross-origin API call.
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]
# Only the verbs the API actually exposes. Webhooks are server-to-server.
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
CORS_URLS_REGEX = r"^/api/.*$"

TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS", True)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if TRUST_PROXY_HEADERS else None
)
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", False)

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL", False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# HSTS is opt-in. Preloading is deliberately never enabled by default: it is
# difficult to reverse and must only be set after the domain is final.
SECURE_HSTS_SECONDS = env_int(
    "DJANGO_HSTS_SECONDS", 31536000 if (SECURE_SSL_REDIRECT and not DEBUG) else 0
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0 and env_bool(
    "DJANGO_HSTS_INCLUDE_SUBDOMAINS", False
)
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Sandbox preview only. The hosted preview renders the app inside an iframe
# from a different origin, so framing headers would show a blank panel. This
# flag is off by default and must stay off in every deployed environment.
PREVIEW_EMBED_ALLOWED = env_bool("DJANGO_PREVIEW_EMBED", False)
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# =========================================================================
# APPLICATIONS
# =========================================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "core",
    "users",
    "loans",
    "payments",
    "loyalty",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "core.middleware.RequestIDMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "Frontend"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.platform",
            ],
        },
    },
]

# =========================================================================
# DATABASE
# =========================================================================
DB_HOST = env("SUPABASE_DB_HOST")

if DB_HOST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("SUPABASE_DB_NAME", "postgres"),
            "USER": env("SUPABASE_DB_USER", "postgres"),
            "PASSWORD": env("SUPABASE_DB_PASSWORD", ""),
            "HOST": DB_HOST,
            "PORT": env_int("SUPABASE_DB_PORT", 5432),
            "OPTIONS": {"sslmode": env("SUPABASE_DB_SSLMODE", "require")},
            "CONN_MAX_AGE": env_int("DJANGO_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
    }
elif env_bool("DJANGO_DEV_SQLITE", False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("DJANGO_SQLITE_PATH", str(BASE_DIR / "dev.sqlite3")),
        }
    }
else:
    raise ImproperlyConfigured(
        "No database configured. Set SUPABASE_DB_HOST (production) or "
        "DJANGO_DEV_SQLITE=1 (local development only)."
    )

# =========================================================================
# CACHE / THROTTLING
# =========================================================================
if env("REDIS_URL"):
    raise ImproperlyConfigured(
        "REDIS_URL is set but no Redis cache backend is installed. "
        "Either remove REDIS_URL or add django-redis before enabling it."
    )
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "loan-platform",
    }
}

# =========================================================================
# STORAGE (private documents)
# =========================================================================
# DOCUMENT_STORE selects the adapter (validated at startup; fail closed).
#   supabase — Supabase Storage (existing production default)
#   s3       — S3-compatible object storage (AWS S3, MinIO, …)
DOCUMENT_STORE = (env("DOCUMENT_STORE", "supabase") or "").lower()
if DOCUMENT_STORE not in {"supabase", "s3"}:
    raise ImproperlyConfigured(
        f"DOCUMENT_STORE {DOCUMENT_STORE!r} is not supported. Choose: supabase, s3."
    )

# --- Supabase adapter -----------------------------------------------------
SUPABASE_URL = env("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY", "")
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", "identity-documents")
SUPABASE_STORAGE_PRIVATE = env_bool("SUPABASE_STORAGE_PRIVATE", True)

# --- S3-compatible adapter (MinIO local testing / S3 production) ----------
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", "")  # server-side operations
# Split-horizon signing: presigned GET URLs are generated for this origin
# instead (defaults to S3_ENDPOINT_URL). Needed when the server talks to the
# store over an in-cluster address (e.g. docker compose `http://minio:9000`)
# but the browser downloads from the published one (http://localhost:9000) —
# SigV4 signs the host header, so the host in the URL must be the one the
# browser will actually reach.
S3_PUBLIC_ENDPOINT_URL = env("S3_PUBLIC_ENDPOINT_URL", S3_ENDPOINT_URL)
S3_REGION = env("S3_REGION", "us-east-1")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", "")
S3_SECRET_KEY = env("S3_SECRET_KEY", "")
S3_BUCKET = env("S3_BUCKET", "")

DOCUMENT_SIGNED_URL_TTL = env_int("DOCUMENT_SIGNED_URL_TTL", 300)
DOCUMENT_RETENTION_DAYS = env_int("DOCUMENT_RETENTION_DAYS", 90)
DOCUMENT_MAX_BYTES = env_int("DOCUMENT_MAX_BYTES", 5 * 1024 * 1024)
REQUIRE_DOCUMENT_STORAGE = env_bool("REQUIRE_DOCUMENT_STORAGE", not DEBUG)

if DOCUMENT_STORE == "s3" and REQUIRE_DOCUMENT_STORAGE:
    missing = [
        name
        for name, value in (
            ("S3_ENDPOINT_URL", S3_ENDPOINT_URL),
            ("S3_ACCESS_KEY", S3_ACCESS_KEY),
            ("S3_SECRET_KEY", S3_SECRET_KEY),
            ("S3_BUCKET", S3_BUCKET),
        )
        if not value
    ]
    if missing:
        raise ImproperlyConfigured(
            "DOCUMENT_STORE=s3 with required storage but the following are "
            "missing: " + ", ".join(missing)
        )

FILE_UPLOAD_MAX_MEMORY_SIZE = DOCUMENT_MAX_BYTES
DATA_UPLOAD_MAX_MEMORY_SIZE = DOCUMENT_MAX_BYTES * 4 + 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 25

# =========================================================================
# STATIC FILES
# =========================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "Frontend"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
WHITENOISE_MAX_AGE = env_int("WHITENOISE_MAX_AGE", 3600)

# =========================================================================
# AUTH
# =========================================================================
AUTH_USER_MODEL = "users.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =========================================================================
# REST FRAMEWORK
# =========================================================================
THROTTLE_ANON = env("THROTTLE_ANON", "60/min")
THROTTLE_QUOTE = env("THROTTLE_QUOTE", "60/min")
THROTTLE_USER = env("THROTTLE_USER", "120/min")
THROTTLE_LOGIN = env("THROTTLE_LOGIN", "10/min")
THROTTLE_REGISTER = env("THROTTLE_REGISTER", "5/hour")
THROTTLE_PASSWORD_RESET = env("THROTTLE_PASSWORD_RESET", "5/hour")

# ---------------------------------------------------------------------------
# SMS one-time codes
# ---------------------------------------------------------------------------
# Hard limits enforced in the view (not only by the DRF throttle, which is
# keyed on IP and can be bypassed through a proxy pool).
OTP_MAX_REQUESTS = env_int("OTP_MAX_REQUESTS", 3)
OTP_WINDOW_MINUTES = env_int("OTP_WINDOW_MINUTES", 15)
OTP_MAX_PER_IP = env_int("OTP_MAX_PER_IP", 10)
OTP_TTL_MINUTES = env_int("OTP_TTL_MINUTES", 10)
THROTTLE_PAYMENT_INIT = env("THROTTLE_PAYMENT_INIT", "20/hour")
THROTTLE_DOCUMENTS = env("THROTTLE_DOCUMENTS", "10/hour")
THROTTLE_VERIFY = env("THROTTLE_VERIFY", "30/hour")

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": ("users.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": THROTTLE_ANON,
        "quote": THROTTLE_QUOTE,
        "user": THROTTLE_USER,
        "login": THROTTLE_LOGIN,
        "register": THROTTLE_REGISTER,
        "password_reset": THROTTLE_PASSWORD_RESET,
        "vault": THROTTLE_PASSWORD_RESET,
        "payment_init": THROTTLE_PAYMENT_INIT,
        "documents": THROTTLE_DOCUMENTS,
        "verify": THROTTLE_VERIFY,
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# =========================================================================
# JWT
# =========================================================================
JWT_ACCESS_TTL_MINUTES = env_int("JWT_ACCESS_TTL_MINUTES", 30)
JWT_ISSUER = env("JWT_ISSUER", "loan-platform")
JWT_AUDIENCE = env("JWT_AUDIENCE", "loan-platform-users")

# =========================================================================
# PAYMENTS (provider abstraction)
# =========================================================================
# Fail closed: a deployment must explicitly opt in to taking payments.
PAYMENTS_ENABLED = env_bool("PAYMENTS_ENABLED", False)
PAYMENTS_LIVE_MODE = env_bool("PAYMENTS_LIVE_MODE", False)
# Staging rehearse with test keys / disabled only: a staging deployment must
# never be able to move real money, whatever env var someone pastes into the
# dashboard. This is startup-fatal, matching LEGAL_TERMS_APPROVED posture.
if DEPLOY_ENVIRONMENT == "staging" and PAYMENTS_LIVE_MODE:
    raise ImproperlyConfigured(
        "Staging can never run PAYMENTS_LIVE_MODE=true. "
        "Use test keys (PAYMENTS_LIVE_MODE=false) or disable payments; "
        "live mode belongs to production only."
    )

# Provider selection. Business logic talks to the PaymentProvider contract
# (payments.providers); only the named adapter holds provider-specific
# behaviour. The set is validated at startup so a typo fails the boot rather
# than the first charge.
PAYMENT_PROVIDER = (env("PAYMENT_PROVIDER", "paystack") or "").lower()
SUPPORTED_PAYMENT_PROVIDERS = frozenset({"paystack"})
if PAYMENT_PROVIDER not in SUPPORTED_PAYMENT_PROVIDERS:
    raise ImproperlyConfigured(
        f"PAYMENT_PROVIDER {PAYMENT_PROVIDER!r} is not supported. "
        f"Choose one of: {', '.join(sorted(SUPPORTED_PAYMENT_PROVIDERS))}."
    )

# --- Paystack adapter settings (required when the provider is active) -----
PAYSTACK_BASE_URL = env("PAYSTACK_BASE_URL", "https://api.paystack.co")
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "") or ""
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "") or ""
PAYSTACK_REQUEST_TIMEOUT = env_int("PAYSTACK_REQUEST_TIMEOUT", 20)
PAYSTACK_MAX_RETRIES = env_int("PAYSTACK_MAX_RETRIES", 2)
PAYSTACK_TRANSFER_MAX_ATTEMPTS = env_int("PAYSTACK_TRANSFER_MAX_ATTEMPTS", 5)
PAYSTACK_TRANSFER_RETRY_BACKOFF_SECONDS = env_int(
    "PAYSTACK_TRANSFER_RETRY_BACKOFF_SECONDS", 300
)
PAYSTACK_VERIFY_ON_WEBHOOK = env_bool("PAYSTACK_VERIFY_ON_WEBHOOK", True)
PAYSTACK_MIN_LOAN_KES = env_int("PAYSTACK_MIN_LOAN_KES", 1000)
PAYSTACK_MAX_LOAN_KES = env_int("PAYSTACK_MAX_LOAN_KES", 60000)
APP_FEE_CURRENCY = env("APP_FEE_CURRENCY", "KES")

if PAYMENTS_ENABLED:
    if PAYMENT_PROVIDER == "paystack":
        missing = [
            name
            for name, value in (
                ("PAYSTACK_SECRET_KEY", PAYSTACK_SECRET_KEY),
                ("PAYSTACK_PUBLIC_KEY", PAYSTACK_PUBLIC_KEY),
            )
            if not value
        ]
        if missing:
            raise ImproperlyConfigured(
                "Payments are enabled but the following are missing: "
                + ", ".join(missing)
            )
        if not PAYMENTS_LIVE_MODE and PAYSTACK_SECRET_KEY.startswith("sk_live_"):
            raise ImproperlyConfigured(
                "PAYMENTS_LIVE_MODE is false but a live Paystack secret key is "
                "configured. Refusing to start: use test keys or explicitly "
                "enable live mode."
            )
        if PAYMENTS_LIVE_MODE and not PAYSTACK_SECRET_KEY.startswith("sk_live_"):
            raise ImproperlyConfigured(
                "PAYMENTS_LIVE_MODE is true but the Paystack secret key is not a "
                "live key."
            )
elif PAYMENT_PROVIDER == "paystack" and (PAYSTACK_SECRET_KEY or PAYSTACK_PUBLIC_KEY):
    raise ImproperlyConfigured(
        "Paystack keys are set while PAYMENTS_ENABLED is false. Remove the keys "
        "or enable payments to avoid ambiguous configuration."
    )

# =========================================================================
# FEES / PRODUCT SAFETY GATES
# =========================================================================
FEE_COLLECTION_MODE = (env("FEE_COLLECTION_MODE", "disabled") or "").lower()
if FEE_COLLECTION_MODE not in {"disabled", "upfront"}:
    raise ImproperlyConfigured(
        "FEE_COLLECTION_MODE must be 'disabled' or 'upfront'."
    )
FEE_COLLECTION_APPROVED = env_bool("FEE_COLLECTION_APPROVED", False)
if FEE_COLLECTION_MODE == "upfront" and not FEE_COLLECTION_APPROVED:
    raise ImproperlyConfigured(
        "FEE_COLLECTION_MODE='upfront' requires FEE_COLLECTION_APPROVED=true. "
        "Upfront fees must be approved by the business and legal owners first."
    )

REQUIRE_OFFER_ACCEPTANCE = env_bool("REQUIRE_OFFER_ACCEPTANCE", True)

# =========================================================================
# LEGAL / COMPLIANCE DISPLAY (never invent values here)
# =========================================================================
# Base path (or absolute URL) the browser uses for API calls. The bundled
# templates are served by this same Django process, so the default is a
# relative, versioned path: no hostname is compiled into the frontend and the
# app works behind any Render hostname, custom domain or preview URL.
#
#   API_BASE_URL=/api/v1                                (default, same origin)
#   API_BASE_URL=https://mb-api.onrender.com/api/v1     (split deployment)
#
# A split deployment also needs CORS_ALLOWED_ORIGINS set to the frontend
# origin on the API service; the two are documented together in docs/API.md.
API_BASE_URL = env("API_BASE_URL", "/api/v1")
API_BASE_URL = API_BASE_URL.rstrip("/") or "/api/v1"

# Single branding source: PLATFORM_NAME. The default matches the deployed
# brand in render.yaml and Backend/.env.example; every screen string reads
# this setting (never a hardcoded name) so re-branding is one environment
# variable, not a code change.
PLATFORM_NAME = env("PLATFORM_NAME", "PesaFix")
PLATFORM_TAGLINE = env(
    "PLATFORM_TAGLINE", "Clarity at every step of your financial journey."
)
INTERNAL_EMAIL_DOMAIN = env("INTERNAL_EMAIL_DOMAIN", "example.com")
PLATFORM_LEGAL_NAME = env("PLATFORM_LEGAL_NAME", "")
PLATFORM_LICENCE_STATUS = env(
    "PLATFORM_LICENCE_STATUS", "Licence status not yet published"
)
PLATFORM_LICENCE_NUMBER = env("PLATFORM_LICENCE_NUMBER", "")
PLATFORM_SUPPORT_PHONE = env("PLATFORM_SUPPORT_PHONE", "")
PLATFORM_SUPPORT_EMAIL = env("PLATFORM_SUPPORT_EMAIL", "")
PLATFORM_ADDRESS = env("PLATFORM_ADDRESS", "")

# M-Pesa collection shortcuts. Both are empty by default: fee collection runs
# through Paystack's prompt, and the interface must never invent a Paybill or
# Till number. Publish one only after the operating account is confirmed.
MPESA_PAYBILL_NUMBER = env("MPESA_PAYBILL_NUMBER", "")
MPESA_TILL_NUMBER = env("MPESA_TILL_NUMBER", "")
LEGAL_TERMS_APPROVED = env_bool("LEGAL_TERMS_APPROVED", False)
TERMS_VERSION = env("TERMS_VERSION", "2026-10-draft")
PRIVACY_VERSION = env("PRIVACY_VERSION", "2026-10-draft")
CONSENT_VERSION = env("CONSENT_VERSION", "2026-10-draft")

# Progressive Web App
PWA_THEME_COLOR = env("PWA_THEME_COLOR", "#071320")
PWA_BACKGROUND_COLOR = env("PWA_BACKGROUND_COLOR", "#F7F4EE")
# Cache-busting identity for the service worker's precache. Default: a
# deterministic fingerprint of the app shell itself (core/pwa.py), so a
# deploy that changes any cached asset automatically changes the version —
# no hand-bumped string. PWA_CACHE_VERSION remains as an explicit override
# for pipelines that prefer stamping the build id / commit SHA.
PWA_CACHE_VERSION = resolve_cache_version(env("PWA_CACHE_VERSION", ""), BASE_DIR / "Frontend")

if PAYMENTS_LIVE_MODE and not LEGAL_TERMS_APPROVED:
    raise ImproperlyConfigured(
        "PAYMENTS_LIVE_MODE requires LEGAL_TERMS_APPROVED=true. "
        "Do not issue live loans before legal review and licensing are complete."
    )

# =========================================================================
# NOTIFICATIONS
# =========================================================================
SMS_BACKEND = (env("SMS_BACKEND", "console" if DEBUG else "none") or "none").lower()
if SMS_BACKEND not in {"console", "africastalking", "none"}:
    raise ImproperlyConfigured("SMS_BACKEND must be console, africastalking or none.")
AFRICASTALKING_USERNAME = env("AFRICASTALKING_USERNAME", "")
AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", "")
AFRICASTALKING_SENDER_ID = env("AFRICASTALKING_SENDER_ID", "")
if SMS_BACKEND == "africastalking" and not (
    AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY
):
    raise ImproperlyConfigured(
        "SMS_BACKEND='africastalking' requires AFRICASTALKING_USERNAME and "
        "AFRICASTALKING_API_KEY."
    )

# Bounded retry of transient provider failures (in-request). Attempts total
# 1 + NOTIFICATION_MAX_RETRIES; permanent rejections never retry.
NOTIFICATION_MAX_RETRIES = env_int("NOTIFICATION_MAX_RETRIES", 1)
NOTIFICATION_RETRY_BACKOFF_SECONDS = env_float("NOTIFICATION_RETRY_BACKOFF_SECONDS", 0.5)

# Minimum gap between two OTP SMS for the same number, on top of the
# windowed limits in the domain layer. Prevents resend loops.
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)

# =========================================================================
# TIME / I18N
# =========================================================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

WSGI_APPLICATION = "core.wsgi.application"

# =========================================================================
# LOGGING
# =========================================================================
LOG_LEVEL = env("DJANGO_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.middleware.RequestIDLogFilter"},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# Disable Django's default test client host restriction issue in tests by
# allowing "testserver" only when explicitly configured by test settings.
