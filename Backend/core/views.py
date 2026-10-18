"""Public pages, PWA assets and health endpoints."""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from loans.models import Loan

logger = logging.getLogger(__name__)

SERVICE_WORKER_PATH = Path(settings.BASE_DIR) / "Frontend" / "sw.js"


def _pricing_rows():
    return Loan.pricing_rows()


# The homepage calculator opens on a representative amount. These values are
# rendered by Django so the card is readable before the script runs; the
# script then replaces them with live quotes.
CALCULATOR_START = 10_000


def _band_for(amount: int):
    for band in Loan.FEE_RATE_BANDS:
        if band[0] <= amount <= band[1]:
            return band
    return None


def _page_context() -> dict:
    band = _band_for(CALCULATOR_START)
    return {
        "pricing_rows": _pricing_rows(),
        "min_amount": Loan.MIN_AMOUNT,
        "max_amount": Loan.MAX_AMOUNT,
        "fee_minimum": Loan.FEE_MINIMUM,
        "fee_schedule_version": Loan.FEE_SCHEDULE_VERSION,
        "initial_amount": CALCULATOR_START,
        "initial_fee": Loan.compute_service_fee(CALCULATOR_START),
        "initial_rate": Loan.fee_rate_percent(CALCULATOR_START),
        "initial_band_min": band[0] if band else Loan.MIN_AMOUNT,
        "initial_band_max": band[1] if band else Loan.MAX_AMOUNT,
    }


def index(request):
    return render(request, "index.html", _page_context())


def register(request):
    return render(request, "register.html", _page_context())


def login_view(request):
    return render(request, "login.html", _page_context())


def dashboard(request):
    return render(request, "dashboard.html", _page_context())


def server_error(request, template_name="500.html"):
    """Render the branded 500 page with the shared shell context.

    Django's default handler renders the template without context processors,
    which leaves the shell without the platform name or the support details.
    The fee context is best-effort: a database failure must not turn one error
    page into a second failure.
    """
    try:
        context = _page_context()
    except Exception:  # pragma: no cover - only during an infrastructure fault
        context = {
            "min_amount": Loan.MIN_AMOUNT,
            "max_amount": Loan.MAX_AMOUNT,
            "fee_minimum": Loan.FEE_MINIMUM,
            "fee_schedule_version": Loan.FEE_SCHEDULE_VERSION,
        }
    try:
        return render(request, template_name, context, status=500)
    except Exception:  # pragma: no cover - last-resort fallback
        logger.exception("Could not render the 500 page")
        return HttpResponseServerError(
            "Something went wrong. The request could not be completed. "
            "Nothing was charged; check your dashboard before retrying."
        )


def privacy(request):
    return render(request, "privacy.html", _page_context())


def terms(request):
    return render(request, "terms.html", _page_context())


def offline(request):
    """Static offline fallback precached by the service worker."""
    return render(request, "offline.html")


@require_GET
def manifest(request):
    """Web app manifest.

    Paths are absolute so the manifest stays valid from any route. Icons are
    real PNG files under /static/icons/ and include a maskable variant.
    """
    data = {
        "id": "/",
        "name": settings.PLATFORM_NAME,
        "short_name": settings.PLATFORM_NAME,
        "description": (
            "Apply for a Kenyan shilling loan, follow each verification step "
            "and see provider-confirmed updates in one account."
        ),
        "lang": "en-KE",
        "dir": "ltr",
        "start_url": "/dashboard/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "theme_color": settings.PWA_THEME_COLOR,
        "background_color": settings.PWA_BACKGROUND_COLOR,
        "categories": ["finance", "business"],
        "icons": [
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": "Your dashboard",
                "short_name": "Dashboard",
                "url": "/dashboard/",
                "icons": [
                    {
                        "src": "/static/icons/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                    }
                ],
            }
        ],
    }
    response = JsonResponse(data, content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_GET
def service_worker(request):
    """Serve the service worker at the root so its scope is the whole app."""
    try:
        source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Service worker file is missing")
        return HttpResponse(
            "/* service worker unavailable */",
            content_type="application/javascript",
            status=500,
        )
    source = source.replace("__CACHE_VERSION__", settings.PWA_CACHE_VERSION)
    response = HttpResponse(source, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_GET
def robots(request):
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Allow: /legal/",
            "Disallow: /api/",
            "Disallow: /admin/",
            "Disallow: /dashboard/",
            "Disallow: /health/",
            "",
        ]
    )
    return HttpResponse(body, content_type="text/plain")


@require_GET
@never_cache
def health(request):
    """Liveness: the process is up and serving requests."""
    return JsonResponse(
        {
            "status": "ok",
            "service": "loan-platform",
            "product": settings.PLATFORM_NAME,
            "environment": "live" if settings.PAYMENTS_LIVE_MODE else "test",
        }
    )


@require_GET
@never_cache
def readiness(request):
    """Readiness: critical dependencies and safety gates.

    Returns 503 when a critical dependency is unavailable so a load balancer
    or uptime monitor can distinguish "running" from "able to serve traffic".
    """
    checks: dict[str, str] = {}
    ready = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:
        logger.exception("Readiness: database check failed")
        checks["database"] = "failed"
        ready = False

    checks["payments"] = "enabled" if settings.PAYMENTS_ENABLED else "disabled"
    if settings.PAYMENTS_ENABLED:
        checks["payments_mode"] = "live" if settings.PAYMENTS_LIVE_MODE else "test"
        if settings.PAYMENTS_LIVE_MODE and not settings.LEGAL_TERMS_APPROVED:
            checks["legal_terms"] = "live_blocked"
            ready = False
        else:
            checks["legal_terms"] = (
                "approved" if settings.LEGAL_TERMS_APPROVED else "draft"
            )

    storage_ok = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)
    checks["document_storage"] = "configured" if storage_ok else "missing"
    if settings.REQUIRE_DOCUMENT_STORAGE and not storage_ok:
        ready = False

    checks["fee_collection"] = settings.FEE_COLLECTION_MODE
    checks["sms"] = settings.SMS_BACKEND

    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )
