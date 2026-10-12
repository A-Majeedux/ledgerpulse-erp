"""
core/money.py

LedgerPulse Monetary Precision & Decimal Utility.
Provides safe Decimal conversion and commercial ROUND_HALF_UP rounding
for monetary calculations, journal entries, taxes, WHT, and invoices.
"""
from decimal import Decimal, ROUND_HALF_UP

Q2 = Decimal("0.01")


def to_decimal(value):
    """
    Safely converts a monetary or numeric input to Decimal using string conversion
    to avoid binary floating-point representation drift.
    Returns Decimal('0') for None or empty strings.
    Raises ValueError for invalid non-empty inputs.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    
    val_str = str(value).strip()
    if not val_str:
        return Decimal("0")
    try:
        return Decimal(val_str)
    except Exception as e:
        raise ValueError(f"Invalid monetary or numeric input: {value}") from e


def money_round(value):
    """
    Rounds a numeric/Decimal value to 2 decimal places using ROUND_HALF_UP.
    Returns a normalized Decimal object.
    """
    return to_decimal(value).quantize(Q2, rounding=ROUND_HALF_UP)
