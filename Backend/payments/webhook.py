"""Paystack webhook endpoint.

Paystack signs the raw request body with HMAC-SHA512 using the account's
**secret key** and sends the hex digest in ``X-Paystack-Signature``. The raw
bytes are verified before parsing, every delivery is persisted exactly once,
and processing is idempotent so provider retries are safe.

The endpoint answers 200 only when the event was processed or deliberately
ignored. Processing errors return 500 so Paystack retries; the stored event can
also be replayed with ``manage.py replay_webhooks``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.models import WebhookEvent
from payments.services import process_webhook_event, record_webhook_event

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 256 * 1024


def _signature_is_valid(request: HttpRequest) -> bool:
    if not settings.PAYSTACK_SECRET_KEY:
        return False
    signature = request.headers.get("X-Paystack-Signature", "")
    if not signature:
        return False
    digest = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        msg=request.body,
        digestmod=hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


@csrf_exempt
@require_POST
def paystack_webhook(request: HttpRequest):
    if not settings.PAYMENTS_ENABLED:
        return JsonResponse(
            {"status": "payments disabled"}, status=503
        )

    if len(request.body) > MAX_BODY_BYTES:
        logger.warning("Rejected oversized webhook body (%s bytes)", len(request.body))
        return JsonResponse({"status": "payload too large"}, status=413)

    if not _signature_is_valid(request):
        logger.warning("Rejected webhook with invalid signature")
        return JsonResponse({"status": "invalid signature"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Rejected webhook with invalid JSON")
        return JsonResponse({"status": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"status": "invalid payload"}, status=400)

    event_type = str(payload.get("event", ""))[:64]
    data = payload.get("data") or {}
    reference = str(data.get("reference", "")) if isinstance(data, dict) else ""

    # Paystack does not send an event id; the body digest is stable across
    # retries of the same delivery and unique per event.
    event_id = hashlib.sha256(request.body).hexdigest()

    event, created = record_webhook_event(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        reference=reference,
    )
    if not created and event.status in {
        WebhookEvent.Status.PROCESSED,
        WebhookEvent.Status.IGNORED,
    }:
        logger.info("Duplicate webhook delivery %s ignored", event.event_type)
        return JsonResponse({"status": "duplicate"})

    try:
        outcome = process_webhook_event(event)
    except Exception:
        logger.exception(
            "Webhook processing failed for %s reference=%s", event_type, reference
        )
        return JsonResponse({"status": "processing failed"}, status=500)

    return JsonResponse({"status": outcome})
