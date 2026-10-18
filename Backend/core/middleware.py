"""Request correlation middleware and log filter."""
from __future__ import annotations

import contextvars
import logging
import re
import uuid

from django.conf import settings

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"
_VALID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def current_request_id() -> str:
    return request_id_var.get()


class RequestIDMiddleware:
    """Attach a correlation id to every request and response.

    Incoming ids are accepted only when they look like an id we could have
    generated; arbitrary header content is never reflected back.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID.match(incoming or "") else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.request_id = request_id
        try:
            response = self.get_response(request)
            response[RESPONSE_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


class RequestIDLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class SecurityHeadersMiddleware:
    """Conservative browser hardening for rendered pages.

    The Django admin is excluded because it ships inline scripts and styles.
    Scripts on our own pages are external files, so no ``unsafe-inline`` is
    allowed for scripts. Styles need inline allowances only for runtime
    progress widths.
    """

    CSP = (
        "default-src 'self'; "
        "script-src 'self' https://js.paystack.co; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' https://api.paystack.co https://checkout.paystack.com; "
        "frame-src https://*.paystack.com; "
        "worker-src 'self'; "
        "manifest-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/admin/"):
            return response
        content_type = response.get("Content-Type", "")
        if not content_type.startswith("text/html"):
            return response
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.setdefault(
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
        )

        if getattr(settings, "PREVIEW_EMBED_ALLOWED", False):
            # The sandbox preview frames the app from the proxy origin. Remove
            # the two framing blocks for that environment only; the flag is
            # off by default and never enabled for a deployment.
            if "X-Frame-Options" in response:
                del response["X-Frame-Options"]
            response["Content-Security-Policy"] = self.CSP.replace(
                "frame-ancestors 'none'", "frame-ancestors *"
            )
        return response
