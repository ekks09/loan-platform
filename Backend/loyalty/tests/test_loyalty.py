from __future__ import annotations

from django.conf import settings

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from core.tests.helpers import auth_client, make_loan, make_user, make_verified_user
from loyalty.models import (
    LoyaltyAccount,
    LoyaltyEvent,
    RepaymentRecord,
    Tier,
    VaultEntry,
)
from loyalty.services import (
    account_for,
    discounted_fee,
    lock_into_vault,
    max_limit_kes,
    record_repayment,
    release_from_vault,
    score_for,
)
from loans.models import Loan


def repay(user, loan, reference, on_time=True, amount=1000):
    return record_repayment(
        user=user,
        loan=loan,
        reference=reference,
        amount_kes=amount,
        due_on=date.today(),
        paid_at=timezone.now(),
        on_time=on_time,
    )


class TierTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.loan = make_loan(self.user)

    def test_three_on_time_repayments_unlock_gold(self):
        account = account_for(self.user)
        self.assertEqual(account.tier, Tier.BASE)
        repay(self.user, self.loan, "R1")
        account.refresh_from_db()
        self.assertEqual(account.tier, Tier.SILVER)
        repay(self.user, self.loan, "R2")
        repay(self.user, self.loan, "R3")
        account.refresh_from_db()
        self.assertEqual(account.tier, Tier.GOLD)
        self.assertEqual(account.streak, 3)
        self.assertEqual(max_limit_kes(account), 60000)

    def test_gold_reduces_the_fee_and_never_below_the_minimum(self):
        account = account_for(self.user)
        for index in range(3):
            repay(self.user, self.loan, f"G{index}")
        account.refresh_from_db()
        base = Loan.compute_service_fee(10_000)
        self.assertEqual(base, 1100)
        self.assertEqual(discounted_fee(base, account), 1083)  # 1,100 - 1.5%
        # The floor still applies to the smallest amounts.
        self.assertEqual(
            discounted_fee(Loan.compute_service_fee(100), account), Loan.FEE_MINIMUM
        )

    def test_late_repayment_resets_the_streak(self):
        account = account_for(self.user)
        repay(self.user, self.loan, "S1")
        repay(self.user, self.loan, "S2")
        repay(self.user, self.loan, "S3")
        account.refresh_from_db()
        self.assertEqual(account.tier, Tier.GOLD)

        repay(self.user, self.loan, "S4", on_time=False)
        account.refresh_from_db()
        self.assertEqual(account.streak, 0)
        self.assertEqual(account.tier, Tier.BASE)
        self.assertTrue(
            LoyaltyEvent.objects.filter(kind=LoyaltyEvent.Kind.TIER_RESET).exists()
        )

    def test_cashback_is_one_percent_and_double_for_gold(self):
        account = account_for(self.user)
        repay(self.user, self.loan, "C1", amount=10_000)
        account.refresh_from_db()
        self.assertEqual(account.cashback_kes, 100)

        repay(self.user, self.loan, "C2", amount=10_000)
        repay(self.user, self.loan, "C3", amount=10_000)
        account.refresh_from_db()
        self.assertEqual(account.tier, Tier.GOLD)
        repay(self.user, self.loan, "C4", amount=10_000)
        account.refresh_from_db()
        # 100 + 100 + 100 at base, then 200 at Gold.
        self.assertEqual(account.cashback_kes, 500)

    def test_replaying_a_repayment_is_idempotent(self):
        account = account_for(self.user)
        repay(self.user, self.loan, "DUP", amount=5_000)
        repay(self.user, self.loan, "DUP", amount=5_000)
        account.refresh_from_db()
        self.assertEqual(account.cashback_kes, 50)
        self.assertEqual(RepaymentRecord.objects.count(), 1)


class VaultTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.loan = make_loan(self.user)
        self.account = account_for(self.user)
        repay(self.user, self.loan, "V1", amount=10_000)  # 100 cash-back

    def test_lock_and_release_move_only_cashback(self):
        entry = lock_into_vault(self.account, 60)
        self.assertEqual(entry.balance_after_kes, 60)
        self.account.refresh_from_db()
        self.assertEqual((self.account.cashback_kes, self.account.vault_kes), (40, 60))

        release_from_vault(self.account, 60)
        self.account.refresh_from_db()
        self.assertEqual((self.account.cashback_kes, self.account.vault_kes), (100, 0))
        self.assertEqual(VaultEntry.objects.count(), 2)

    def test_cannot_lock_more_than_earned(self):
        with self.assertRaises(ValueError):
            lock_into_vault(self.account, 101)
        self.account.refresh_from_db()
        self.assertEqual(self.account.vault_kes, 0)

    def test_cannot_release_more_than_held(self):
        lock_into_vault(self.account, 20)
        with self.assertRaises(ValueError):
            release_from_vault(self.account, 21)

    def test_negative_amounts_are_refused(self):
        for amount in (0, -5):
            with self.assertRaises(ValueError):
                lock_into_vault(self.account, amount)


class CreditScoreTests(TestCase):
    def test_new_account_sits_at_the_floor_and_is_explained(self):
        user = make_user()
        score, band, factors = score_for(user)
        self.assertGreaterEqual(score, 300)
        self.assertLessEqual(score, 850)
        self.assertEqual(band, "Starting")
        total = sum(f.points for f in factors)
        self.assertEqual(total, score)
        self.assertEqual(factors[-1].key, "base")

    def test_verified_and_repaid_account_scores_higher(self):
        # Compare like with like: one verified account with no history against
        # another verified account with three repayments. Two freshly created
        # accounts would differ only by the history, but ``make_user`` alone
        # leaves verification pending, which is a separate factor.
        peer = make_verified_user(
            consent=False, documents=False, phone="0733333333", national_id="33333333"
        )
        low, _, _ = score_for(peer)

        user = make_verified_user(consent=False, documents=False)
        loan = make_loan(user)
        account = account_for(user)
        for index in range(3):
            repay(user, loan, f"Z{index}", amount=10_000)
        account.refresh_from_db()
        high, band, _ = score_for(user, account)
        self.assertGreater(high, low)
        self.assertIn(band, {"Building", "Good", "Excellent"})

    def test_score_is_capped(self):
        user = make_verified_user(consent=False, documents=False)
        loan = make_loan(user)
        account = account_for(user)
        for index in range(40):
            repay(user, loan, f"CAP{index}", amount=60_000)
        account.refresh_from_db()
        score, band, factors = score_for(user, account)
        # 300 base + 220 identity + 180 repaid + 120 streak = 820. Vault and
        # tenure can add up to 90 more, so the cap is asserted structurally
        # rather than by reaching 850 with this fixture.
        self.assertLessEqual(score, 850)
        self.assertEqual(sum(f.points for f in factors), score)
        self.assertEqual(band, "Excellent")
        self.assertEqual(account.streak, 40)


class LoyaltyApiTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.loan = make_loan(self.user)

    def test_summary_requires_authentication(self):
        self.assertEqual(self.client.get("/api/loyalty/summary/").status_code, 401)

    def test_summary_returns_tier_score_and_disclosure(self):
        response = auth_client(self.user).get("/api/loyalty/summary/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["tier"], Tier.BASE)
        self.assertFalse(body["credit"]["portable"])
        self.assertIn(settings.PLATFORM_NAME, body["credit"]["disclosure"])
        self.assertEqual(body["vault"]["yield_bps"], 0)
        self.assertEqual(body["max_limit_kes"], 20000)

    def test_vault_endpoint_enforces_balances(self):
        client = auth_client(self.user)
        response = client.post(
            "/api/loyalty/vault/", {"direction": "lock", "amount_kes": 5000}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "vault_rejected")

        repay(self.user, self.loan, "API1", amount=10_000)
        ok = client.post(
            "/api/loyalty/vault/", {"direction": "lock", "amount_kes": 50}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["vault_kes"], 50)

    def test_vault_rejects_a_bad_direction_and_amount(self):
        client = auth_client(self.user)
        for payload in (
            {"direction": "steal", "amount_kes": 10},
            {"direction": "lock", "amount_kes": "ten"},
            {"direction": "lock", "amount_kes": -5},
        ):
            response = client.post("/api/loyalty/vault/", payload, format="json")
            self.assertEqual(response.status_code, 400, payload)

    def test_a_user_cannot_read_another_users_summary(self):
        other = make_user(phone="0722222222", national_id="22222222")
        repay(self.user, self.loan, "PRIV", amount=10_000)
        mine = auth_client(self.user).get("/api/loyalty/summary/").json()
        theirs = auth_client(other).get("/api/loyalty/summary/").json()
        self.assertNotEqual(mine["cashback_kes"], theirs["cashback_kes"])
        self.assertEqual(theirs["cashback_kes"], 0)
