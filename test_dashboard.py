"""
test_dashboard.py

Checks for the arithmetic behind the dashboard (dashboard/services.py) that
needs no database: which dates a period covers, what it is compared with,
when a percentage may be shown, bar heights and how money is written.

HOW TO RUN
    python3 test_dashboard.py

Whether the figures on the page match the ledger is checked separately,
against a real database, in test_workflows.py (section 9).
"""
from datetime import date, timedelta
from decimal import Decimal as D

from accounting.schema import CASH_AND_BANK_KINDS
from dashboard.services import (
    DEFAULT_PERIOD, PERIODS, add_months, bar_heights, compare, fiscal_year_label, format_day,
    format_long_day, format_money, format_percent, format_range, is_cash_or_bank_account,
    month_windows, normalize_fiscal_month, plain_amount, resolve_period,
)


def dates(period):
    return (period.start, period.end, period.compare_start, period.compare_end)


def test_add_months():
    assert add_months(date(2026, 11, 13), -1) == date(2026, 10, 13)
    assert add_months(date(2026, 1, 15), -1) == date(2025, 12, 15)          # back over a year end
    assert add_months(date(2025, 12, 15), 1) == date(2026, 1, 15)           # forward over a year end
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)           # February is shorter
    assert add_months(date(2028, 3, 31), -1) == date(2028, 2, 29)           # ... 29 days in a leap year
    assert add_months(date(2028, 2, 29), -12) == date(2027, 2, 28)          # 29 Feb, one year earlier
    assert add_months(date(2026, 5, 31), -3) == date(2026, 2, 28)
    assert add_months(date(2026, 7, 1), -12) == date(2025, 7, 1)
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    print("✓ Month arithmetic (short months, leap years, year ends) passed")


def test_periods_on_known_dates():
    # --- this month: 1st .. today, against the SAME days of the month before
    assert dates(resolve_period("month", date(2026, 11, 13))) == (
        date(2026, 11, 1), date(2026, 11, 13), date(2026, 10, 1), date(2026, 10, 13))
    assert dates(resolve_period("month", date(2026, 11, 1))) == (          # first day of a month
        date(2026, 11, 1), date(2026, 11, 1), date(2026, 10, 1), date(2026, 10, 1))
    assert dates(resolve_period("month", date(2026, 1, 15))) == (          # January looks back into last year
        date(2026, 1, 1), date(2026, 1, 15), date(2025, 12, 1), date(2025, 12, 15))
    assert dates(resolve_period("month", date(2026, 3, 31))) == (          # 31 March vs all 28 days of February
        date(2026, 3, 1), date(2026, 3, 31), date(2026, 2, 1), date(2026, 2, 28))
    assert dates(resolve_period("month", date(2028, 3, 30))) == (          # leap year: February has 29
        date(2028, 3, 1), date(2028, 3, 30), date(2028, 2, 1), date(2028, 2, 29))

    # --- last month: the whole month, against the whole month before it
    assert dates(resolve_period("last_month", date(2026, 11, 13))) == (
        date(2026, 10, 1), date(2026, 10, 31), date(2026, 9, 1), date(2026, 9, 30))
    assert dates(resolve_period("last_month", date(2026, 1, 10))) == (
        date(2025, 12, 1), date(2025, 12, 31), date(2025, 11, 1), date(2025, 11, 30))
    assert dates(resolve_period("last_month", date(2026, 3, 5))) == (      # February vs the whole of January
        date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 31))
    assert dates(resolve_period("last_month", date(2028, 3, 31))) == (
        date(2028, 2, 1), date(2028, 2, 29), date(2028, 1, 1), date(2028, 1, 31))

    # --- this quarter: follows the fiscal year (July start: Jul-Sep, Oct-Dec, Jan-Mar, Apr-Jun)
    assert dates(resolve_period("quarter", date(2026, 11, 13), 7)) == (
        date(2026, 10, 1), date(2026, 11, 13), date(2026, 7, 1), date(2026, 8, 13))
    assert dates(resolve_period("quarter", date(2026, 7, 1), 7)) == (      # first day of the fiscal year
        date(2026, 7, 1), date(2026, 7, 1), date(2026, 4, 1), date(2026, 4, 1))
    assert dates(resolve_period("quarter", date(2026, 5, 31), 7)) == (     # 31 May, three months back is 28 Feb
        date(2026, 4, 1), date(2026, 5, 31), date(2026, 1, 1), date(2026, 2, 28))
    assert dates(resolve_period("quarter", date(2026, 2, 28), 1)) == (     # calendar-year company
        date(2026, 1, 1), date(2026, 2, 28), date(2025, 10, 1), date(2025, 11, 28))
    assert dates(resolve_period("quarter", date(2026, 11, 13), 4)) == (    # April start: Apr-Jun, Jul-Sep, Oct-Dec
        date(2026, 10, 1), date(2026, 11, 13), date(2026, 7, 1), date(2026, 8, 13))
    assert dates(resolve_period("quarter", date(2026, 11, 13), 9)) == (    # September start: Sep-Nov
        date(2026, 9, 1), date(2026, 11, 13), date(2026, 6, 1), date(2026, 8, 13))

    # --- this fiscal year: start of the year .. today, against the same span a year earlier
    assert dates(resolve_period("fiscal_year", date(2026, 11, 13), 7)) == (
        date(2026, 7, 1), date(2026, 11, 13), date(2025, 7, 1), date(2025, 11, 13))
    assert dates(resolve_period("fiscal_year", date(2026, 3, 10), 7)) == (  # before July: the year began last July
        date(2025, 7, 1), date(2026, 3, 10), date(2024, 7, 1), date(2025, 3, 10))
    assert dates(resolve_period("fiscal_year", date(2026, 6, 30), 7)) == (  # last day of the fiscal year
        date(2025, 7, 1), date(2026, 6, 30), date(2024, 7, 1), date(2025, 6, 30))
    assert dates(resolve_period("fiscal_year", date(2028, 2, 29), 7)) == (  # 29 Feb vs 28 Feb the year before
        date(2027, 7, 1), date(2028, 2, 29), date(2026, 7, 1), date(2027, 2, 28))
    assert dates(resolve_period("fiscal_year", date(2026, 11, 13), 1)) == (
        date(2026, 1, 1), date(2026, 11, 13), date(2025, 1, 1), date(2025, 11, 13))

    # --- anything unexpected falls back to something safe
    this_month = dates(resolve_period("month", date(2026, 11, 13)))
    for strange in (None, "", "nonsense", "MONTH", "year", "1; DROP TABLE", 7):
        period = resolve_period(strange, date(2026, 11, 13))
        assert period.key == DEFAULT_PERIOD and dates(period) == this_month, strange
    july = dates(resolve_period("fiscal_year", date(2026, 11, 13), 7))
    for strange in (None, 0, 13, -1, "x", "", 7.9):
        if strange == 7.9:
            assert normalize_fiscal_month(strange) == 7
        assert dates(resolve_period("fiscal_year", date(2026, 11, 13), strange)) == july, strange
    assert normalize_fiscal_month("4") == 4 and normalize_fiscal_month(12) == 12
    print("✓ Period dates on known days (month, last month, quarter, fiscal year) passed")


def test_periods_on_every_day():
    """The rules that must hold on ANY day, checked for every day of eight years."""
    day, last, checked = date(2023, 1, 1), date(2030, 12, 31), 0
    keys = [key for key, _ in PERIODS]
    while day <= last:
        for fiscal_month in range(1, 13):
            for key in keys:
                p = resolve_period(key, day, fiscal_month)
                where = f"{key} on {day}, fiscal year from month {fiscal_month}"
                assert p.start.day == 1 and p.compare_start.day == 1, where
                assert p.start <= p.end <= day, where                      # never looks into the future
                assert p.compare_start <= p.compare_end < p.start, where   # compared with EARLIER days only
                assert p.opening_date == p.start - timedelta(days=1), where
                if key == "last_month":
                    assert not p.to_date
                    assert p.end + timedelta(days=1) == day.replace(day=1), where          # ends the day before this month
                    assert p.compare_end + timedelta(days=1) == p.start, where            # the whole month before
                    assert add_months(p.compare_start, 1) == p.start, where
                else:
                    assert p.to_date and p.end == day, where
                    months_back = {"month": 1, "quarter": 3, "fiscal_year": 12}[key]
                    assert add_months(p.compare_start, months_back) == p.start, where
                    # like for like: the comparison is never more than 3 days longer or shorter
                    assert abs((p.compare_end - p.compare_start).days - (p.end - p.start).days) <= 3, where
                    # ... and stops on the same day-of-month, unless that month is too short for it
                    assert p.compare_end.day == p.end.day or (
                        p.compare_end.day < p.end.day
                        and (p.compare_end + timedelta(days=1)).day == 1), where
                if key == "month":
                    assert (p.start.year, p.start.month) == (day.year, day.month), where
                if key == "quarter":
                    assert (p.start.month - fiscal_month) % 3 == 0, where
                    assert p.start > add_months(day, -3), where
                if key == "fiscal_year":
                    assert p.start.month == fiscal_month, where
                    assert p.start > add_months(day, -12), where
                checked += 1
        day += timedelta(days=1)
    print(f"✓ Period rules hold on every day from 2023 to 2030 ({checked:,} combinations) passed")


def test_month_windows():
    windows = month_windows(date(2026, 11, 13))
    assert windows == [
        (date(2026, 6, 1), date(2026, 6, 30)), (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 13)),   # stops at "today"
    ]
    windows = month_windows(date(2026, 2, 28))                               # crosses a year end
    assert windows[0] == (date(2025, 9, 1), date(2025, 9, 30))
    assert windows[-1] == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_windows(date(2028, 2, 29))[-1] == (date(2028, 2, 1), date(2028, 2, 29))
    for end in (date(2026, 1, 1), date(2026, 3, 31), date(2027, 12, 15)):
        windows = month_windows(end)
        assert len(windows) == 6 and windows[-1][1] == end
        for (start, finish), (next_start, _) in zip(windows, windows[1:]):
            assert finish + timedelta(days=1) == next_start                  # no gap, no overlap
    print("✓ Six-month windows for the charts passed")


def test_a_percentage_is_never_invented():
    # a real, positive figure to compare with -> a percentage, rounded half-up to one place
    assert compare(D("150000"), D("120000")) == {"change": D("30000"), "percent": D("25.0"), "direction": "up"}
    assert compare(D("90"), D("120"))["percent"] == D("-25.0") and compare(D("90"), D("120"))["direction"] == "down"
    assert compare(D("40000"), D("30000"))["percent"] == D("33.3")
    assert compare(D("100.25"), D("100.00"))["percent"] == D("0.3")          # 0.25 rounds UP, not to even
    assert compare(D("0"), D("500"))["percent"] == D("-100.0")               # fell to nothing: a real -100%
    assert compare(D("-50"), D("200"))["percent"] == D("-125.0")
    assert compare(D("100.01"), D("100000.00"))["percent"] == D("-99.9")

    # NOTHING to compare with: the reference app's "Rs0 revenue, +12.8%" must be impossible
    for current in (D("0"), D("0.00"), D("150000"), D("-20")):
        assert compare(current, D("0"))["percent"] is None, current
        assert compare(current, D("0.00"))["percent"] is None, current
    assert compare(D("0"), D("0")) == {"change": D("0"), "percent": None, "direction": "flat"}
    assert compare(D("150000"), D("0"))["direction"] == "up"

    # a LOSS (or negative figure) to compare with: a percentage of it would mislead
    assert compare(D("50"), D("-100")) == {"change": D("150"), "percent": None, "direction": "up"}
    assert compare(D("-300"), D("-100"))["percent"] is None

    # no movement: no percentage (the page says "Same as ..." instead of "0.0%")
    assert compare(D("777.77"), D("777.77")) == {"change": D("0.00"), "percent": None, "direction": "flat"}

    assert format_percent(D("25.0")) == "25.0%" and format_percent(D("-25.0")) == "25.0%"   # the arrow shows direction
    assert format_percent(D("1234.5")) == "1,234.5%"
    assert format_percent(D("0.0")) == "<0.1%"                                # moved, but by less than 0.05%
    print("✓ Percentages: shown only against a real positive figure passed")


def test_bar_heights():
    assert bar_heights([]) == []
    assert bar_heights([D("0"), D("0"), D("0")]) == [0, 0, 0]                # nothing happened: no bars at all
    assert bar_heights([D("50"), D("100"), D("0"), D("25")]) == [50, 100, 0, 25]
    assert bar_heights([D("-100"), D("50")]) == [100, 50]                    # by size; the sign only changes the colour
    assert bar_heights([D("1"), D("1000000")]) == [6, 100]                   # tiny, but still visible
    assert bar_heights([D("1"), D("1000000")], floor=10) == [10, 100]
    assert bar_heights([D("0.01")]) == [100]
    assert bar_heights([D("200000"), D("150000"), D("33300"), D("44000")]) == [100, 75, 17, 22]
    for values in ([D("3"), D("-7"), D("0")], [D("0.01"), D("99999999999.99")]):
        assert all(0 <= height <= 100 for height in bar_heights(values))
    print("✓ Bar heights passed")


def test_money_formatting():
    assert format_money(D("1250000")) == "1,250,000.00"
    assert format_money(D("0")) == "0.00" and format_money(None) == "0.00"
    assert format_money(D("-0.00")) == "0.00" and format_money(D("-0.001")) == "0.00"       # never "-0.00"
    assert format_money(D("-5000")) == "-5,000.00"
    assert format_money(D("999.995")) == "1,000.00" and format_money(D("0.005")) == "0.01"  # half-up
    assert format_money(D("999999999999.99")) == "999,999,999,999.99"
    assert format_money(D("12.3")) == "12.30"
    assert plain_amount(D("1250000")) == "1250000.00" and plain_amount(D("-0.00")) == "0.00"
    assert plain_amount(D("-12.345")) == "-12.35" and plain_amount(None) == "0.00"
    assert D(plain_amount(D("1069000.00"))) == D("1069000")
    print("✓ Money formatting passed")


def test_labels():
    assert format_day(date(2026, 11, 3)) == "3 Nov 2026"
    assert format_long_day(date(2026, 11, 13)) == "Friday, 13 November 2026"
    assert format_range(date(2026, 11, 1), date(2026, 11, 13)) == "1 – 13 Nov 2026"
    assert format_range(date(2026, 10, 1), date(2026, 11, 13)) == "1 Oct – 13 Nov 2026"
    assert format_range(date(2025, 7, 1), date(2026, 3, 10)) == "1 Jul 2025 – 10 Mar 2026"
    assert format_range(date(2026, 11, 1), date(2026, 11, 1)) == "1 Nov 2026"
    assert fiscal_year_label(date(2026, 7, 1)) == "FY 2026-27"
    assert fiscal_year_label(date(2099, 7, 1)) == "FY 2099-00"
    assert fiscal_year_label(date(2026, 1, 1)) == "FY 2026"

    month = resolve_period("month", date(2026, 11, 13))
    assert (month.label, month.detail) == ("This month", None)
    assert (month.range_label, month.compare_label) == ("1 – 13 Nov 2026", "1 – 13 Oct 2026")
    assert month.opening_date == date(2026, 10, 31)
    quarter = resolve_period("quarter", date(2026, 11, 13), 7)
    assert (quarter.label, quarter.detail) == ("This quarter", "Q2 · FY 2026-27")
    assert resolve_period("quarter", date(2026, 7, 1), 7).detail == "Q1 · FY 2026-27"
    assert resolve_period("quarter", date(2026, 6, 30), 7).detail == "Q4 · FY 2025-26"
    assert resolve_period("quarter", date(2026, 11, 13), 1).detail == "Q4 · FY 2026"
    year = resolve_period("fiscal_year", date(2026, 3, 10), 7)
    assert (year.label, year.detail, year.range_label) == ("This fiscal year", "FY 2025-26", "1 Jul 2025 – 10 Mar 2026")
    assert resolve_period("last_month", date(2026, 11, 13)).range_label == "1 – 31 Oct 2026"
    print("✓ Date and period labels passed")


def test_cash_and_bank_rule():
    # An account counts as cash & bank when its KIND on the Chart of Accounts
    # is Bank or Cash - whatever its code is.
    for account_kind in ("Bank", "Cash"):
        assert is_cash_or_bank_account(account_kind), account_kind
    # every other kind is not money in hand: receivables, payables, the
    # accounts of directors and employees, and ordinary accounts
    for account_kind in ("General", "Trade receivable", "Trade payable",
                         "Non-trading receivable", "Non-trading payable"):
        assert not is_cash_or_bank_account(account_kind), account_kind
    # ...and nothing that merely looks like the two words
    for account_kind in ("bank", "CASH", " Bank", "Bank ", "Banking", "", None, "1010"):
        assert not is_cash_or_bank_account(account_kind), account_kind
    # the two kinds here are exactly the kinds the payment screens offer
    assert set(CASH_AND_BANK_KINDS) == {"Bank", "Cash"}
    assert CASH_AND_BANK_KINDS == ("Bank", "Cash")       # a tuple: it is passed to SQL "IN %s"
    print("✓ Which accounts count as cash & bank passed")


if __name__ == "__main__":
    test_add_months()
    test_periods_on_known_dates()
    test_periods_on_every_day()
    test_month_windows()
    test_a_percentage_is_never_invented()
    test_bar_heights()
    test_money_formatting()
    test_labels()
    test_cash_and_bank_rule()
    print("ALL DASHBOARD ARITHMETIC TESTS PASSED SUCCESSFULLY.")
