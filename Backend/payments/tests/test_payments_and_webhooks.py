from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.errors import (
    FeeCollectionDisabled,
    InvalidTransition,
    PaymentsDisabled,
)
from core.tests.helpers import (
    DictMock,
    auth_client,
    make_loan,
    make_staff,
    make_verified_user,
    patched_provider,
)
from payments.models import LedgerEntry, Payment, PaymentEvent, WebhookEvent
from payments.services import (
    get_or_create_fee_payment,
    initialize_fee_payment,
    journal_balance,
    mark_payment_succeeded,
    process_charge_success,
)
from users.models import User


def charge_payload(payment: Payment, **overrides) -> dict:
    data = DictMock.charge(payment.reference, payment.amount_kes)
    data.update(overrides)
    return data


class PaymentInitializationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.staff = make_staff()
        self.loan = make_loan(self.user, accept=True)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_checkout_is_created_once_and_reused(self):
        with patched_provider(
            initialize_transaction={
                "reference": self.loan.paystack_reference,
                "authorization_url": "https://checkout.paystack.test/abc",
                "access_code": "ACCESS_ABC",
            }
        ) as client:
            payment = initialize_fee_payment(self.loan, actor=self.user)
            self.assertEqual(payment.status, Payment.Status.INITIALIZED)
            self.assertEqual(payment.amount_kes, self.loan.service_fee)
            self.assertEqual(payment.currency, "KES")
            self.assertTrue(payment.reference.startswith(f"LPF_{self.loan.pk}_"))
            self.assertEqual(payment.authorization_url, "https://checkout.paystack.test/abc")
            self.assertEqual(payment.access_code, "ACCESS_ABC")
            self.assertEqual(
                payment.idempotency_key, f"fee:loan:{self.loan.pk}"
            )

            self.loan.refresh_from_db()
            self.assertEqual(self.loan.paystack_reference, payment.reference)

            again = initialize_fee_payment(self.loan, actor=self.user)
            self.assertEqual(again.pk, payment.pk)
            self.assertEqual(client.initialize_transaction.call_count, 1)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_checkout_is_restricted_to_mobile_money_in_kes(self):
        with patched_provider(
            initialize_transaction={
                "reference": "LPF_channel_test",
                "authorization_url": "https://checkout.paystack.test/abc",
                "access_code": "ACCESS_ABC",
            }
        ) as client:
            initialize_fee_payment(self.loan, actor=self.user)
            kwargs = client.initialize_transaction.call_args.kwargs
            self.assertEqual(kwargs["currency"], "KES")
            self.assertEqual(kwargs["channels"], ["mobile_money"])
            self.assertEqual(kwargs["metadata"]["provider"], "mpesa")

    def test_fee_collection_must_be_enabled(self):
        with self.assertRaises(FeeCollectionDisabled):
            initialize_fee_payment(self.loan, actor=self.user)

    @override_settings(PAYMENTS_ENABLED=False)
    def test_payments_must_be_enabled(self):
        with self.assertRaises(PaymentsDisabled):
            initialize_fee_payment(self.loan, actor=self.user)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_offer_must_be_accepted_before_paying(self):
        loan = make_loan(make_verified_user(phone="0722222222", national_id="22222222"),
                        approve_offer=True)
        with self.assertRaises(InvalidTransition):
            initialize_fee_payment(loan, actor=self.user)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_only_approved_loans_can_be_charged(self):
        loan = make_loan(make_verified_user(phone="0733333333", national_id="33333333"))
        with self.assertRaises(InvalidTransition):
            initialize_fee_payment(loan, actor=self.user)

    def test_fee_payment_creation_is_idempotent(self):
        first = get_or_create_fee_payment(self.loan)
        second = get_or_create_fee_payment(self.loan)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.loan.payments.count(), 1)


class ChargeSuccessTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.payment = get_or_create_fee_payment(self.loan)

    def test_success_posts_a_balanced_collection_journal(self):
        payment = mark_payment_succeeded(
            self.payment, charge_payload(self.payment), reason="test"
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertTrue(payment.verified)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(payment.channel, "mobile_money")
        self.assertEqual(payment.provider_transaction_id, "12345")

        self.loan.refresh_from_db()
        self.assertTrue(self.loan.service_fee_paid)

        group = LedgerEntry.objects.filter(payment=payment).first().entry_group
        self.assertEqual(journal_balance(group), 0)
        self.assertEqual(LedgerEntry.objects.filter(payment=payment).count(), 2)
        self.assertEqual(payment.events.count(), 1)

    def test_success_is_idempotent_and_does_not_duplicate_the_journal(self):
        first = mark_payment_succeeded(
            self.payment, charge_payload(self.payment), reason="test"
        )
        second = mark_payment_succeeded(first, charge_payload(first), reason="retry")
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(LedgerEntry.objects.filter(payment=first).count(), 2)
        self.assertEqual(PaymentEvent.objects.filter(payment=first).count(), 1)

    def test_pending_payment_may_jump_to_succeeded_for_provider_initiated_success(self):
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.payment.transition_to(Payment.Status.SUCCEEDED, reason="provider confirmed")
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_refunded_payment_cannot_be_marked_paid_again(self):
        self.payment.transition_to(Payment.Status.SUCCEEDED, reason="test")
        self.payment.transition_to(Payment.Status.REFUND_PENDING, reason="test")
        self.payment.transition_to(Payment.Status.REFUNDED, reason="test")
        with self.assertRaises(InvalidTransition):
            mark_payment_succeeded(self.payment, charge_payload(self.payment), reason="again")

    def test_amount_mismatch_is_refused(self):
        data = charge_payload(self.payment, amount=self.payment.amount_kes * 100 + 100)
        with self.assertRaises(InvalidTransition):
            process_charge_success(self.payment.reference, data, verify=False)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_currency_mismatch_is_refused(self):
        data = charge_payload(self.payment, currency="NGN")
        with self.assertRaises(InvalidTransition):
            process_charge_success(self.payment.reference, data, verify=False)

    def test_reference_mismatch_is_refused(self):
        data = charge_payload(self.payment, reference="LPF_other")
        with self.assertRaises(InvalidTransition):
            process_charge_success(self.payment.reference, data, verify=False)

    def test_non_success_status_is_refused(self):
        data = charge_payload(self.payment, status="failed")
        with self.assertRaises(InvalidTransition):
            process_charge_success(self.payment.reference, data, verify=False)

    def test_unknown_reference_is_refused(self):
        with self.assertRaises(InvalidTransition):
            process_charge_success("LPF_unknown", {}, verify=False)


class PaymentApiTests(APITestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.other = make_verified_user(phone="0722222222", national_id="22222222")
        self.staff = make_staff()
        self.loan = make_loan(self.user, accept=True)

    def test_init_requires_authentication_and_ownership(self):
        payload = {"loan_id": self.loan.pk}
        self.assertEqual(
            self.client.post("/api/payments/init/", payload, format="json").status_code,
            401,
        )
        response = auth_client(self.other).post(
            "/api/payments/init/", payload, format="json"
        )
        self.assertEqual(response.status_code, 409)

    def test_init_refused_when_fee_collection_is_disabled(self):
        response = auth_client(self.user).post(
            "/api/payments/init/", {"loan_id": self.loan.pk}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "fee_collection_disabled")

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_init_returns_checkout_data_without_leaking_provider_secrets(self):
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
        self.assertEqual(body["mode"], "test")
        self.assertEqual(body["amount_kes"], self.loan.service_fee)
        self.assertTrue(body["reference"].startswith("LPF_"))
        self.assertTrue(body["email"].endswith("@example.com"))
        self.assertNotIn(settings.PAYSTACK_SECRET_KEY, response.content.decode())

    def test_verify_is_owner_scoped_and_idempotent(self):
        payment = get_or_create_fee_payment(self.loan)
        mark_payment_succeeded(payment, charge_payload(payment), reason="test")

        other = auth_client(self.other).post(
            "/api/payments/verify/", {"reference": payment.reference}, format="json"
        )
        self.assertEqual(other.status_code, 409)

        response = auth_client(self.user).post(
            "/api/payments/verify/", {"reference": payment.reference}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_verified")

    def test_reference_status_route_is_read_only_and_owner_scoped(self):
        payment = get_or_create_fee_payment(self.loan)
        other_loan = make_loan(self.other, accept=True)
        get_or_create_fee_payment(other_loan)

        unauthenticated = self.client.get(
            f"/api/paystack/verify/{payment.reference}/"
        )
        self.assertEqual(unauthenticated.status_code, 401)

        response = auth_client(self.user).get(
            f"/api/paystack/verify/{payment.reference}/"
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["payment"]["reference"], payment.reference)
        self.assertEqual(body["payment"]["status"], Payment.Status.PENDING)

        # It must not change state, and another user cannot read it.
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        foreign = auth_client(self.other).get(
            f"/api/paystack/verify/{payment.reference}/"
        )
        self.assertEqual(foreign.status_code, 404)

    def test_payment_history_is_owner_scoped(self):
        own = get_or_create_fee_payment(self.loan)
        other_loan = make_loan(self.other, accept=True)
        get_or_create_fee_payment(other_loan)

        response = auth_client(self.user).get("/api/payments/history/")
        self.assertEqual(response.status_code, 200)
        references = [row["reference"] for row in response.json()["results"]]
        self.assertEqual(references, [own.reference])


class WebhookTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.payment = get_or_create_fee_payment(self.loan)

    def _sign(self, body: bytes) -> str:
        return hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
        ).hexdigest()

    def _post(self, payload: dict, *, signature: str | None = None):
        body = json.dumps(payload).encode()
        headers = {}
        if signature != "__missing__":
            headers["HTTP_X_PAYSTACK_SIGNATURE"] = signature or self._sign(body)
        return self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            **headers,
        )

    def payload(self, **overrides) -> dict:
        data = charge_payload(self.payment)
        data.update(overrides.pop("data", {}))
        return {"event": overrides.pop("event", "charge.success"), "data": data}

    @override_settings(PAYSTACK_VERIFY_ON_WEBHOOK=False)
    def test_valid_charge_event_is_processed(self):
        response = self._post(self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "processed")

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
        event = WebhookEvent.objects.get()
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(event.event_type, "charge.success")
        self.assertEqual(event.reference, self.payment.reference)

    @override_settings(PAYSTACK_VERIFY_ON_WEBHOOK=False)
    def test_duplicate_delivery_is_acknowledged_once(self):
        payload = self.payload()
        self.assertEqual(self._post(payload).status_code, 200)
        second = self._post(payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.filter(payment=self.payment).count(), 1)

    def test_missing_signature_is_rejected(self):
        response = self._post(self.payload(), signature="__missing__")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_invalid_signature_is_rejected_before_parsing(self):
        response = self._post(self.payload(), signature="0" * 128)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @override_settings(PAYMENTS_ENABLED=False)
    def test_webhook_refuses_traffic_when_payments_are_disabled(self):
        response = self._post(self.payload())
        self.assertEqual(response.status_code, 503)

    def test_oversized_body_is_rejected(self):
        body = b"{" + b"0" * (300 * 1024) + b"}"
        response = self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=self._sign(body),
        )
        self.assertEqual(response.status_code, 413)

    def test_invalid_json_with_valid_signature_is_rejected(self):
        body = b"not-json"
        response = self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=self._sign(body),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_processing_failure_stores_the_event_for_replay(self):
        payload = self.payload(data={"reference": "LPF_does_not_exist"})
        response = self._post(payload)
        self.assertEqual(response.status_code, 500)
        event = WebhookEvent.objects.get()
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("No payment matches", event.error)

    def test_non_charge_events_are_stored_and_ignored(self):
        response = self._post({"event": "customeridentification.success", "data": {"reference": "x"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.Status.IGNORED)


class WebhookProviderVerificationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.payment = get_or_create_fee_payment(self.loan)

    def _post(self, payload: dict):
        body = json.dumps(payload).encode()
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
        ).hexdigest()
        return self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

    def test_charge_success_is_reverified_with_the_provider(self):
        payload = {
            "event": "charge.success",
            "data": charge_payload(self.payment),
        }
        with patched_provider(
            verify_transaction=charge_payload(self.payment)
        ) as client:
            response = self._post(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
        client.verify_transaction.assert_called_once_with(self.payment.reference)

    def test_provider_verification_failure_leaves_the_event_for_replay(self):
        payload = {
            "event": "charge.success",
            "data": charge_payload(self.payment),
        }
        with patched_provider(
            verify_transaction=charge_payload(self.payment, amount=1)
        ):
            response = self._post(payload)
        self.assertEqual(response.status_code, 500)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.Status.FAILED)


class TransferWebhookTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, accept=True)
        self.staff = make_staff()
        from core.tests.helpers import patched_provider
        from payments.services import start_disbursement

        with patched_provider(
            create_transfer_recipient={"recipient_code": "RCP_1"},
            initiate_transfer={"transfer_code": "TRF_1", "reference": "LNT_1"},
        ):
            self.transfer = start_disbursement(self.loan, actor=self.staff)

    def _post(self, payload: dict):
        body = json.dumps(payload).encode()
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
        ).hexdigest()
        return self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

    def test_transfer_success_confirms_the_loan(self):
        response = self._post(
            {
                "event": "transfer.success",
                "data": {
                    "reference": self.transfer.reference,
                    "transfer_code": "TRF_1",
                    "status": "success",
                },
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.transfer.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(self.transfer.status, "SUCCESS")
        self.assertEqual(self.loan.status, "DISBURSED")

    def test_unknown_transfer_reference_is_retried_by_the_provider(self):
        response = self._post(
            {
                "event": "transfer.success",
                "data": {"reference": "LNT_999", "transfer_code": "TRF_999"},
            }
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.Status.FAILED)
