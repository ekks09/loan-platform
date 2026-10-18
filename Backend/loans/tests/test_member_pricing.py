"""Member pricing: the Gold discount and the tier borrowing limit.

The schedule in ``Loan.compute_service_fee`` is the price for everybody. The
loyalty layer can reduce it for a Gold member, never increase it, and never
take it below the published minimum. These tests pin both halves so a future
pricing change cannot quietly leak a discount to a Base member or charge a
Gold member the standard fee.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.tests.helpers import auth_client, make_loan, make_verified_user
from loans.models import Loan
from loans.services import (
    AmountAboveTierLimit,
    borrowing_limit,
    cancel,
    create_application,
    quoted_fee,
)
from loyalty.models import GOLD_FEE_DISCOUNT_BPS, MAX_LIMIT_WITH_GOLD, Tier
from loyalty.services import account_for, record_repayment


def make_gold(user, loan=None, close=False):
    """Drive a user to Gold with three provider-confirmed on-time repayments.

    ``close`` cancels the repayment loan afterwards, which frees the one-open-
    application rule for tests that then want to apply again.
    """
    account = account_for(user)
    loan = loan or make_loan(user)
    for index in range(3):
        record_repayment(
            user=user,
            loan=loan,
            reference=f"GOLD-{user.pk}-{index}",
            amount_kes=1_000,
            due_on=date.today(),
            paid_at=timezone.now(),
            on_time=True,
        )
    if close:
        loan.refresh_from_db()
        cancel(loan, actor=user, reason="Test cleanup")
    account.refresh_from_db()
    return account


class FeeDiscountTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()

    def test_base_member_pays_the_published_fee(self):
        for amount in (499, 5_000, 9_999, 19_999):
            with self.subTest(amount=amount):
                self.assertEqual(quoted_fee(self.user, amount), Loan.compute_service_fee(amount))

    def test_gold_member_gets_one_and_a_half_percent_off(self):
        make_gold(self.user)
        for amount in (5_000, 10_000, 60_000):
            with self.subTest(amount=amount):
                standard = Loan.compute_service_fee(amount)
                expected = max(
                    Loan.FEE_MINIMUM,
                    (standard * (10_000 - GOLD_FEE_DISCOUNT_BPS)) // 10_000,
                )
                self.assertEqual(quoted_fee(self.user, amount), expected)
                self.assertLessEqual(quoted_fee(self.user, amount), standard)

    def test_discount_never_drops_below_the_published_minimum(self):
        make_gold(self.user)
        self.assertEqual(quoted_fee(self.user, Loan.MIN_AMOUNT), Loan.FEE_MINIMUM)

    def test_application_stores_the_discounted_fee(self):
        make_gold(self.user, close=True)
        loan = create_application(
            user=self.user, amount=10_000, mpesa_phone=self.user.phone
        )
        self.assertEqual(loan.service_fee, quoted_fee(self.user, 10_000))
        self.assertLess(loan.service_fee, Loan.compute_service_fee(10_000))

    def test_offer_requotes_instead_of_reverting_to_the_standard_fee(self):
        loan = make_loan(self.user, amount=10_000)
        standard = Loan.compute_service_fee(10_000)
        self.assertEqual(loan.service_fee, standard)

        make_gold(self.user, loan=loan)
        loan.refresh_from_db()
        loan.issue_offer(actor=self.user)
        loan.refresh_from_db()
        self.assertEqual(loan.service_fee, quoted_fee(self.user, 10_000))
        self.assertLess(loan.service_fee, standard)

    def test_saved_loan_keeps_the_fee_it_was_quoted(self):
        """An unrelated save must not silently re-price an application."""
        make_gold(self.user, close=True)
        loan = make_loan(self.user, amount=10_000)
        quoted = quoted_fee(self.user, 10_000)
        self.assertEqual(loan.service_fee, quoted)

        loan.last_event = "Edited by support"
        loan.save()
        loan.refresh_from_db()
        self.assertEqual(loan.service_fee, quoted)


class BorrowingLimitTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()

    def test_base_limit_blocks_above_twenty_thousand(self):
        self.assertEqual(borrowing_limit(self.user), 20_000)
        with self.assertRaises(AmountAboveTierLimit):
            create_application(
                user=self.user, amount=20_001, mpesa_phone=self.user.phone
            )

    def test_gold_limit_reaches_sixty_thousand(self):
        make_gold(self.user, close=True)
        self.assertEqual(borrowing_limit(self.user), MAX_LIMIT_WITH_GOLD)
        loan = create_application(
            user=self.user, amount=60_000, mpesa_phone=self.user.phone
        )
        self.assertEqual(loan.amount, 60_000)
        self.assertEqual(loan.service_fee, quoted_fee(self.user, 60_000))

    def test_limit_can_never_exceed_the_product_maximum(self):
        account = make_gold(self.user)
        account.tier = Tier.GOLD
        account.save(update_fields=["tier"])
        self.assertLessEqual(borrowing_limit(self.user), Loan.MAX_AMOUNT)

    def test_api_returns_a_domain_error_with_the_limit_in_the_message(self):
        client = auth_client(self.user)
        response = client.post(
            "/api/loans/apply/",
            {"amount": 30_000, "mpesa_phone": self.user.phone},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("20,000", str(body))

    def test_staff_cannot_bypass_the_limit_via_the_api(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = auth_client(self.user).post(
            "/api/loans/apply/",
            {"amount": 30_000, "mpesa_phone": self.user.phone},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
