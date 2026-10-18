"""Consistent error envelope for every API response.

Shape::

    {"error": {"code": "...", "message": "...", "details": {...},
               "request_id": "..."}}

The frontend reads ``error.message``. Unexpected exceptions never leak stack
traces, SQL, or provider credentials.
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.errors import DomainError, InvalidTransition

logger = logging.getLogger(__name__)


def _request_id(context) -> str:
    request = context.get("request") if context else None
    return getattr(request, "request_id", "") or ""


def _flatten(detail) -> dict:
    if isinstance(detail, dict):
        return {key: _flatten(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return {"non_field_errors": [str(item) for item in detail]}
    return {"detail": str(detail)}


def _envelope(code: str, message: str, *, details=None, request_id: str = "") -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


def custom_exception_handler(exc, context):
    request_id = _request_id(context)

    if isinstance(exc, DomainError):
        return Response(
            _envelope(exc.code, exc.message, details=exc.details, request_id=request_id),
            status=exc.http_status,
        )

    if isinstance(exc, (Http404, DjangoPermissionDenied)):
        # Convert Django exceptions to DRF equivalents.
        exc = (
            drf_exceptions.NotFound()
            if isinstance(exc, Http404)
            else drf_exceptions.PermissionDenied()
        )

    response = drf_exception_handler(exc, context)

    if response is not None:
        detail = response.data
        code = "invalid_request"
        message = "Request could not be processed."
        details: dict = {}

        if isinstance(exc, drf_exceptions.ValidationError):
            code = "validation_error"
            message = "Please correct the highlighted fields."
            details = _flatten(detail)
        elif isinstance(exc, drf_exceptions.NotAuthenticated):
            code = "not_authenticated"
            message = "Authentication is required."
        elif isinstance(exc, drf_exceptions.AuthenticationFailed):
            code = "authentication_failed"
            message = str(getattr(exc, "detail", "Authentication failed."))
        elif isinstance(exc, drf_exceptions.PermissionDenied):
            code = "permission_denied"
            message = "You do not have permission to perform this action."
        elif isinstance(exc, drf_exceptions.Throttled):
            code = "rate_limited"
            message = "Too many requests. Please wait and try again."
            details = {"retry_after_seconds": exc.wait}
        elif isinstance(exc, drf_exceptions.NotFound):
            code = "not_found"
            message = "The requested resource was not found."
        elif isinstance(exc, drf_exceptions.MethodNotAllowed):
            code = "method_not_allowed"
            message = "This method is not allowed."
        elif isinstance(exc, InvalidTransition):
            code = exc.code
            message = exc.message
        elif isinstance(detail, dict) and "detail" in detail:
            message = str(detail["detail"])
        elif isinstance(detail, list) and detail:
            message = str(detail[0])

        response.data = _envelope(code, message, details=details, request_id=request_id)
        return response

    logger.exception("Unhandled API exception (request_id=%s)", request_id)
    return Response(
        _envelope(
            "server_error",
            "An unexpected error occurred. Please contact support with the request id.",
            request_id=request_id,
        ),
        status=500,
    )
