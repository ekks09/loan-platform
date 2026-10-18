"""Payment-provider webhook endpoint.

The configured provider (``PAYMENT_PROVIDER``) authenticates each delivery:
Paystack signs the raw request body with HMAC-SHA512 using the account's
secret key. The raw bytes are verified before parsing, every delivery is
persisted exactly once, and processing is idempotent so provider retries are
safe.

Authenticity is not fulfilment: ``charge.success`` is re-verified against the
provider API when ``PAYSTACK_VERIFY_ON_WEBHOOK`` is on, and every state change
still validates reference / amount / currency / expected state.

The endpoint answers 200 only when the event was processed or deliberately
ignored. Processing errors return 500 so the provider retries; the stored
event can also be replayed with ``manage.py replay_webhooks``.
"""
from __future__ import annotations

import hashlib
import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.models import WebhookEvent
from payments.providers import get_payment_provider
from payments.services import process_webhook_event, record_webhook_event

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 256 * 1024


@csrf_exempt
@require_POST
def paystack_webhook(request: HttpRequest):
    """Webhook receiver. The route name is kept for the already-configured
    provider URL; behaviour is provider-neutral through the adapter."""
    if not settings.PAYMENTS_ENABLED:
        return JsonResponse(
            {"status": "payments disabled"}, status=503
        )

    if len(request.body) > MAX_BODY_BYTES:
        logger.warning("Rejected oversized webhook body (%s bytes)", len(request.body))
        return JsonResponse({"status": "payload too large"}, status=413)

    provider = get_payment_provider()
    if not provider.verify_webhook(raw_body=request.body, headers=request.headers):
        logger.warning("Rejected webhook with invalid signature")
        return JsonResponse({"status": "invalid signature"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Rejected webhook with invalid JSON")
        return JsonResponse({"status": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"status": "invalid payload"}, status=400)

    envelope = provider.classify_webhook(payload)

    # Paystack does not send an event id; the body digest is stable across
    # retries of the same delivery and unique per event.
    event_id = hashlib.sha256(request.body).hexdigest()

    event, created = record_webhook_event(
        event_id=event_id,
        event_type=envelope.event_type,
        payload=payload,
        reference=envelope.reference,
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
            "Webhook processing failed for %s reference=%s",
            envelope.event_type,
            envelope.reference,
        )
        return JsonResponse({"status": "processing failed"}, status=500)

    return JsonResponse({"status": outcome})
