"""Paystack HTTP client.

Design rules:
* network calls never happen inside a database transaction;
* errors are classified as retryable / uncertain / permanent;
* provider response bodies are never logged wholesale (they can contain PII);
* an "accepted" response is not a settlement: callers must confirm.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class ProviderError(Exception):
    message: str
    code: str = "provider_error"
    http_status: int | None = None
    retryable: bool = False
    uncertain: bool = False

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class PaystackClient:
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
