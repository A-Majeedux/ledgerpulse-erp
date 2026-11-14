"""
dashboard/services.py

Works out every number the dashboard shows. dashboard/routes.py only asks
this file for the figures and hands them to the template.

THE RULES EVERY FIGURE FOLLOWS (the same rules a Trial Balance follows):

  1. Only POSTED journal entries count. A draft invoice, bill or journal
     entry is not in the books yet, so it is never part of a figure. Drafts
     are counted separately and shown under "Needs attention".
  2. Only the ACTIVE organization's entries count. Every query below filters
     on organization_id, so one company can never see another's numbers.
  3. Dates are ACCOUNTING dates. A balance "as at 13 Nov" is everything dated
     up to and including 13 Nov. Revenue "for 1 - 13 Nov" is only what is
     dated inside that range. An entry dated in the future is left out until
     its date arrives (the dashboard says how many such entries exist).
  4. All arithmetic is Decimal. The database adds up NUMERIC columns and
     Python never turns them into floats.
  5. Nothing is shown that was not worked out from the ledger. In
     particular, a percentage change is only shown when there is a real,
     positive figure to compare with - see compare(). (The design reference
     this dashboard is modelled on showed "Rs0 revenue, +12.8%"; that can
     not happen here.)

This file only READS. It never inserts, updates or deletes anything.
Tables read: organizations, chart_of_accounts, journal_headers,
journal_lines (the ledger), and - only to count drafts waiting to be
posted - sales_invoices, sales_invoice_lines, vendor_bills,
vendor_bill_lines.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from accounting.routes import SOURCE_DOCUMENTS
from accounting.schema import (
    ACCOUNT_TYPES, CASH_AND_BANK_KINDS, TRADE_RECEIVABLES_CODE, TRADE_PAYABLES_CODE,
)
from core.money import money_round

ZERO = Decimal("0.00")

# ---------------------------------------------------------------------
# Which accounts feed which figure
# ---------------------------------------------------------------------
# Control accounts are found by the SAME account codes the posting code
# uses, so the dashboard and the vouchers can never disagree:
#   sales/routes.py and customers/routes.py post receivables to 1100
#   vendors/routes.py posts payables to 2000 and tax withheld to 2100 / 2110
#   customers/routes.py posts tax withheld BY customers to 1150 / 1160
#   sales/routes.py posts output sales tax to 2200
# (1100 and 2000 themselves are defined once, in accounting/schema.py:
#  TRADE_RECEIVABLES_CODE and TRADE_PAYABLES_CODE, imported above.)
WHT_PAYABLE_CODES = ("2100", "2110")
WHT_RECEIVABLE_CODES = ("1150", "1160")
OUTPUT_TAX_CODE = "2200"

# "Cash & bank" is every account the Chart of Accounts marks as Bank or
# Cash (its Kind - accounting/schema.py). They are the same accounts a
# vendor payment can be paid from and a customer receipt deposited to, so
# the dashboard and the payment screens can never disagree about what
# counts as money. The account's code plays no part: a bank account can
# carry any code.
# An INACTIVE bank or cash account is still counted - switching an account
# off hides it from new documents, it does not make its balance disappear.
# The dashboard lists exactly which accounts it counted, so the rule is
# never hidden from the person reading the number.
CASH_AND_BANK_RULE = "Accounts marked Bank or Cash on the Chart of Accounts"
CASH_AND_BANK_NOTE = "Accounts marked Bank or Cash"     # the same rule, short enough for the stat card


def is_cash_or_bank_account(account_kind):
    return account_kind in CASH_AND_BANK_KINDS


# How journal_headers.source_type is worded for a reader.
SOURCE_LABELS = {
    "Manual": "Manual entry",
    "VendorBill": "Vendor bill",
    "SalesInvoice": "Sales invoice",
    "VendorPayment": "Vendor payment",
    "CustomerReceipt": "Customer receipt",
}

TREND_MONTHS = 6          # months shown in the sparklines and the trend chart
TOP_PARTNERS = 5          # rows in "Customers owe you" / "You owe vendors"
RECENT_ENTRIES = 8        # rows in "Recent journal entries"


# ---------------------------------------------------------------------
# Dates and periods
# ---------------------------------------------------------------------
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LONG_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                    "September", "October", "November", "December")

DEFAULT_FISCAL_YEAR_START_MONTH = 7   # July - the same default organizations/schema.py gives a company

# The period buttons on the dashboard, in the order they are shown.
PERIODS = (
    ("month", "This month"),
    ("last_month", "Last month"),
    ("quarter", "This quarter"),
    ("fiscal_year", "This fiscal year"),
)
DEFAULT_PERIOD = "month"


def first_of_month(day):
    return day.replace(day=1)


def add_months(day, months):
    """
    The same day-of-month `months` later (or earlier, if negative). When
    the target month is shorter, the last day of that month is used:
    31 Mar minus one month is 28 Feb (29 Feb in a leap year).
    """
    index = day.year * 12 + (day.month - 1) + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def format_day(day):
    return f"{day.day} {MONTH_NAMES[day.month - 1]} {day.year}"


def format_long_day(day):
    return f"{WEEKDAY_NAMES[day.weekday()]}, {day.day} {LONG_MONTH_NAMES[day.month - 1]} {day.year}"


def format_range(start, end):
    """'1 – 13 Nov 2026', '1 Oct – 13 Nov 2026' or '1 Jul 2025 – 30 Jun 2026'."""
    if start == end:
        return format_day(end)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} – {format_day(end)}"
    if start.year == end.year:
        return f"{start.day} {MONTH_NAMES[start.month - 1]} – {format_day(end)}"
    return f"{format_day(start)} – {format_day(end)}"


def normalize_fiscal_month(value):
    """A usable fiscal-year start month (1-12); July when the stored value is missing or wrong."""
    try:
        month = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FISCAL_YEAR_START_MONTH
    return month if 1 <= month <= 12 else DEFAULT_FISCAL_YEAR_START_MONTH


def fiscal_year_label(fiscal_year_start):
    """'FY 2026-27' for a year starting 1 Jul 2026; 'FY 2026' for a calendar year."""
    if fiscal_year_start.month == 1:
        return f"FY {fiscal_year_start.year}"
    return f"FY {fiscal_year_start.year}-{(fiscal_year_start.year + 1) % 100:02d}"


@dataclass(frozen=True)
class Period:
    key: str                  # "month", "last_month", "quarter" or "fiscal_year"
    label: str                # "This month"
    detail: Optional[str]     # "Q2 · FY 2026-27", or None
    start: date               # first day counted
    end: date                 # last day counted
    compare_start: date       # the period it is compared with ...
    compare_end: date         # ... which always ends before `start`
    to_date: bool             # True while the period is still running (it ends today)

    @property
    def range_label(self):
        return format_range(self.start, self.end)

    @property
    def compare_label(self):
        return format_range(self.compare_start, self.compare_end)

    @property
    def opening_date(self):
        """The day before the period starts - balances 'at the start' are as at this day."""
        return self.start - timedelta(days=1)


def resolve_period(key, today, fiscal_year_start_month=DEFAULT_FISCAL_YEAR_START_MONTH):
    """
    Turn a period button into exact dates.

    Every comparison is LIKE FOR LIKE. On 13 Nov, "this month" is 1 - 13 Nov
    and it is compared with 1 - 13 Oct - not with the whole of October,
    which would make every month look like a decline until its last day.

        month        1st of this month .. today        vs  same days one month earlier
        last_month   the whole previous month          vs  the whole month before it
        quarter      start of fiscal quarter .. today  vs  same span one quarter earlier
        fiscal_year  start of fiscal year .. today     vs  same span one year earlier

    An unknown key means "month". Quarters and the fiscal year follow the
    organization's fiscal_year_start_month (July by default).
    """
    labels = dict(PERIODS)
    if key not in labels:
        key = DEFAULT_PERIOD
    fiscal_month = normalize_fiscal_month(fiscal_year_start_month)
    this_month = first_of_month(today)
    months_into_fiscal_year = (today.month - fiscal_month) % 12
    fiscal_year_start = add_months(this_month, -months_into_fiscal_year)

    detail = None
    if key == "month":
        start, end, shift, whole = this_month, today, 1, False
    elif key == "last_month":
        start, end, shift, whole = add_months(this_month, -1), this_month - timedelta(days=1), 1, True
    elif key == "quarter":
        start, end, shift, whole = add_months(this_month, -(months_into_fiscal_year % 3)), today, 3, False
        detail = f"Q{months_into_fiscal_year // 3 + 1} · {fiscal_year_label(fiscal_year_start)}"
    else:  # fiscal_year
        start, end, shift, whole = fiscal_year_start, today, 12, False
        detail = fiscal_year_label(fiscal_year_start)

    compare_start = add_months(start, -shift)
    # A whole period is compared with the whole period before it. A period
    # that is still running is compared with the same number of months back.
    compare_end = (start - timedelta(days=1)) if whole else add_months(end, -shift)
    return Period(key=key, label=labels[key], detail=detail, start=start, end=end,
                  compare_start=compare_start, compare_end=compare_end, to_date=not whole)


def month_windows(end, count=TREND_MONTHS):
    """
    The `count` calendar months that finish with the month `end` falls in,
    oldest first, as (first day, last day). The last one stops AT `end`, so
    a month that is still running only counts the days that have happened.
    """
    newest = first_of_month(end)
    windows = []
    for back in range(count - 1, -1, -1):
        start = add_months(newest, -back)
        last = add_months(start, 1) - timedelta(days=1)
        windows.append((start, min(last, end)))
    return windows


# ---------------------------------------------------------------------
# Small pieces of arithmetic and wording
# ---------------------------------------------------------------------
def format_money(value):
    """1250000 -> '1,250,000.00'. Rounded half-up to 2 places; never '-0.00'."""
    amount = money_round(value)
    if amount == 0:
        amount = ZERO
    return f"{amount:,.2f}"


def plain_amount(value):
    """1250000 -> '1250000.00': the exact figure with no separators, for the
    data-* attributes of the page (the automated checks read those)."""
    amount = money_round(value)
    return str(ZERO if amount == 0 else amount)


def format_percent(percent):
    """Decimal('25.0') -> '25.0%'. The direction is shown by an arrow, so no sign here."""
    size = abs(percent)
    return "<0.1%" if size == 0 else f"{size:,.1f}%"


def compare(current, previous):
    """
    How a figure moved against the figure it is compared with.

    `percent` is None unless the earlier figure is greater than zero. With
    nothing (or a loss) to compare against, a percentage would be invented
    or misleading, so the dashboard shows the two amounts instead.
    """
    change = current - previous
    percent = None
    if previous > 0 and change != 0:
        percent = (change * 100 / previous).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    return {"change": change, "percent": percent, "direction": direction}


def bar_heights(values, floor=6):
    """
    Heights (0-100) for a row of bars, by SIZE: the largest value is 100.
    A zero value is 0 (drawn as a thin baseline, never as a fake bar); any
    other value is at least `floor` so it stays visible.
    """
    largest = max((abs(value) for value in values), default=ZERO)
    if largest == 0:
        return [0 for _ in values]
    heights = []
    for value in values:
        if value == 0:
            heights.append(0)
        else:
            exact = (abs(value) * 100 / largest).to_integral_value(rounding=ROUND_HALF_UP)
            heights.append(max(floor, min(100, int(exact))))
    return heights


def _tone(direction, good_when_up):
    """'good' / 'bad' / 'neutral' colouring for a change. good_when_up=None means 'never judge'."""
    if direction == "flat" or good_when_up is None:
        return "neutral"
    return "good" if (direction == "up") == good_when_up else "bad"


# ---------------------------------------------------------------------
# The dashboard itself
# ---------------------------------------------------------------------
def build_dashboard(cursor, organization_id, today, period_key=None):
    """
    Everything templates/dashboard/home.html shows, for ONE organization.
    Returns None when that organization does not exist.

    Signs: the ledger is read as (debit - credit) per account. Assets and
    expenses are shown that way round; liabilities, equity and revenue are
    flipped, so a normal balance is always a positive number on screen.
    """
    cursor.execute(
        "SELECT name, currency, fiscal_year_start_month FROM organizations WHERE organization_id = %s",
        (organization_id,)
    )
    row = cursor.fetchone()
    if row is None:
        return None
    organization = {
        "name": row[0],
        "currency": (row[1] or "").strip(),
        "fiscal_year_start_month": normalize_fiscal_month(row[2]),
    }
    period = resolve_period(period_key, today, organization["fiscal_year_start_month"])
    windows = month_windows(period.end)
    month_keys = [(start.year, start.month) for start, _ in windows]

    # -- the Chart of Accounts of this organization -------------------------
    cursor.execute("""
        SELECT account_id, code, name, account_type, account_kind
        FROM chart_of_accounts
        WHERE organization_id = %s
        ORDER BY code, account_id
    """, (organization_id,))
    accounts = {
        account_id: {"code": code, "name": name, "type": account_type, "kind": account_kind}
        for account_id, code, name, account_type, account_kind in cursor.fetchall()
    }

    # -- one pass over the posted ledger: three sums per account ------------
    #    opening    : everything dated before the period starts
    #    movement   : everything dated inside the period
    #    comparison : everything dated inside the comparison period
    cursor.execute("""
        SELECT l.account_id,
               COALESCE(SUM(l.debit - l.credit) FILTER (WHERE h.accounting_date < %(start)s), 0),
               COALESCE(SUM(l.debit - l.credit) FILTER (WHERE h.accounting_date >= %(start)s), 0),
               COALESCE(SUM(l.debit - l.credit) FILTER (
                   WHERE h.accounting_date BETWEEN %(compare_start)s AND %(compare_end)s), 0)
        FROM journal_lines l
        JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
        WHERE h.organization_id = %(organization_id)s
          AND h.status = 'Posted'
          AND h.accounting_date <= %(end)s
        GROUP BY l.account_id
    """, {
        "organization_id": organization_id, "start": period.start, "end": period.end,
        "compare_start": period.compare_start, "compare_end": period.compare_end,
    })
    opening, movement, comparison = {}, {}, {}
    for account_id, before, inside, compared in cursor.fetchall():
        opening[account_id], movement[account_id], comparison[account_id] = before, inside, compared

    # -- the same ledger, month by month, for the last few months -----------
    cursor.execute("""
        SELECT l.account_id,
               EXTRACT(YEAR FROM h.accounting_date)::int,
               EXTRACT(MONTH FROM h.accounting_date)::int,
               COALESCE(SUM(l.debit - l.credit), 0)
        FROM journal_lines l
        JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
        WHERE h.organization_id = %(organization_id)s
          AND h.status = 'Posted'
          AND h.accounting_date BETWEEN %(window_start)s AND %(end)s
        GROUP BY 1, 2, 3
    """, {"organization_id": organization_id, "window_start": windows[0][0], "end": period.end})
    monthly = {(account_id, (year, month)): net for account_id, year, month, net in cursor.fetchall()}

    def total(table, account_ids):
        return sum((table.get(account_id, ZERO) for account_id in account_ids), ZERO)

    def closing(account_ids):
        return total(opening, account_ids) + total(movement, account_ids)

    def monthly_flow(account_ids, sign=1):
        """What moved through these accounts in each of the months shown."""
        return [sign * sum((monthly.get((account_id, key), ZERO) for account_id in account_ids), ZERO)
                for key in month_keys]

    def monthly_balance(account_ids, sign=1):
        """The balance at the END of each month shown (the last one as at period.end)."""
        flows = monthly_flow(account_ids, sign)
        balances = [sign * closing(account_ids)]
        for flow in reversed(flows[1:]):          # walk back from the newest month
            balances.append(balances[-1] - flow)
        return list(reversed(balances))

    def ids_of_type(account_type):
        return [account_id for account_id, account in accounts.items() if account["type"] == account_type]

    def ids_of_codes(codes):
        wanted = {codes} if isinstance(codes, str) else set(codes)
        return [account_id for account_id, account in accounts.items() if account["code"] in wanted]

    assets, liabilities = ids_of_type("Asset"), ids_of_type("Liability")
    equity, revenue, expenses = ids_of_type("Equity"), ids_of_type("Revenue"), ids_of_type("Expense")
    cash = [account_id for account_id, account in accounts.items()
            if is_cash_or_bank_account(account["kind"])]
    receivables, payables = ids_of_codes(TRADE_RECEIVABLES_CODE), ids_of_codes(TRADE_PAYABLES_CODE)

    month_labels = [f"{MONTH_NAMES[start.month - 1]} {start.year}" for start, _ in windows]

    def series(values):
        heights = bar_heights(values, floor=10)
        return [{"label": month_labels[i], "value": value, "height": heights[i],
                 "negative": value < 0, "current": i == len(values) - 1}
                for i, value in enumerate(values)]

    def balance_card(key, label, note, account_ids, sign, good_when_up):
        """A point-in-time figure: the balance as at period.end, and how it moved over the period."""
        at_start, at_end = sign * total(opening, account_ids), sign * closing(account_ids)
        moved = compare(at_end, at_start)
        return {"key": key, "label": label, "note": note, "kind": "balance", "value": at_end,
                "previous": at_start, "change": moved["change"], "percent": None,
                "direction": moved["direction"], "tone": _tone(moved["direction"], good_when_up),
                "series": series(monthly_balance(account_ids, sign))}

    def flow_card(key, label, note, current, previous, values, good_when_up):
        """A for-the-period figure, against the same figure for the comparison period."""
        moved = compare(current, previous)
        return {"key": key, "label": label, "note": note, "kind": "flow", "value": current,
                "previous": previous, "change": moved["change"], "percent": moved["percent"],
                "direction": moved["direction"], "tone": _tone(moved["direction"], good_when_up),
                "series": series(values)}

    revenue_now, revenue_before = -total(movement, revenue), -total(comparison, revenue)
    expenses_now, expenses_before = total(movement, expenses), total(comparison, expenses)
    revenue_by_month, expenses_by_month = monthly_flow(revenue, -1), monthly_flow(expenses)
    profit_by_month = [earned - spent for earned, spent in zip(revenue_by_month, expenses_by_month)]

    def account_note(account_ids, fallback):
        names = [f"{accounts[a]['code']} {accounts[a]['name']}" for a in account_ids]
        return ", ".join(names) if names else fallback

    cards = {
        "cash": balance_card("cash", "Cash & bank", CASH_AND_BANK_NOTE, cash, 1, True),
        "receivables": balance_card(
            "receivables", "Trade receivables",
            account_note(receivables, f"Account {TRADE_RECEIVABLES_CODE} (not in this Chart of Accounts)"),
            receivables, 1, None),
        "payables": balance_card(
            "payables", "Trade payables",
            account_note(payables, f"Account {TRADE_PAYABLES_CODE} (not in this Chart of Accounts)"),
            payables, -1, None),
        "revenue": flow_card("revenue", "Revenue", "All Revenue accounts",
                             revenue_now, revenue_before, revenue_by_month, True),
        "expenses": flow_card("expenses", "Expenses", "All Expense accounts",
                              expenses_now, expenses_before, expenses_by_month, False),
        "profit": flow_card("profit", "Net profit", "Revenue less expenses",
                            revenue_now - expenses_now, revenue_before - expenses_before,
                            profit_by_month, True),
    }

    # -- revenue against expenses, month by month (one shared scale) --------
    peak = max((abs(value) for value in revenue_by_month + expenses_by_month), default=ZERO)
    shared = bar_heights(revenue_by_month + expenses_by_month)
    last_window_start, last_window_end = windows[-1]
    trend = {
        "peak": peak,
        "has_data": peak != 0,
        "partial_month": (add_months(last_window_start, 1) - timedelta(days=1)) != last_window_end,
        "months": [{
            "label": MONTH_NAMES[start.month - 1], "long_label": month_labels[i],
            "revenue": revenue_by_month[i], "expenses": expenses_by_month[i], "profit": profit_by_month[i],
            "revenue_height": shared[i], "expenses_height": shared[len(windows) + i],
            "current": i == len(windows) - 1,
        } for i, (start, _) in enumerate(windows)],
    }

    # -- which accounts make up "Cash & bank" -------------------------------
    cash_accounts = [{"code": accounts[a]["code"], "name": accounts[a]["name"], "balance": closing([a])}
                     for a in cash]

    # -- tax ledgers ---------------------------------------------------------
    def ledger_group(title, hint, codes, sign):
        ids = ids_of_codes(codes)
        rows = [{"code": accounts[a]["code"], "name": accounts[a]["name"], "balance": sign * closing([a])}
                for a in ids]
        return {"title": title, "hint": hint, "accounts": rows, "total": sign * closing(ids)}

    tax_groups = [
        ledger_group("WHT payable", "Tax you withheld from vendors, due to FBR", WHT_PAYABLE_CODES, -1),
        ledger_group("WHT receivable", "Tax your customers withheld from you", WHT_RECEIVABLE_CODES, 1),
        ledger_group("Output sales tax payable", "Sales tax charged on your invoices", OUTPUT_TAX_CODE, -1),
    ]

    # -- who owes, and who is owed (by partner name on posted lines) --------
    partners = {"receivable": {"rows": [], "count": 0}, "payable": {"rows": [], "count": 0}}
    control_accounts = receivables + payables
    if control_accounts:
        cursor.execute("""
            SELECT l.account_id,
                   COALESCE(NULLIF(BTRIM(l.partner_name), ''), '') AS partner,
                   SUM(l.debit - l.credit) AS balance
            FROM journal_lines l
            JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
            WHERE h.organization_id = %(organization_id)s
              AND h.status = 'Posted'
              AND h.accounting_date <= %(end)s
              AND l.account_id = ANY(%(account_ids)s)
            GROUP BY 1, 2
            HAVING SUM(l.debit - l.credit) <> 0
        """, {"organization_id": organization_id, "end": period.end, "account_ids": control_accounts})
        owed_to_us, owed_by_us = {}, {}
        for account_id, partner, balance in cursor.fetchall():
            if account_id in receivables:
                owed_to_us[partner] = owed_to_us.get(partner, ZERO) + balance
            else:
                owed_by_us[partner] = owed_by_us.get(partner, ZERO) - balance
        for side, balances in (("receivable", owed_to_us), ("payable", owed_by_us)):
            open_balances = sorted(((amount, name) for name, amount in balances.items() if amount > 0),
                                   key=lambda item: (-item[0], item[1]))
            largest = open_balances[0][0] if open_balances else ZERO
            partners[side] = {
                "count": len(open_balances),
                "rows": [{"name": name or "(no partner name)", "balance": amount,
                          "share": bar_heights([amount, largest], floor=4)[0]}
                         for amount, name in open_balances[:TOP_PARTNERS]],
            }

    # -- drafts waiting to be posted, and entries dated after today ---------
    cursor.execute("""
        SELECT
          (SELECT COUNT(*) FROM sales_invoices
            WHERE organization_id = %(organization_id)s AND status = 'Draft'),
          (SELECT COALESCE(SUM(line.total), 0) FROM sales_invoice_lines line
             JOIN sales_invoices doc ON doc.sales_invoice_id = line.sales_invoice_id
            WHERE doc.organization_id = %(organization_id)s AND doc.status = 'Draft'),
          (SELECT COUNT(*) FROM vendor_bills
            WHERE organization_id = %(organization_id)s AND status = 'Draft'),
          (SELECT COALESCE(SUM(line.total), 0) FROM vendor_bill_lines line
             JOIN vendor_bills doc ON doc.vendor_bill_id = line.vendor_bill_id
            WHERE doc.organization_id = %(organization_id)s AND doc.status = 'Draft'),
          (SELECT COUNT(*) FROM journal_headers
            WHERE organization_id = %(organization_id)s AND status = 'Draft'
              AND COALESCE(source_type, 'Manual') <> ALL(%(document_sources)s)),
          (SELECT COUNT(*) FROM journal_headers
            WHERE organization_id = %(organization_id)s AND status = 'Posted'
              AND accounting_date > %(today)s)
    """, {
        "organization_id": organization_id, "today": today,
        # A draft journal that belongs to a bill or an invoice is counted
        # with that document, not a second time as a journal entry.
        "document_sources": list(SOURCE_DOCUMENTS),
    })
    (draft_invoices, draft_invoices_total, draft_bills, draft_bills_total,
     draft_journals, future_dated) = cursor.fetchone()
    drafts = {
        "sales_invoices": {"count": draft_invoices, "total": draft_invoices_total},
        "vendor_bills": {"count": draft_bills, "total": draft_bills_total},
        "journal_entries": {"count": draft_journals},
        "count": draft_invoices + draft_bills + draft_journals,
    }

    # -- the newest journal entries (same order as the Journal Entries list) -
    cursor.execute("""
        SELECT jh.journal_header_id, jh.reference, jh.accounting_date, jh.journal_name,
               jh.status, jh.source_type, COALESCE(SUM(jl.debit), 0)
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE jh.organization_id = %s
        GROUP BY jh.journal_header_id
        ORDER BY jh.accounting_date DESC, jh.journal_header_id DESC
        LIMIT %s
    """, (organization_id, RECENT_ENTRIES))
    recent = [{
        "journal_header_id": journal_header_id, "reference": reference, "date": accounting_date,
        "journal": journal_name, "status": status,
        "source": SOURCE_LABELS.get(source_type or "Manual", source_type), "total": amount,
    } for journal_header_id, reference, accounting_date, journal_name, status, source_type, amount
        in cursor.fetchall()]

    # -- the accounting equation, as at period.end --------------------------
    # Profit that has not been closed into Retained Earnings is still part
    # of equity, so it is ADDED to the equity accounts here. Without that,
    # Assets would not equal Liabilities + Equity.
    classified = set(assets + liabilities + equity + revenue + expenses)
    stray = [account_id for account_id in set(opening) | set(movement) if account_id not in classified]
    profit_to_date = -(closing(revenue) + closing(expenses))
    position = {
        "assets": closing(assets),
        "liabilities": -closing(liabilities),
        "equity_accounts": -closing(equity),
        "profit_to_date": profit_to_date,
        "equity": -closing(equity) + profit_to_date,
        # Amounts on accounts whose type is not one of the five known types.
        # Normally zero; shown as a warning when it is not, never hidden.
        "unclassified": closing(stray),
        "unclassified_types": sorted({accounts[a]["type"] for a in stray if a in accounts}),
    }
    # Total posted debits minus total posted credits. Zero in healthy books.
    position["difference"] = closing(list(set(opening) | set(movement)))
    position["balanced"] = position["difference"] == 0

    return {
        "organization": organization,
        "today": today,
        "today_label": format_long_day(today),
        "as_at_label": format_day(period.end),
        "opening_label": format_day(period.opening_date),
        "period": period,
        "periods": [{"key": key, "label": label, "active": key == period.key} for key, label in PERIODS],
        "has_any_entry": bool(recent),
        "cards": cards,
        "trend": trend,
        "cash_accounts": cash_accounts,
        "cash_rule": CASH_AND_BANK_RULE,
        "tax_groups": tax_groups,
        "partners": partners,
        "drafts": drafts,
        "future_dated": future_dated,
        "recent": recent,
        "position": position,
        "account_types": ACCOUNT_TYPES,
    }
