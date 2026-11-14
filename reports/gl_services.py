"""
reports/gl_services.py

General Ledger logic for LedgerPulse.

The General Ledger shows every transaction of ONE account, oldest first,
with a running balance. It answers "what exactly makes up this number on the
Trial Balance?".

Balance convention: balance = total debits - total credits.
  positive -> Dr (debit balance)      negative -> Cr (credit balance)

Rules followed (see PROJECT.md): Decimal money, every query filtered by
organization_id, only Posted entries unless drafts are requested.
"""
from decimal import Decimal

from core.money import to_decimal, money_round

ZERO = Decimal("0.00")


def _status_list(include_drafts):
    return ["Posted", "Draft"] if include_drafts else ["Posted"]


def fetch_accounts(cursor, org_id):
    """All accounts of this organization, for the dropdown: (id, code, name)."""
    cursor.execute(
        """
        SELECT account_id, code, name
        FROM chart_of_accounts
        WHERE organization_id = %s
        ORDER BY code
        """,
        (org_id,),
    )
    return cursor.fetchall()


def fetch_account(cursor, org_id, account_id):
    """One account of this organization, or None: (id, code, name, type)."""
    cursor.execute(
        """
        SELECT account_id, code, name, account_type
        FROM chart_of_accounts
        WHERE account_id = %s AND organization_id = %s
        """,
        (account_id, org_id),
    )
    return cursor.fetchone()


def fetch_opening_totals(cursor, org_id, account_id, from_date, include_drafts):
    """
    Debit and credit totals of everything BEFORE from_date.
    Returns (total_debit, total_credit). With no from_date the opening is zero.
    """
    if not from_date:
        return ZERO, ZERO
    cursor.execute(
        """
        SELECT COALESCE(SUM(jl.debit), 0), COALESCE(SUM(jl.credit), 0)
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts a ON a.account_id = jl.account_id
        WHERE a.organization_id = %s
          AND jh.organization_id = %s
          AND jl.account_id = %s
          AND jh.status = ANY(%s)
          AND jh.accounting_date < %s
        """,
        (org_id, org_id, account_id, _status_list(include_drafts), from_date),
    )
    debit, credit = cursor.fetchone()
    return to_decimal(debit), to_decimal(credit)


def fetch_ledger_lines(cursor, org_id, account_id, from_date, to_date, include_drafts):
    """
    Every line of the account in the period, oldest first.
    Returns: (journal_header_id, accounting_date, reference, journal_name,
              status, label, partner_name, debit, credit)
    """
    filters = [
        "a.organization_id = %s",
        "jh.organization_id = %s",
        "jl.account_id = %s",
        "jh.status = ANY(%s)",
    ]
    params = [org_id, org_id, account_id, _status_list(include_drafts)]
    if from_date:
        filters.append("jh.accounting_date >= %s")
        params.append(from_date)
    if to_date:
        filters.append("jh.accounting_date <= %s")
        params.append(to_date)

    cursor.execute(
        f"""
        SELECT jh.journal_header_id, jh.accounting_date, jh.reference,
               jh.journal_name, jh.status, jl.label, jl.partner_name,
               jl.debit, jl.credit
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts a ON a.account_id = jl.account_id
        WHERE {" AND ".join(filters)}
        ORDER BY jh.accounting_date, jh.journal_header_id,
                 jl.line_order, jl.journal_line_id
        """,
        params,
    )
    return cursor.fetchall()


def compute_general_ledger(opening_debit, opening_credit, rows):
    """
    Adds a running balance to each line.

    rows: iterable of (journal_header_id, accounting_date, reference,
                       journal_name, status, label, partner_name, debit, credit)

    Returns a dict:
      opening          opening balance (debit positive, credit negative)
      lines            list of dicts, each with its running 'balance'
      total_debit      sum of debits in the period
      total_credit     sum of credits in the period
      closing          opening + total_debit - total_credit
    """
    opening = money_round(to_decimal(opening_debit)) - money_round(to_decimal(opening_credit))
    running = opening
    total_debit = ZERO
    total_credit = ZERO
    lines = []

    for (header_id, acc_date, reference, journal_name, status,
         label, partner, debit, credit) in rows:
        debit = money_round(to_decimal(debit))
        credit = money_round(to_decimal(credit))
        running = running + debit - credit
        total_debit += debit
        total_credit += credit
        lines.append({
            "journal_header_id": header_id,
            "date": acc_date,
            "reference": reference,
            "journal_name": journal_name,
            "status": status,
            "label": label,
            "partner": partner,
            "debit": debit,
            "credit": credit,
            "balance": running,
        })

    return {
        "opening": opening,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "closing": running,
    }
