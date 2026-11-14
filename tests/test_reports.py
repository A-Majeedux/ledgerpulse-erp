"""
tests/test_reports.py

Tests for the Trial Balance calculation. These need NO database:
they only check the maths, so they are safe to run anytime.
"""
import unittest
from decimal import Decimal

from reports.services import compute_trial_balance


def row(code, name, a_type, debit, credit, account_id=1):
    return (account_id, code, name, a_type, Decimal(debit), Decimal(credit))


class TrialBalanceTests(unittest.TestCase):

    def test_balanced_books(self):
        rows = [
            row("1010", "Bank Account", "Asset", "1000.00", "0.00"),
            row("3000", "Owner's Equity", "Equity", "0.00", "1000.00"),
        ]
        tb = compute_trial_balance(rows)
        self.assertTrue(tb["is_balanced"])
        self.assertEqual(tb["debit_balance"], Decimal("1000.00"))
        self.assertEqual(tb["credit_balance"], Decimal("1000.00"))
        self.assertEqual(tb["difference"], Decimal("0.00"))

    def test_net_balance_goes_to_natural_side(self):
        rows = [
            row("1010", "Bank", "Asset", "5000.00", "1200.00"),
            row("2000", "Trade Creditors", "Liability", "300.00", "4100.00"),
        ]
        tb = compute_trial_balance(rows)
        bank, creditors = tb["lines"]
        self.assertEqual(bank["debit_balance"], Decimal("3800.00"))
        self.assertEqual(bank["credit_balance"], Decimal("0.00"))
        self.assertEqual(creditors["credit_balance"], Decimal("3800.00"))
        self.assertEqual(creditors["debit_balance"], Decimal("0.00"))
        self.assertTrue(tb["is_balanced"])

    def test_out_of_balance_is_detected(self):
        rows = [
            row("1010", "Bank", "Asset", "1000.00", "0.00"),
            row("3000", "Equity", "Equity", "0.00", "900.00"),
        ]
        tb = compute_trial_balance(rows)
        self.assertFalse(tb["is_balanced"])
        self.assertEqual(tb["difference"], Decimal("100.00"))

    def test_accounts_without_activity_are_hidden(self):
        rows = [
            row("1000", "Cash", "Asset", "0.00", "0.00"),
            row("1010", "Bank", "Asset", "50.00", "0.00"),
            row("3000", "Equity", "Equity", "0.00", "50.00"),
        ]
        tb = compute_trial_balance(rows)
        self.assertEqual([l["code"] for l in tb["lines"]], ["1010", "3000"])

    def test_account_with_equal_debit_and_credit_shows_zero_balance(self):
        rows = [row("1100", "Receivables", "Asset", "700.00", "700.00")]
        tb = compute_trial_balance(rows)
        self.assertEqual(len(tb["lines"]), 1)
        self.assertEqual(tb["lines"][0]["debit_balance"], Decimal("0.00"))
        self.assertEqual(tb["lines"][0]["credit_balance"], Decimal("0.00"))
        self.assertEqual(tb["total_debits"], Decimal("700.00"))
        self.assertEqual(tb["total_credits"], Decimal("700.00"))

    def test_no_rounding_drift_with_many_small_amounts(self):
        # 0.10 added 1000 times must be exactly 100.00 (floats would drift).
        rows = [
            row("1010", "Bank", "Asset", "0.10", "0.00", account_id=i)
            for i in range(1000)
        ]
        rows.append(row("3000", "Equity", "Equity", "0.00", "100.00", account_id=2000))
        tb = compute_trial_balance(rows)
        self.assertEqual(tb["debit_balance"], Decimal("100.00"))
        self.assertTrue(tb["is_balanced"])

    def test_empty_organization(self):
        tb = compute_trial_balance([])
        self.assertEqual(tb["lines"], [])
        self.assertTrue(tb["is_balanced"])


if __name__ == "__main__":
    unittest.main()
