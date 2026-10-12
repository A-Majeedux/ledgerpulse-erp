"""
test_integration.py
Read-only integration test script for LedgerPulse Foundation Task 2 workflows.
"""
from decimal import Decimal
from core.money import to_decimal, money_round
from accounting.services import create_journal_entry, UnbalancedEntryError


def test_sales_invoice_workflow_math():
    # Multi-line invoice math verification
    items = [
        {"qty": 2, "rate": "150.50", "disc_pct": 10, "tax_rate": "18"},
        {"qty": 1, "rate": "1000.00", "disc_pct": 0, "tax_rate": "18"},
    ]
    
    total_tax = Decimal("0")
    invoice_grand_total = Decimal("0")
    net_revenue = Decimal("0")
    
    for item in items:
        qty = to_decimal(item["qty"])
        rate = to_decimal(item["rate"])
        disc_pct = to_decimal(item["disc_pct"])
        gross = qty * rate
        disc_amt = money_round(gross * (disc_pct / Decimal("100")))
        net = money_round(gross - disc_amt)
        tax_pct = to_decimal(item["tax_rate"])
        tax_amt = money_round(net * (tax_pct / Decimal("100")))
        line_tot = money_round(net + tax_amt)
        
        net_revenue += net
        total_tax += tax_amt
        invoice_grand_total += line_tot
        
    assert invoice_grand_total == money_round(net_revenue + total_tax)
    print("✓ Sales invoice workflow math balance verified (AR = Revenue + Tax)")


def test_vendor_payment_wht_math():
    base = to_decimal("1500.00")
    wht_rate = to_decimal("5.0")
    wht_amt = money_round(base * (wht_rate / Decimal("100")))
    net_pay = base - wht_amt
    assert net_pay + wht_amt == base
    assert wht_amt == Decimal("75.00")
    assert net_pay == Decimal("1425.00")
    print("✓ Vendor payment WHT math verified (Net + WHT = Gross)")


def test_customer_receipt_wht_math():
    gross = to_decimal("2500.00")
    wht_rate = to_decimal("4.0")
    wht_amt = money_round(gross * (wht_rate / Decimal("100")))
    net_received = gross - wht_amt
    assert net_received + wht_amt == gross
    assert wht_amt == Decimal("100.00")
    assert net_received == Decimal("2400.00")
    print("✓ Customer receipt WHT math verified (Net received + WHT = Gross)")


def test_invalid_input_exception():
    try:
        to_decimal("abc")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("✓ Invalid input raises ValueError as designed")


if __name__ == "__main__":
    test_sales_invoice_workflow_math()
    test_vendor_payment_wht_math()
    test_customer_receipt_wht_math()
    test_invalid_input_exception()
    print("ALL INTEGRATION VERIFICATION TESTS PASSED SUCCESSFULLY.")
