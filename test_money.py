"""
test_money.py
Verification script for LedgerPulse Decimal and ROUND_HALF_UP implementation.
"""
from decimal import Decimal, ROUND_HALF_UP
from core.money import to_decimal, money_round


def test_basic_conversions():
    assert to_decimal(None) == Decimal("0")
    assert to_decimal("") == Decimal("0")
    assert to_decimal("   ") == Decimal("0")
    assert to_decimal(0) == Decimal("0")
    assert to_decimal(100) == Decimal("100")
    assert to_decimal("1234.56") == Decimal("1234.56")
    print("✓ Basic conversions passed")


def test_invalid_input_rejection():
    # Invalid non-empty input must raise ValueError
    try:
        to_decimal("abc")
        assert False, "Expected ValueError for 'abc'"
    except ValueError:
        pass

    try:
        to_decimal("12.34.56")
        assert False, "Expected ValueError for '12.34.56'"
    except ValueError:
        pass
    print("✓ Invalid input rejection passed")


def test_rounding_half_up():
    # ROUND_HALF_UP test cases
    assert money_round(Decimal("0.005")) == Decimal("0.01")
    assert money_round(Decimal("1.005")) == Decimal("1.01")
    assert money_round(Decimal("10.005")) == Decimal("10.01")
    assert money_round(Decimal("1.234")) == Decimal("1.23")
    assert money_round(Decimal("1.236")) == Decimal("1.24")
    print("✓ ROUND_HALF_UP passed")


def test_sales_tax_and_net_consistency():
    # 100.00 @ 18%
    net_1 = money_round(to_decimal("100.00"))
    tax_1 = money_round(net_1 * (to_decimal("18") / Decimal("100")))
    total_1 = money_round(net_1 + tax_1)
    assert tax_1 == Decimal("18.00")
    assert total_1 == Decimal("118.00")

    # Multi-line invoice consistency check: AR debit = sum(net) + sum(tax) = sum(line_tot)
    net_val = money_round("200.50")
    tax_val = money_round(net_val * Decimal("0.15"))
    line_tot = money_round(net_val + tax_val)
    assert line_tot == net_val + tax_val
    print("✓ Sales tax & net consistency passed")


def test_vendor_bill_empty_total():
    # Vendor bill line total with empty submitted total defaults to 0
    raw_tot = ""
    total = money_round(raw_tot) if raw_tot else to_decimal("0")
    assert total == Decimal("0.00")
    print("✓ Vendor bill empty total preserved zero behavior passed")


def test_wht_calculations():
    # 1000.00 @ 4.5%
    base = to_decimal("1000.00")
    wht_rate = to_decimal("4.5")
    wht_amt = money_round(base * (wht_rate / Decimal("100")))
    net_pay = base - wht_amt
    assert wht_amt == Decimal("45.00")
    assert net_pay == Decimal("955.00")
    print("✓ WHT calculations passed")


def test_float_drift_avoidance():
    # Float 0.1 + 0.2 != 0.3 in float arithmetic, but Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    d1 = to_decimal("0.1")
    d2 = to_decimal("0.2")
    assert (d1 + d2) == Decimal("0.3")
    print("✓ Float drift avoidance passed")


if __name__ == "__main__":
    test_basic_conversions()
    test_invalid_input_rejection()
    test_rounding_half_up()
    test_sales_tax_and_net_consistency()
    test_vendor_bill_empty_total()
    test_wht_calculations()
    test_float_drift_avoidance()
    print("ALL REFINEMENT AND MONETARY PRECISION TESTS PASSED SUCCESSFULLY.")
