"""Paystack adapter for the :class:`PaymentProvider` contract.

All Paystack-specific knowledge lives in this module:

* API paths, headers and authentication (secret key server-side only);
* request payload formats (minor-unit amounts, mobile-money channels);
* response parsing and the provider's status vocabulary;
* webhook authentication (HMAC-SHA512 over the raw body with the secret key)
  and raw event-type strings.

Design rules (unchanged from the proven HTTP client this file grew from):

* network calls never happen inside a database transaction;
* errors are classified as retryable / uncertain / permanent;
* provider response bodies are never logged wholesale (they can contain PII);
* an "accepted" response is not a settlement: callers must confirm.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Mapping, Optional, Sequence

import requests
from django.conf import settings

from payments.providers.base import (
    ChargeStatus,
    ChargeVerification,
    CheckoutSession,
    ProviderError,
    RefundOutcome,
    RefundStatus,
    TransferInitiation,
    TransferRecipient,
    TransferState,
    TransferStatus,
    WebhookEnvelope,
    WebhookEventKind,
)

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Paystack-Signature"


class PaystackClient:
    """Low-level HTTP client. Callers should prefer ``PaystackProvider``."""

    def __init__(self, *, timeout: int | None = None, max_retries: int | None = None):
        self.base_url = settings.PAYSTACK_BASE_URL.rstrip("/")
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.timeout = timeout or settings.PAYSTACK_REQUEST_TIMEOUT
        self.max_retries = (
            settings.PAYSTACK_MAX_RETRIES if max_retries is None else max_retries
        )
        if not self.secret_key:
            raise ProviderError("Paystack is not configured.", code="not_configured")

    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        retry: bool = True,
        idempotent: bool = True,
    ) -> dict:
        url = f"{self.base_url}{path}"
        attempts = 0
        last_error: ProviderError | None = None
        while attempts <= (self.max_retries if retry else 0):
            attempts += 1
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                logger.warning("Paystack timeout on %s %s", method, path)
                last_error = ProviderError(
                    "The payment provider did not respond in time.",
                    code="provider_timeout",
                    retryable=True,
                    uncertain=not idempotent,
                )
                if idempotent and attempts <= self.max_retries:
                    time.sleep(min(2 ** attempts, 5))
                    continue
                raise last_error from exc
            except requests.RequestException as exc:
                logger.warning("Paystack network error on %s %s", method, path)
                last_error = ProviderError(
                    "Could not reach the payment provider.",
                    code="provider_network_error",
                    retryable=True,
                )
                if attempts <= self.max_retries and idempotent:
                    time.sleep(min(2 ** attempts, 5))
                    continue
                raise last_error from exc

            if response.status_code >= 500 or response.status_code == 429:
                last_error = ProviderError(
                    "The payment provider is temporarily unavailable.",
                    code="provider_unavailable",
                    http_status=response.status_code,
                    retryable=True,
                    uncertain=not idempotent,
                )
                if attempts <= self.max_retries:
                    time.sleep(min(2 ** attempts, 5))
                    continue
                raise last_error

            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "The payment provider returned an unreadable response.",
                    code="provider_bad_response",
                    http_status=response.status_code,
                    retryable=False,
                ) from exc

            if response.status_code >= 400 or not body.get("status"):
                message = (body.get("message") or "Payment provider rejected the request.")[:300]
                raise ProviderError(
                    message,
                    code="provider_rejected",
                    http_status=response.status_code,
                    retryable=False,
                )

            return body.get("data") or {}

        raise last_error or ProviderError("Payment provider request failed.")

    # ------------------------------------------------------------------
    def initialize_transaction(
        self,
        *,
        email: str,
        amount_minor: int,
        reference: str,
        currency: str,
        metadata: dict,
        channels: list[str] | None = None,
    ) -> dict:
        if amount_minor <= 0:
            raise ProviderError("Amount must be positive.", code="invalid_amount")
        payload = {
            "email": email,
            "amount": int(amount_minor),
            "reference": reference,
            "currency": currency,
            "metadata": metadata,
        }
        if channels:
            # Restricting the channel is what turns checkout into a mobile
            # money prompt instead of a card form. Paystack only honours
            # channels the account has been enabled for; an unsupported value
            # is rejected by the provider rather than silently ignored.
            payload["channels"] = list(channels)
        return self._request(
            "POST",
            "/transaction/initialize",
            payload=payload,
            idempotent=True,
        )

    def verify_transaction(self, reference: str) -> dict:
        return self._request("GET", f"/transaction/verify/{reference}", idempotent=True)

    def create_transfer_recipient(self, *, name: str, phone_254: str) -> dict:
        return self._request(
            "POST",
            "/transferrecipient",
            payload={
                "type": "mobile_money",
                "name": name,
                "account_number": phone_254,
                "bank_code": "MPESA",
                "currency": "KES",
            },
            idempotent=False,
        )

    def initiate_transfer(
        self, *, amount_minor: int, recipient_code: str, reference: str, reason: str
    ) -> dict:
        return self._request(
            "POST",
            "/transfer",
            payload={
                "source": "balance",
                "amount": int(amount_minor),
                "recipient": recipient_code,
                "reference": reference,
                "reason": reason,
                "currency": "KES",
            },
            # A transfer is not idempotent on the client: a timeout may occur
            # after the provider accepted it. Do not blind-retry; reconcile.
            idempotent=False,
            retry=False,
        )

    def get_transfer(self, transfer_code: str) -> dict:
        return self._request("GET", f"/transfer/{transfer_code}", idempotent=True)

    def list_transfers(self, *, reference: str) -> list[dict]:
        data = self._request("GET", f"/transfer?reference={reference}", idempotent=True)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    def create_refund(
        self, *, transaction_reference: str, amount_minor: int, reason: str
    ) -> dict:
        return self._request(
            "POST",
            "/refund",
            payload={
                "transaction": transaction_reference,
                "amount": int(amount_minor),
                "reason": reason,
            },
            idempotent=False,
            retry=False,
        )


# -------------------------------------------------------------------------
# Status vocabulary mapping. Only this table may interpret provider strings.
# -------------------------------------------------------------------------
_CHARGE_STATUS = {
    "success": ChargeStatus.SUCCESS,
    "failed": ChargeStatus.FAILED,
    "reversed": ChargeStatus.FAILED,
    "abandoned": ChargeStatus.ABANDONED,
    "pending": ChargeStatus.PENDING,
    "ongoing": ChargeStatus.PENDING,
    "processing": ChargeStatus.PENDING,
    "queued": ChargeStatus.PENDING,
}

_TRANSFER_STATUS = {
    "success": TransferStatus.SUCCESS,
    "failed": TransferStatus.FAILED,
    "reversed": TransferStatus.REVERSED,
    # accepted but not settled: the service keeps awaiting confirmation
    "pending": TransferStatus.PENDING,
    "processing": TransferStatus.PENDING,
    "queued": TransferStatus.PENDING,
    "otp": TransferStatus.PENDING,
}

_REFUND_STATUS = {
    "processed": RefundStatus.SETTLED,
    "success": RefundStatus.SETTLED,
    "completed": RefundStatus.SETTLED,
    "failed": RefundStatus.FAILED,
    "pending": RefundStatus.PENDING,
    "processing": RefundStatus.PENDING,
}

_EVENT_KINDS = {
    "charge.success": WebhookEventKind.CHARGE_SUCCESS,
    "transfer.success": WebhookEventKind.TRANSFER_SUCCESS,
    "transfer.failed": WebhookEventKind.TRANSFER_FAILED,
    "transfer.reversed": WebhookEventKind.TRANSFER_REVERSED,
    "refund.processed": WebhookEventKind.REFUND_PROCESSED,
    "refund.failed": WebhookEventKind.REFUND_FAILED,
}


class PaystackProvider:
    """Normalize the Paystack API onto the provider contract."""

    name = "paystack"

    def __init__(self, *, timeout: int | None = None, max_retries: int | None = None):
        self._client = PaystackClient(timeout=timeout, max_retries=max_retries)

    # static, so normalization can be exercised without credentials
    @staticmethod
    def normalize_charge(payload: Mapping[str, Any]) -> ChargeVerification:
        raw_status = str(payload.get("status", "")).lower()
        status_value = _CHARGE_STATUS.get(raw_status, ChargeStatus.PENDING)
        try:
            amount_minor = int(payload.get("amount", 0))
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "The provider returned an invalid amount.",
                code="provider_bad_response",
            ) from exc
        gateway_response = payload.get("gateway_response") or payload.get("status") or ""
        return ChargeVerification(
            status=status_value,
            reference=str(payload.get("reference", "")),
            amount_minor=amount_minor,
            currency=str(payload.get("currency", "")).upper(),
            channel=str(payload.get("channel", "") or ""),
            transaction_id=str(payload.get("id", "") or ""),
            gateway_response=str(gateway_response),
        )

    # -- inbound charges -----------------------------------------------------
    def initialize(
        self,
        *,
        email: str,
        amount_minor: int,
        reference: str,
        currency: str,
        metadata: Mapping[str, Any],
        channels: Optional[Sequence[str]] = None,
    ) -> CheckoutSession:
        data = self._client.initialize_transaction(
            email=email,
            amount_minor=amount_minor,
            reference=reference,
            currency=currency,
            metadata=dict(metadata),
            channels=list(channels) if channels else None,
        )
        authorization_url = str(data.get("authorization_url") or "")
        if not authorization_url:
            raise ProviderError(
                "The provider did not return a checkout URL.",
                code="provider_bad_response",
            )
        return CheckoutSession(
            authorization_url=authorization_url,
            access_code=str(data.get("access_code") or ""),
            provider_reference=str(data.get("reference") or reference),
        )

    def verify(self, reference: str) -> ChargeVerification:
        return self.normalize_charge(self._client.verify_transaction(reference))

    # -- outbound transfers ---------------------------------------------------
    def create_transfer_recipient(self, *, name: str, phone: str) -> TransferRecipient:
        data = self._client.create_transfer_recipient(name=name, phone_254=phone)
        return TransferRecipient(recipient_code=str(data.get("recipient_code") or ""))

    def initiate_transfer(
        self,
        *,
        amount_minor: int,
        recipient_code: str,
        reference: str,
        reason: str,
    ) -> TransferInitiation:
        data = self._client.initiate_transfer(
            amount_minor=amount_minor,
            recipient_code=recipient_code,
            reference=reference,
            reason=reason,
        )
        return TransferInitiation(
            transfer_code=str(data.get("transfer_code") or ""),
            provider_reference=str(data.get("reference") or reference),
            raw=data if isinstance(data, Mapping) else {},
        )

    @staticmethod
    def _transfer_state(data: Mapping[str, Any]) -> TransferState:
        raw_status = str(data.get("status", "")).lower()
        status_value = _TRANSFER_STATUS.get(raw_status, TransferStatus.PENDING)
        return TransferState(
            status=status_value,
            reason=str(data.get("reason") or data.get("message") or ""),
            raw=data,
        )

    def get_transfer(self, transfer_code: str) -> TransferState:
        return self._transfer_state(self._client.get_transfer(transfer_code))

    def find_transfer_by_reference(self, reference: str) -> TransferState | None:
        matches = self._client.list_transfers(reference=reference)
        if not matches:
            return None
        return self._transfer_state(matches[0])

    # -- refunds ----------------------------------------------------------------
    def refund(
        self,
        *,
        transaction_reference: str,
        amount_minor: int,
        reason: str,
    ) -> RefundOutcome:
        data = self._client.create_refund(
            transaction_reference=transaction_reference,
            amount_minor=amount_minor,
            reason=reason,
        )
        raw_status = str(data.get("status", "")).lower()
        status_value = _REFUND_STATUS.get(raw_status, RefundStatus.PENDING)
        return RefundOutcome(
            status=status_value,
            refund_id=str(data.get("id") or ""),
            raw=data if isinstance(data, Mapping) else {},
        )

    # -- webhooks -----------------------------------------------------------------
    def verify_webhook(self, *, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Authenticate the delivery with HMAC-SHA512 over the raw body.

        Paystack signs with the account **secret key** and sends the hex digest
        in ``X-Paystack-Signature``. Comparison is constant-time.
        """
        if not settings.PAYSTACK_SECRET_KEY:
            return False
        signature = ""
        for key, value in headers.items():
            if key.lower() == SIGNATURE_HEADER.lower():
                signature = str(value)
                break
        if not signature:
            return False
        digest = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(digest, signature.strip())

    def classify_webhook(self, payload: Mapping[str, Any]) -> WebhookEnvelope:
        event_type = str(payload.get("event", ""))[:64]
        data = payload.get("data")
        if not isinstance(data, Mapping):
            data = {}
        reference = str(data.get("reference", "") or "")
        return WebhookEnvelope(
            kind=_EVENT_KINDS.get(event_type, WebhookEventKind.UNSUPPORTED),
            event_type=event_type,
            reference=reference,
            data=data,
        )
