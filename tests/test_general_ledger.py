"""
tests/test_general_ledger.py

Tests for the General Ledger running-balance maths. No database needed.
"""
import unittest
from datetime import date
from decimal import Decimal

from reports.gl_services import compute_general_ledger


def line(day, debit, credit, header_id=1, ref="REF", status="Posted"):
    return (header_id, date(2026, 11, day), ref, "Journal", status,
            "label", None, Decimal(debit), Decimal(credit))


class GeneralLedgerTests(unittest.TestCase):

    def test_running_balance_builds_line_by_line(self):
        rows = [line(1, "1000.00", "0.00"),
                line(2, "0.00", "300.00"),
                line(3, "50.00", "0.00")]
        gl = compute_general_ledger(Decimal("0"), Decimal("0"), rows)
        self.assertEqual([l["balance"] for l in gl["lines"]],
                         [Decimal("1000.00"), Decimal("700.00"), Decimal("750.00")])
        self.assertEqual(gl["closing"], Decimal("750.00"))
        self.assertEqual(gl["total_debit"], Decimal("1050.00"))
        self.assertEqual(gl["total_credit"], Decimal("300.00"))

    def test_opening_balance_carries_into_first_line(self):
        rows = [line(5, "0.00", "200.00")]
        gl = compute_general_ledger(Decimal("500.00"), Decimal("100.00"), rows)
        self.assertEqual(gl["opening"], Decimal("400.00"))
        self.assertEqual(gl["lines"][0]["balance"], Decimal("200.00"))
        self.assertEqual(gl["closing"], Decimal("200.00"))

    def test_credit_balance_is_negative(self):
        rows = [line(1, "0.00", "900.00")]
        gl = compute_general_ledger(Decimal("0"), Decimal("0"), rows)
        self.assertEqual(gl["closing"], Decimal("-900.00"))

    def test_closing_equals_opening_plus_movements(self):
        rows = [line(1, "10.10", "0.00"), line(2, "0.00", "3.03"), line(3, "7.07", "0.00")]
        gl = compute_general_ledger(Decimal("20.00"), Decimal("5.00"), rows)
        self.assertEqual(
            gl["closing"],
            gl["opening"] + gl["total_debit"] - gl["total_credit"],
        )
        self.assertEqual(gl["closing"], Decimal("29.14"))

    def test_no_activity_shows_only_opening(self):
        gl = compute_general_ledger(Decimal("250.00"), Decimal("0.00"), [])
        self.assertEqual(gl["lines"], [])
        self.assertEqual(gl["closing"], Decimal("250.00"))

    def test_no_rounding_drift(self):
        rows = [line(1, "0.10", "0.00", header_id=i) for i in range(1000)]
        gl = compute_general_ledger(Decimal("0"), Decimal("0"), rows)
        self.assertEqual(gl["closing"], Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
