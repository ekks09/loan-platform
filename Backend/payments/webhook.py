from __future__ import annotations

import hmac
import hashlib
import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from loans.models import Loan
from .views import mark_transfer_event  # function defined in payments/views.py


def _valid_signature(request: HttpRequest) -> bool:
    signature = request.headers.get("X-Paystack-Signature", "")
    computed = hmac.new(
        key=settings.PAYSTACK_WEBHOOK_SECRET.encode("utf-8"),
        msg=request.body,
        digestmod=hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(signature, computed)


@csrf_exempt
def paystack_webhook(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    if not _valid_signature(request):
        return JsonResponse({"ok": False, "error": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    event = payload.get("event", "")
    data = payload.get("data", {}) or {}

    # Handle transfer events to keep loan state consistent.
    # Paystack transfer events commonly include:
    # - transfer.success
    # - transfer.failed
    # - transfer.reversed
    transfer_ref = data.get("reference") or ""
    if event.startswith("transfer.") and transfer_ref:
        with transaction.atomic():
            mark_transfer_event(event=event, transfer_reference=transfer_ref, raw=data)
        return JsonResponse({"ok": True})

    return JsonResponse({"ok": True})