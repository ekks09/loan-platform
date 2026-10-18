from __future__ import annotations

from django.test import TestCase

from core.money import InvalidMoney
from core.money import as_kes, format_kes, from_minor, to_minor
from payments.models import LedgerEntry
from payments.services import journal_balance, post_journal


class MoneyConversionTests(TestCase):
    def test_kes_and_minor_units_round_trip(self):
        for amount in (0, 1, 200, 60_000):
            self.assertEqual(from_minor(to_minor(amount)), amount)

    def test_to_minor_rejects_invalid_amounts(self):
        for value in ("12.50", -1, True, None):
            with self.assertRaises(InvalidMoney, msg=repr(value)):
                to_minor(value)

    def test_from_minor_rejects_fractional_shillings(self):
        with self.assertRaises(InvalidMoney):
            from_minor(12345)  # 123.45 KES

    def test_from_minor_rejects_negative_values(self):
        with self.assertRaises(InvalidMoney):
            from_minor(-100)

    def test_as_kes_accepts_digit_strings_only(self):
        self.assertEqual(as_kes("1500"), 1500)
        for value in ("1,500", "15.00", "abc", -5):
            with self.assertRaises(InvalidMoney):
                as_kes(value)

    def test_format_kes(self):
        self.assertEqual(format_kes(60_000), "KES 60,000")


class JournalTests(TestCase):
    def test_balanced_journal_is_accepted_and_nets_to_zero(self):
        group = post_journal(
            [
                (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, 1200),
                (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.CREDIT, 1200),
            ],
            reference="LPF_test",
            memo="Test collection",
        )
        entries = LedgerEntry.objects.filter(entry_group=group)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(journal_balance(group), 0)

    def test_unbalanced_journal_is_refused_without_writing_rows(self):
        with self.assertRaises(ValueError):
            post_journal(
                [
                    (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, 1200),
                    (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.CREDIT, 1000),
                ],
                reference="LPF_bad",
                memo="Bad",
            )
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_empty_or_zero_journal_is_refused(self):
        for lines in ([], [
            (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, 0),
            (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.CREDIT, 0),
        ]):
            with self.assertRaises(ValueError):
                post_journal(lines, reference="x", memo="x")
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_entries_are_immutable(self):
        entry = LedgerEntry.objects.create(
            account=LedgerEntry.Account.PLATFORM_REVENUE,
            direction=LedgerEntry.Direction.CREDIT,
            amount_kes=100,
            reference="immutable",
        )
        entry.memo = "edited"
        with self.assertRaises(ValueError):
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()

    def test_non_positive_amount_is_refused(self):
        with self.assertRaises(ValueError):
            LedgerEntry.objects.create(
                account=LedgerEntry.Account.PLATFORM_REVENUE,
                direction=LedgerEntry.Direction.CREDIT,
                amount_kes=0,
                reference="zero",
            )

    def test_negative_direction_amounts_are_impossible_at_the_field_level(self):
        # PositiveIntegerField rejects negative values at the database layer on
        # Postgres; the service also validates before writing.
        with self.assertRaises(ValueError):
            post_journal(
                [
                    (LedgerEntry.Account.PROVIDER_CLEARING, LedgerEntry.Direction.DEBIT, -100),
                    (LedgerEntry.Account.PLATFORM_REVENUE, LedgerEntry.Direction.CREDIT, -100),
                ],
                reference="negative",
                memo="negative",
            )
