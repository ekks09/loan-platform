from __future__ import annotations

import requests
from django.conf import settings


class PaystackError(RuntimeError):
    pass


class PaystackClient:
    def __init__(self) -> None:
        self.base_url = settings.PAYSTACK_BASE_URL
        self.secret_key = settings.PAYSTACK_SECRET_KEY

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    def initialize_transaction(self, email: str, amount_kobo: int, reference: str, currency: str, metadata: dict) -> dict:
        url = f"{self.base_url}/transaction/initialize"
        payload = {
            "email": email,
            "amount": int(amount_kobo),
            "reference": reference,
            "currency": currency,
            "metadata": metadata,
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        data = r.json()
        if r.status_code >= 400 or not data.get("status"):
            raise PaystackError(data.get("message") or "Paystack initialize failed")
        return data["data"]

    def verify_transaction(self, reference: str) -> dict:
        url = f"{self.base_url}/transaction/verify/{reference}"
        r = requests.get(url, headers=self._headers(), timeout=30)
        data = r.json()
        if r.status_code >= 400 or not data.get("status"):
            raise PaystackError(data.get("message") or "Paystack verify failed")
        return data["data"]

    def create_transfer_recipient(self, name: str, phone_254: str) -> dict:
        # Paystack Transfer recipient for Mobile Money in Kenya.
        # Uses "mobile_money" type per Paystack docs.
        url = f"{self.base_url}/transferrecipient"
        payload = {
            "type": "mobile_money",
            "name": name,
            "account_number": phone_254,   # mobile number in international format
            "bank_code": "MPESA",          # Paystack Kenya mobile money code
            "currency": "KES",
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        data = r.json()
        if r.status_code >= 400 or not data.get("status"):
            raise PaystackError(data.get("message") or "Create recipient failed")
        return data["data"]

    def initiate_transfer(self, amount_kobo: int, recipient_code: str, reference: str, reason: str) -> dict:
        url = f"{self.base_url}/transfer"
        payload = {
            "source": "balance",
            "amount": int(amount_kobo),
            "recipient": recipient_code,
            "reference": reference,
            "reason": reason,
            "currency": "KES",
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        data = r.json()
        if r.status_code >= 400 or not data.get("status"):
            raise PaystackError(data.get("message") or "Transfer initiation failed")
        return data["data"]