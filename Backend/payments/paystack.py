from __future__ import annotations

import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class PaystackError(RuntimeError):
    """Custom exception for Paystack API errors."""
    pass


class PaystackClient:
    def __init__(self) -> None:
        self.base_url = getattr(settings, 'PAYSTACK_BASE_URL', 'https://api.paystack.co')
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        
        if not self.secret_key:
            logger.warning("PAYSTACK_SECRET_KEY not configured")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    def initialize_transaction(
        self,
        email: str,
        amount_kobo: int,
        reference: str,
        currency: str,
        metadata: dict
    ) -> dict:
        """Initialize a Paystack transaction."""
        url = f"{self.base_url}/transaction/initialize"
        payload = {
            "email": email,
            "amount": int(amount_kobo),
            "reference": reference,
            "currency": currency,
            "metadata": metadata,
        }
        
        logger.info(f"Initializing Paystack transaction: {reference}")
        
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
            data = r.json()
            
            if r.status_code >= 400 or not data.get("status"):
                error_msg = data.get("message") or f"Paystack initialize failed (HTTP {r.status_code})"
                logger.error(f"Paystack init error: {error_msg}")
                raise PaystackError(error_msg)
            
            logger.info(f"Paystack transaction initialized successfully: {reference}")
            return data["data"]
            
        except requests.exceptions.RequestException as e:
            logger.exception(f"Paystack request error: {e}")
            raise PaystackError(f"Network error: {str(e)}")

    def verify_transaction(self, reference: str) -> dict:
        """Verify a Paystack transaction."""
        url = f"{self.base_url}/transaction/verify/{reference}"
        
        logger.info(f"Verifying Paystack transaction: {reference}")
        
        try:
            r = requests.get(url, headers=self._headers(), timeout=30)
            data = r.json()
            
            if r.status_code >= 400 or not data.get("status"):
                error_msg = data.get("message") or f"Paystack verify failed (HTTP {r.status_code})"
                logger.error(f"Paystack verify error: {error_msg}")
                raise PaystackError(error_msg)
            
            logger.info(f"Paystack transaction verified: {reference}")
            return data["data"]
            
        except requests.exceptions.RequestException as e:
            logger.exception(f"Paystack request error: {e}")
            raise PaystackError(f"Network error: {str(e)}")

    def create_transfer_recipient(self, name: str, phone_254: str) -> dict:
        """Create a Paystack Transfer recipient for Mobile Money in Kenya."""
        url = f"{self.base_url}/transferrecipient"
        payload = {
            "type": "mobile_money",
            "name": name,
            "account_number": phone_254,
            "bank_code": "MPESA",
            "currency": "KES",
        }
        
        logger.info(f"Creating transfer recipient for: {phone_254}")
        
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
            data = r.json()
            
            if r.status_code >= 400 or not data.get("status"):
                error_msg = data.get("message") or f"Create recipient failed (HTTP {r.status_code})"
                logger.error(f"Paystack recipient error: {error_msg}")
                raise PaystackError(error_msg)
            
            logger.info(f"Transfer recipient created: {data['data'].get('recipient_code')}")
            return data["data"]
            
        except requests.exceptions.RequestException as e:
            logger.exception(f"Paystack request error: {e}")
            raise PaystackError(f"Network error: {str(e)}")

    def initiate_transfer(
        self,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str
    ) -> dict:
        """Initiate a transfer to a recipient."""
        url = f"{self.base_url}/transfer"
        payload = {
            "source": "balance",
            "amount": int(amount_kobo),
            "recipient": recipient_code,
            "reference": reference,
            "reason": reason,
            "currency": "KES",
        }
        
        logger.info(f"Initiating transfer: {reference}")
        
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
            data = r.json()
            
            if r.status_code >= 400 or not data.get("status"):
                error_msg = data.get("message") or f"Transfer initiation failed (HTTP {r.status_code})"
                logger.error(f"Paystack transfer error: {error_msg}")
                raise PaystackError(error_msg)
            
            logger.info(f"Transfer initiated successfully: {reference}")
            return data["data"]
            
        except requests.exceptions.RequestException as e:
            logger.exception(f"Paystack request error: {e}")
            raise PaystackError(f"Network error: {str(e)}")
