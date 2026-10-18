from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.errors import InvalidLoanAmount, InvalidTransition
from core.tests.helpers import (
    expire_loan_offer,
    make_loan,
    make_staff,
    make_verified_user,
)
from loans.models import Loan, LoanEvent
from loans.services import (
    ActiveLoanExists,
    cancel,
    create_application,
    expire_stale_offers,
    reject,
)


class FeeScheduleTests(TestCase):
    def test_every_supported_amount_has_a_fee(self):
        for amount in range(Loan.MIN_AMOUNT, Loan.MAX_AMOUNT + 1, 37):
            fee = Loan.compute_service_fee(amount)
            self.assertGreater(fee, 0)
            self.assertLess(fee, amount)

    def test_band_boundaries(self):
        expected = {
            100: 45,  # the KES 45 minimum applies at the bottom of the range
            500: 45,
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
            self.assertEqual(Loan.compute_service_fee(amount), fee, amount)

    def test_effective_rate_increases_with_amount(self):
        # One representative amount per band: 35,000 and 60,000 share 12.0%.
        amounts = (1000, 5000, 10000, 20000, 35000)
        rates = [Loan.fee_rate_percent(a) for a in amounts]
        self.assertEqual(rates, sorted(rates))
        self.assertEqual(len(set(rates)), len(rates))
        fees = [Loan.compute_service_fee(a) for a in amounts]
        self.assertEqual(fees, sorted(fees))
        self.assertEqual(len(set(fees)), len(fees))

    def test_pricing_rows_cover_the_supported_range(self):
        rows = Loan.pricing_rows()
        self.assertEqual(rows[0]['min'], Loan.MIN_AMOUNT)
        self.assertEqual(rows[-1]['max'], Loan.MAX_AMOUNT)
        for previous, current in zip(rows, rows[1:]):
            self.assertEqual(previous['max'] + 1, current['min'])

    def test_invalid_amounts_are_refused(self):
        for amount in (0, 99, 60001, -1, "ten thousand"):
            with self.assertRaises(InvalidLoanAmount, msg=repr(amount)):
                Loan.compute_service_fee(amount)

    def test_booleans_are_not_amounts(self):
        with self.assertRaises(InvalidLoanAmount):
            Loan.compute_service_fee(True)

    def test_fee_percent_is_reported(self):
        self.assertEqual(Loan.fee_percent(10_000), 11.0)

    def test_schedule_is_contiguous(self):
        self.assertEqual(Loan.validate_schedule(), [])


class LoanCreationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()

    def test_create_application_sets_fee_phone_and_initial_event(self):
        loan = create_application(
            user=self.user, amount=10_000, mpesa_phone="0712345678"
        )
        self.assertEqual(loan.status, Loan.Status.PENDING)
        self.assertEqual(loan.service_fee, 1100)
        self.assertEqual(loan.mpesa_phone, "254712345678")
        self.assertTrue(loan.paystack_reference is None)
        event = loan.events.get()
        self.assertEqual(event.from_status, "")
        self.assertEqual(event.to_status, Loan.Status.PENDING)
        self.assertEqual(event.actor, self.user)

    def test_second_open_application_is_blocked(self):
        create_application(user=self.user, amount=10_000, mpesa_phone="0712345678")
        with self.assertRaises(ActiveLoanExists):
            create_application(user=self.user, amount=20_000, mpesa_phone="0712345678")
        self.assertEqual(self.user.loans.count(), 1)

    def test_closed_application_does_not_block_a_new_one(self):
        first = create_application(
            user=self.user, amount=10_000, mpesa_phone="0712345678"
        )
        cancel(first, actor=self.user)
        second = create_application(
            user=self.user, amount=20_000, mpesa_phone="0712345678"
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(self.user.loans.count(), 2)

    def test_amount_outside_product_range_is_refused(self):
        for amount in (99, 60_001):
            with self.assertRaises(InvalidLoanAmount):
                create_application(
                    user=self.user, amount=amount, mpesa_phone="0712345678"
                )


class LoanStateMachineTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.staff = make_staff()
        self.loan = create_application(
            user=self.user, amount=10_000, mpesa_phone="0712345678"
        )

    def test_legal_transition_updates_status_and_writes_event(self):
        self.loan.transition_to(Loan.Status.APPROVED, actor=self.staff, reason="ok")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)
        event = self.loan.events.order_by("-created_at").first()
        self.assertEqual(event.from_status, Loan.Status.PENDING)
        self.assertEqual(event.to_status, Loan.Status.APPROVED)
        self.assertEqual(event.reason, "ok")

    def test_illegal_transition_is_refused(self):
        with self.assertRaises(InvalidTransition):
            self.loan.transition_to(Loan.Status.DISBURSED)

    def test_same_status_is_a_noop_without_an_event(self):
        count = self.loan.events.count()
        result = self.loan.transition_to(Loan.Status.PENDING)
        self.assertEqual(result, self.loan)
        self.assertEqual(self.loan.events.count(), count)

    def test_events_are_immutable(self):
        event = self.loan.events.get()
        event.reason = "edited"
        with self.assertRaises(ValueError):
            event.save()

    def test_offer_sets_expiry_and_terms_version(self):
        from loans.services import approve

        approve(self.loan, actor=self.staff, expiry_hours=48)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)
        self.assertEqual(self.loan.offer_terms_version, "2026-10-draft")
        self.assertIsNotNone(self.loan.offer_expires_at)
        self.assertGreater(self.loan.offer_expires_at, timezone.now())
        self.assertLess(
            self.loan.offer_expires_at, timezone.now() + timedelta(hours=49)
        )

    def test_only_pending_applications_can_be_approved(self):
        self.loan.transition_to(Loan.Status.REJECTED, reason="no")
        with self.assertRaises(InvalidTransition):
            self.loan.issue_offer(actor=self.staff)

    def test_accept_offer_requires_the_matching_terms_version(self):
        self.loan.issue_offer(actor=self.staff)
        self.loan.refresh_from_db()
        with self.assertRaises(InvalidTransition):
            self.loan.accept_offer(version="2019-old", actor=self.user)
        self.loan.accept_offer(version=self.loan.offer_terms_version, actor=self.user)
        self.loan.refresh_from_db()
        self.assertTrue(self.loan.offer_is_accepted)
        self.assertTrue(
            self.loan.events.filter(reason__startswith="Offer accepted").exists()
        )

    def test_expired_offer_transitions_to_expired_and_refuses_acceptance(self):
        self.loan.issue_offer(actor=self.staff)
        self.loan.refresh_from_db()
        expire_loan_offer(self.loan)
        with self.assertRaises(InvalidTransition):
            self.loan.accept_offer(
                version=self.loan.offer_terms_version, actor=self.user
            )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.EXPIRED)

    def test_rejection_requires_a_reason(self):
        with self.assertRaises(InvalidTransition):
            reject(self.loan, actor=self.staff, reason="   ")
        reject(self.loan, actor=self.staff, reason="Income could not be verified")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.REJECTED)
        self.assertEqual(
            self.loan.rejection_reason, "Income could not be verified"
        )

    def test_disbursed_is_terminal_until_repayment_exists(self):
        self.loan.transition_to(Loan.Status.APPROVED, reason="manual")
        self.loan.transition_to(Loan.Status.DISBURSEMENT_PENDING, reason="start")
        self.loan.transition_to(Loan.Status.DISBURSED, reason="confirmed")
        self.assertIsNotNone(self.loan.disbursed_at)
        with self.assertRaises(InvalidTransition):
            self.loan.transition_to(Loan.Status.REPAID)

    def test_failed_disbursement_can_recover(self):
        self.loan.transition_to(Loan.Status.APPROVED, reason="manual")
        self.loan.transition_to(Loan.Status.DISBURSEMENT_PENDING, reason="start")
        self.loan.transition_to(Loan.Status.FAILED, reason="provider failed")
        self.loan.transition_to(
            Loan.Status.DISBURSEMENT_PENDING, reason="provider confirmed late"
        )
        self.loan.transition_to(Loan.Status.DISBURSED, reason="provider confirmed")

    def test_only_pending_or_approved_can_be_cancelled(self):
        self.loan.issue_offer(actor=self.staff)
        self.loan.refresh_from_db()
        self.loan.transition_to(Loan.Status.DISBURSEMENT_PENDING, reason="start")
        with self.assertRaises(InvalidTransition):
            cancel(self.loan, actor=self.user)


class OfferExpiryJobTests(TestCase):
    def setUp(self):
        self.user = make_verified_user()
        self.loan = make_loan(self.user, approve_offer=True)

    def test_dry_run_counts_without_mutating(self):
        expire_loan_offer(self.loan)
        self.assertEqual(expire_stale_offers(dry_run=True), 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)

    def test_apply_expires_and_writes_event(self):
        expire_loan_offer(self.loan)
        self.assertEqual(expire_stale_offers(dry_run=False), 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.EXPIRED)
        self.assertTrue(
            self.loan.events.filter(to_status=Loan.Status.EXPIRED).exists()
        )

    def test_non_expired_offers_are_untouched(self):
        self.assertEqual(expire_stale_offers(dry_run=False), 0)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)
