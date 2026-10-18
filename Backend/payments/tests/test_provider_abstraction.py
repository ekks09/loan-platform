"""Contract and configuration tests for the payment-provider abstraction.

These tests pin the isolation rules:

* business services resolve the provider through the registry, and the
  registry fails closed on bad configuration;
* only the adapter parses provider payloads/statuses;
* the webhook signer authenticates raw bodies with the documented algorithm;
* the old import locations keep working (backward compatibility);
* the frontend ships no provider SDK and the init response stays contract
  compatible.

No test performs network access: HTTP is mocked at the adapter boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from core.errors import PaymentsDisabled
from core.tests.helpers import (
    DictMock,
    auth_client,
    make_loan,
    make_verified_user,
    patched_provider,
)
from payments.providers import (
    ChargeStatus,
    PaymentProvider,
    ProviderError,
    RefundStatus,
    TransferStatus,
    WebhookEventKind,
    get_payment_provider,
)
from payments.providers.paystack import PaystackProvider
from payments.services import get_or_create_fee_payment


class RegistrySelectionTests(TestCase):
    def test_default_provider_is_paystack(self):
        self.assertEqual(settings.PAYMENT_PROVIDER, "paystack")
        provider = get_payment_provider()
        self.assertEqual(provider.name, "paystack")
        self.assertIsInstance(provider, PaymentProvider)

    @override_settings(PAYMENTS_ENABLED=False)
    def test_disabled_payments_fail_closed(self):
        with self.assertRaises(PaymentsDisabled):
            get_payment_provider()

    @override_settings(PAYMENT_PROVIDER="not-a-provider")
    def test_unknown_provider_is_a_configuration_error(self):
        # settings.py rejects this at boot; the registry is defence in depth.
        with self.assertRaises(ImproperlyConfigured):
            get_payment_provider()

    def test_backward_compatible_imports(self):
        from payments.paystack import PaystackClient, ProviderError as OldError

        from payments.providers.base import ProviderError as NewError

        self.assertIs(OldError, NewError)
        self.assertTrue(callable(PaystackClient))

    def test_boot_fails_closed_on_an_unknown_provider(self):
        """Startup validation: an unsupported PAYMENT_PROVIDER aborts boot."""
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        env["PAYMENT_PROVIDER"] = "bogus"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os;"
                "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.test_settings');"
                "import django;"
                "django.setup()",
            ],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PAYMENT_PROVIDER", result.stderr)


class AdapterContractTests(TestCase):
    """PaystackProvider satisfies the protocol with the client mocked out."""

    def setUp(self):
        self.provider = PaystackProvider.__new__(PaystackProvider)
        self.provider._client = MagicMock()

    def test_initialize_maps_checkout_fields(self):
        self.provider._client.initialize_transaction.return_value = {
            "authorization_url": "https://checkout.paystack.test/x",
            "access_code": "ACC",
            "reference": "LPF_1",
        }
        checkout = self.provider.initialize(
            email="e@example.com",
            amount_minor=1500_00,
            reference="LPF_1",
            currency="KES",
            metadata={"loan_id": 1},
            channels=["mobile_money"],
        )
        self.assertEqual(checkout.authorization_url, "https://checkout.paystack.test/x")
        self.assertEqual(checkout.access_code, "ACC")
        self.assertEqual(checkout.provider_reference, "LPF_1")

    def test_initialize_without_url_is_a_provider_error(self):
        self.provider._client.initialize_transaction.return_value = {}
        with self.assertRaises(ProviderError):
            self.provider.initialize(
                email="e@example.com",
                amount_minor=100,
                reference="LPF_2",
                currency="KES",
                metadata={},
            )

    def test_verify_normalizes_success(self):
        self.provider._client.verify_transaction.return_value = DictMock.charge(
            "LPF_9", 1500
        )
        verification = self.provider.verify("LPF_9")
        self.assertIs(verification.status, ChargeStatus.SUCCESS)
        self.assertEqual(verification.amount_minor, 1500_00)
        self.assertEqual(verification.currency, "KES")
        self.assertEqual(verification.transaction_id, "12345")

    def test_charge_status_vocabulary_is_normalized(self):
        cases = {
            "success": ChargeStatus.SUCCESS,
            "failed": ChargeStatus.FAILED,
            "reversed": ChargeStatus.FAILED,
            "abandoned": ChargeStatus.ABANDONED,
            "pending": ChargeStatus.PENDING,
            "ongoing": ChargeStatus.PENDING,
            "processing": ChargeStatus.PENDING,
            "queued": ChargeStatus.PENDING,
            "something-new": ChargeStatus.PENDING,  # unknown never upgrades
        }
        for raw, expected in cases.items():
            verification = PaystackProvider.normalize_charge(
                {"status": raw, "reference": "R", "amount": 100, "currency": "KES"}
            )
            self.assertIs(
                verification.status, expected, f"status {raw!r} must map to {expected}"
            )
        # An unknown status must never be treated as success.
        verification = PaystackProvider.normalize_charge(
            {"status": "SUcCeSs", "reference": "R", "amount": 100, "currency": "KES"}
        )
        self.assertIs(verification.status, ChargeStatus.SUCCESS)

    def test_charge_with_invalid_amount_is_a_provider_error(self):
        with self.assertRaises(ProviderError):
            PaystackProvider.normalize_charge(
                {"status": "success", "reference": "R", "amount": "not-a-number"}
            )

    def test_transfer_status_vocabulary_is_normalized(self):
        provider = self.provider
        for raw, expected in {
            "success": TransferStatus.SUCCESS,
            "failed": TransferStatus.FAILED,
            "reversed": TransferStatus.REVERSED,
            "pending": TransferStatus.PENDING,
            "otp": TransferStatus.PENDING,
            "processing": TransferStatus.PENDING,
            "queued": TransferStatus.PENDING,
        }.items():
            provider._client.get_transfer.return_value = {"status": raw}
            state = provider.get_transfer("TRF_x")
            self.assertIs(state.status, expected, raw)

    def test_find_transfer_by_reference_returns_none_without_matches(self):
        self.provider._client.list_transfers.return_value = []
        self.assertIsNone(self.provider.find_transfer_by_reference("LNT_1"))
        self.provider._client.list_transfers.return_value = [
            {"status": "success", "reference": "LNT_1"}
        ]
        state = self.provider.find_transfer_by_reference("LNT_1")
        self.assertIsNotNone(state)
        self.assertIs(state.status, TransferStatus.SUCCESS)

    def test_refund_status_vocabulary_is_normalized(self):
        for raw, expected in {
            "processed": RefundStatus.SETTLED,
            "success": RefundStatus.SETTLED,
            "completed": RefundStatus.SETTLED,
            "failed": RefundStatus.FAILED,
            "pending": RefundStatus.PENDING,
            "processing": RefundStatus.PENDING,
        }.items():
            self.provider._client.create_refund.return_value = {
                "id": 7,
                "status": raw,
            }
            outcome = self.provider.refund(
                transaction_reference="LPF_1", amount_minor=100, reason="r"
            )
            self.assertIs(outcome.status, expected, raw)
            self.assertEqual(outcome.refund_id, "7")

    def test_webhook_signature_uses_raw_body_hmac_sha512(self):
        body = json.dumps({"event": "charge.success", "data": {}}).encode()
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
        ).hexdigest()
        provider = PaystackProvider.__new__(PaystackProvider)
        self.assertTrue(
            provider.verify_webhook(
                raw_body=body, headers={"X-Paystack-Signature": signature}
            )
        )
        # Header lookup is case-insensitive.
        self.assertTrue(
            provider.verify_webhook(
                raw_body=body, headers={"x-paystack-signature": signature}
            )
        )
        self.assertFalse(
            provider.verify_webhook(
                raw_body=body, headers={"X-Paystack-Signature": "0" * 128}
            )
        )
        self.assertFalse(provider.verify_webhook(raw_body=body, headers={}))
        # Signing a *modified* body must fail even with a valid-looking header.
        self.assertFalse(
            provider.verify_webhook(
                raw_body=body + b" ", headers={"X-Paystack-Signature": signature}
            )
        )

    @override_settings(PAYSTACK_SECRET_KEY="")
    def test_webhook_verification_fails_closed_without_a_secret(self):
        provider = PaystackProvider.__new__(PaystackProvider)
        self.assertFalse(
            provider.verify_webhook(
                raw_body=b"{}", headers={"X-Paystack-Signature": "abc"}
            )
        )

    def test_webhook_classification_normalizes_event_kinds(self):
        provider = PaystackProvider.__new__(PaystackProvider)
        cases = {
            "charge.success": WebhookEventKind.CHARGE_SUCCESS,
            "transfer.success": WebhookEventKind.TRANSFER_SUCCESS,
            "transfer.failed": WebhookEventKind.TRANSFER_FAILED,
            "transfer.reversed": WebhookEventKind.TRANSFER_REVERSED,
            "refund.processed": WebhookEventKind.REFUND_PROCESSED,
            "refund.failed": WebhookEventKind.REFUND_FAILED,
            "customeridentification.success": WebhookEventKind.UNSUPPORTED,
            "": WebhookEventKind.UNSUPPORTED,
        }
        for raw, expected in cases.items():
            envelope = provider.classify_webhook(
                {"event": raw, "data": {"reference": "R1"}}
            )
            self.assertIs(envelope.kind, expected, raw)
            self.assertEqual(envelope.reference, "R1")
        # Missing/invalid data must not raise.
        envelope = provider.classify_webhook({"event": "charge.success"})
        self.assertIs(envelope.kind, WebhookEventKind.CHARGE_SUCCESS)
        self.assertEqual(envelope.reference, "")


class ServiceLayerIsolationTests(TestCase):
    """Services store normalized values and tag rows with the provider name."""

    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)

    def test_fee_payment_records_the_configured_provider(self):
        payment = get_or_create_fee_payment(self.loan)
        self.assertEqual(payment.provider, "paystack")

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_initialize_stores_normalized_checkout_fields(self):
        with patched_provider(
            initialize_transaction={
                "authorization_url": "https://checkout.paystack.test/abc",
                "access_code": "ACCESS_ABC",
                "reference": "LPF_provider_ref",
            }
        ):
            from payments.services import initialize_fee_payment

            payment = initialize_fee_payment(self.loan, actor=self.user)
        self.assertEqual(payment.authorization_url, "https://checkout.paystack.test/abc")
        self.assertEqual(payment.provider_reference, "LPF_provider_ref")


class ApiContractTests(TestCase):
    """The init response keeps its public contract and gains neutral fields."""

    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_init_response_includes_neutral_authorization_url_and_provider(self):
        with patched_provider(
            initialize_transaction={
                "authorization_url": "https://checkout.paystack.test/abc",
                "access_code": "ACCESS_ABC",
            }
        ):
            response = auth_client(self.user).post(
                "/api/payments/init/", {"loan_id": self.loan.pk}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["authorization_url"], "https://checkout.paystack.test/abc")
        self.assertEqual(body["provider"], "paystack")
        # Compatibility fields retained for this release (deprecated).
        self.assertIn("paystack_public_key", body)
        self.assertIn("access_code", body)
        # The secret key must never appear in any part of the response.
        self.assertNotIn(settings.PAYSTACK_SECRET_KEY, response.content.decode())


class FrontendDecouplingTests(SimpleTestCase):
    """The shipped frontend must not depend on provider-specific client code."""

    def test_no_paystack_sdk_references_in_frontend_sources(self):
        import pathlib

        frontend = pathlib.Path(settings.BASE_DIR) / "Frontend"
        offenders = []
        for path in list(frontend.rglob("*.js")) + list(frontend.rglob("*.html")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in ("js.paystack.co", "PaystackPop", "paystack.js"):
                if marker in text:
                    offenders.append(f"{path.relative_to(frontend)}: {marker}")
        self.assertEqual(offenders, [])

    def test_deleted_sdk_wrapper_is_not_precached(self):
        import pathlib

        sw = (pathlib.Path(settings.BASE_DIR) / "Frontend" / "sw.js").read_text()
        self.assertNotIn("js/paystack.js", sw)
