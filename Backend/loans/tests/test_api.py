from __future__ import annotations

from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.helpers import (
    attach_documents,
    auth_client,
    make_loan,
    make_staff,
    make_user,
    make_verified_user,
)
from loans.models import Loan


class QuoteApiTests(APITestCase):
    def test_quote_is_public_and_returns_the_schedule_fee(self):
        response = self.client.get("/api/loans/quote/?amount=10000")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service_fee"], 1100)
        self.assertEqual(body["currency"], "KES")
        self.assertEqual(body["fee_collection_mode"], "disabled")
        self.assertFalse(body["upfront_fee_required"])

    def test_quote_exposes_the_band_rate_and_the_effective_rate(self):
        response = self.client.get("/api/loans/quote/?amount=500")
        body = response.json()
        self.assertEqual(body["service_fee"], 45)
        self.assertEqual(body["fee_rate_percent"], 9.0)
        # KES 45 on KES 500 is 9.0%; the minimum only bites below that.
        self.assertEqual(body["fee_percent"], 9.0)

        low = self.client.get("/api/loans/quote/?amount=100").json()
        self.assertEqual(low["service_fee"], 45)
        self.assertEqual(low["fee_rate_percent"], 9.0)
        self.assertEqual(low["fee_percent"], 45.0)

    def test_quote_matches_the_published_six_band_schedule(self):
        expected = {
            100: 45,
            999: 89,
            1000: 100,
            4999: 499,
            5000: 525,
            9999: 1049,
            10000: 1100,
            19999: 2199,
            20000: 2300,
            34999: 4024,
            35000: 4200,
            60000: 7200,
        }
        for amount, fee in expected.items():
            body = self.client.get(f"/api/loans/quote/?amount={amount}").json()
            self.assertEqual(body["service_fee"], fee, amount)

    def test_quote_rejects_missing_or_invalid_amounts(self):
        for query in ("", "?amount=abc", "?amount=99", "?amount=60001"):
            response = self.client.get("/api/loans/quote/" + query)
            self.assertIn(
                response.status_code,
                (400, 409),
                query,
            )


class LoanApplicationApiTests(APITestCase):
    def test_application_requires_authentication(self):
        self.assertEqual(
            self.client.post(
                "/api/loans/apply/",
                {"amount": 10_000, "mpesa_phone": "0712345678"},
                format="json",
            ).status_code,
            401,
        )

    def test_application_requires_current_consent(self):
        user = make_verified_user(consent=False)
        client = auth_client(user)
        response = client.post(
            "/api/loans/apply/",
            {"amount": 10_000, "mpesa_phone": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "consent_required")

    def test_application_requires_documents_and_verification(self):
        unverified = make_verified_user()
        unverified.verification_status = "pending"
        unverified.save(update_fields=["verification_status"])
        client = auth_client(unverified)
        response = client.post(
            "/api/loans/apply/",
            {"amount": 10_000, "mpesa_phone": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "identity_not_verified")

        no_docs = make_verified_user(phone="0722222222", national_id="22222222")
        no_docs.id_front_path = no_docs.id_back_path = no_docs.selfie_path = ""
        no_docs.save(update_fields=["id_front_path", "id_back_path", "selfie_path"])
        response = auth_client(no_docs).post(
            "/api/loans/apply/",
            {"amount": 10_000, "mpesa_phone": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "identity_not_verified")

    def test_happy_path_creates_a_pending_loan(self):
        user = make_verified_user()
        response = auth_client(user).post(
            "/api/loans/apply/",
            {"amount": 12_000, "mpesa_phone": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        loan = response.json()["loan"]
        self.assertEqual(loan["status"], "PENDING")
        self.assertEqual(loan["service_fee"], 1320)
        self.assertEqual(loan["status_label"], "Pending review")
        self.assertEqual(loan["mpesa_phone"], "254712345678")

    def test_amount_outside_range_is_a_validation_error(self):
        user = make_verified_user()
        client = auth_client(user)
        for amount in (99, 60_001):
            response = client.post(
                "/api/loans/apply/",
                {"amount": amount, "mpesa_phone": "0712345678"},
                format="json",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("amount", response.json()["error"]["details"])

    def test_second_application_is_blocked(self):
        user = make_verified_user()
        client = auth_client(user)
        payload = {"amount": 10_000, "mpesa_phone": "0712345678"}
        self.assertEqual(client.post("/api/loans/apply/", payload, format="json").status_code, 201)
        response = client.post("/api/loans/apply/", payload, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "active_loan_exists")


class ActiveLoanContractTests(APITestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.client = auth_client(self.user)

    def test_no_loans_returns_null_contract(self):
        response = self.client.get("/api/loans/active/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"has_active_loan": False, "loan": None, "latest": None},
        )

    def test_open_loan_is_returned_with_label(self):
        loan = make_loan(self.user)
        body = self.client.get("/api/loans/active/").json()
        self.assertTrue(body["has_active_loan"])
        self.assertEqual(body["loan"]["id"], loan.pk)
        self.assertEqual(body["latest"]["id"], loan.pk)
        self.assertEqual(body["loan"]["status_label"], "Pending review")

    def test_closed_loan_is_latest_but_not_active(self):
        loan = make_loan(self.user)
        from loans.services import cancel

        cancel(loan, actor=self.user)
        body = self.client.get("/api/loans/active/").json()
        self.assertFalse(body["has_active_loan"])
        self.assertIsNone(body["loan"])
        self.assertEqual(body["latest"]["id"], loan.pk)
        self.assertEqual(body["latest"]["status_label"], "Cancelled")

    def test_current_alias_matches_active(self):
        make_loan(self.user)
        self.assertEqual(
            self.client.get("/api/loans/current/").json(),
            self.client.get("/api/loans/active/").json(),
        )

    def test_history_is_paginated(self):
        loan = make_loan(self.user)
        body = self.client.get("/api/loans/history/").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["pages"], 1)
        self.assertEqual(body["results"][0]["id"], loan.pk)


class LoanDetailApiTests(APITestCase):
    def setUp(self):
        self.owner = make_verified_user()
        self.other = make_verified_user(phone="0722222222", national_id="22222222")
        self.loan = make_loan(self.owner)

    def test_owner_sees_loan_and_events(self):
        response = auth_client(self.owner).get(f"/api/loans/{self.loan.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["loan"]["id"], self.loan.pk)
        self.assertEqual(len(response.json()["events"]), 1)

    def test_other_users_get_not_found(self):
        response = auth_client(self.other).get(f"/api/loans/{self.loan.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "loan_not_found")

    def test_staff_can_read_any_loan(self):
        staff = make_staff()
        response = auth_client(staff).get(f"/api/loans/{self.loan.pk}/")
        self.assertEqual(response.status_code, 200)


class OfferAcceptanceApiTests(APITestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.staff = make_staff()
        self.client = auth_client(self.user)

    def test_accept_requires_the_current_terms_version(self):
        loan = make_loan(self.user, approve_offer=True)
        response = self.client.post(
            f"/api/loans/{loan.pk}/accept-offer/",
            {"terms_version": "old"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "invalid_state_transition"
        )

        response = self.client.post(
            f"/api/loans/{loan.pk}/accept-offer/",
            {"terms_version": loan.offer_terms_version},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["loan"]["offer_is_accepted"])
        self.assertEqual(response.json()["loan"]["next_action"], "await_disbursement")

    def test_other_users_cannot_accept(self):
        loan = make_loan(self.user, approve_offer=True)
        other = make_verified_user(phone="0733333333", national_id="33333333")
        response = auth_client(other).post(
            f"/api/loans/{loan.pk}/accept-offer/",
            {"terms_version": loan.offer_terms_version},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_cancel_pending_loan(self):
        loan = make_loan(self.user)
        response = self.client.post(f"/api/loans/{loan.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["loan"]["status"], "CANCELLED")


class StaffLoanOperationsTests(APITestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.staff = make_staff()
        self.loan = make_loan(self.user)

    def test_non_staff_cannot_approve(self):
        response = auth_client(self.user).post(
            f"/api/loans/ops/{self.loan.pk}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")

    def test_staff_can_approve_and_issue_an_offer(self):
        response = auth_client(self.staff).post(
            f"/api/loans/ops/{self.loan.pk}/approve/",
            {"expiry_hours": 24},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        loan = response.json()["loan"]
        self.assertEqual(loan["status"], "APPROVED")
        self.assertEqual(loan["next_action"], "accept_offer")
        self.assertEqual(loan["offer_terms_version"], settings.TERMS_VERSION)

    def test_rejection_requires_a_reason(self):
        client = auth_client(self.staff)
        empty = client.post(
            f"/api/loans/ops/{self.loan.pk}/reject/", {"reason": ""}, format="json"
        )
        self.assertEqual(empty.status_code, 409)

        response = client.post(
            f"/api/loans/ops/{self.loan.pk}/reject/",
            {"reason": "Document mismatch"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["loan"]["status"], "REJECTED")
        self.assertEqual(response.json()["loan"]["rejection_reason"], "Document mismatch")

    def test_disbursement_refused_before_acceptance(self):
        client = auth_client(self.staff)
        client.post(
            f"/api/loans/ops/{self.loan.pk}/approve/", {}, format="json"
        )
        response = client.post(
            f"/api/loans/ops/{self.loan.pk}/disburse/", {}, format="json"
        )
        self.assertEqual(response.status_code, 409)

    @override_settings(PAYMENTS_ENABLED=False)
    def test_disbursement_refused_when_payments_are_disabled(self):
        from loans.services import accept_offer, approve

        loan = self.loan
        approve(loan, actor=self.staff)
        loan.refresh_from_db()
        accept_offer(loan, actor=self.user, version=loan.offer_terms_version)
        response = auth_client(self.staff).post(
            f"/api/loans/ops/{loan.pk}/disburse/", {}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "payments_disabled")

    def test_ops_feed_requires_staff(self):
        self.assertEqual(
            auth_client(self.user).get("/api/loans/ops/").status_code, 403
        )
        response = auth_client(self.staff).get("/api/loans/ops/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertIn("user", response.json()["results"][0])
