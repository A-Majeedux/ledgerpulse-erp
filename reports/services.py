"""
reports/services.py

Trial Balance logic for LedgerPulse.

A trial balance lists every account with its total debits and credits.
If the books are correct, total debits ALWAYS equal total credits.
This is the main health check for the whole system: every module (vendors,
customers, sales, journal entries) posts into the same journal tables, so one
wrong posting anywhere shows up here.

Rules followed (see PROJECT.md):
  - Money is Decimal, never float.
  - Every query filters by organization_id.
  - By default only POSTED entries count. Drafts can be included to preview.
"""
from decimal import Decimal

from core.money import to_decimal, money_round

ZERO = Decimal("0.00")


def _status_list(include_drafts):
    return ["Posted", "Draft"] if include_drafts else ["Posted"]


def fetch_trial_balance_rows(cursor, org_id, as_at=None, include_drafts=False):
    """
    Returns one row per account of this organization:
        (account_id, code, name, account_type, total_debit, total_credit)

    Only lines belonging to journal entries of THIS organization, with an
    accepted status, dated on or before `as_at` (if given), are summed.
    Accounts with no activity still come back (with zeros); the calculation
    step hides them.
    """
    line_filters = ["jh.organization_id = %s", "jh.status = ANY(%s)"]
    params = [org_id, _status_list(include_drafts)]
    if as_at:
        line_filters.append("jh.accounting_date <= %s")
        params.append(as_at)

    sql = f"""
        SELECT a.account_id, a.code, a.name, a.account_type,
               COALESCE(SUM(x.debit), 0)  AS total_debit,
               COALESCE(SUM(x.credit), 0) AS total_credit
        FROM chart_of_accounts a
        LEFT JOIN (
            SELECT jl.account_id, jl.debit, jl.credit
            FROM journal_lines jl
            JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
            WHERE {" AND ".join(line_filters)}
        ) x ON x.account_id = a.account_id
        WHERE a.organization_id = %s
        GROUP BY a.account_id, a.code, a.name, a.account_type
        ORDER BY a.code
    """
    params.append(org_id)
    cursor.execute(sql, params)
    return cursor.fetchall()


def fetch_unbalanced_entries(cursor, org_id, as_at=None, include_drafts=False):
    """
    Finds journal entries whose own debits and credits differ.
    This should return NOTHING. If it returns rows, those entries are the
    reason the trial balance is out of balance.
    Returns: (journal_header_id, reference, accounting_date, journal_name,
              status, total_debit, total_credit)
    """
    filters = ["jh.organization_id = %s", "jh.status = ANY(%s)"]
    params = [org_id, _status_list(include_drafts)]
    if as_at:
        filters.append("jh.accounting_date <= %s")
        params.append(as_at)

    sql = f"""
        SELECT jh.journal_header_id, jh.reference, jh.accounting_date,
               jh.journal_name, jh.status,
               COALESCE(SUM(jl.debit), 0)  AS total_debit,
               COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE {" AND ".join(filters)}
        GROUP BY jh.journal_header_id, jh.reference, jh.accounting_date,
                 jh.journal_name, jh.status
        HAVING COALESCE(SUM(jl.debit), 0) <> COALESCE(SUM(jl.credit), 0)
        ORDER BY jh.accounting_date, jh.journal_header_id
    """
    cursor.execute(sql, params)
    return cursor.fetchall()


def compute_trial_balance(rows):
    """
    Turns raw rows into the final trial balance.

    rows: iterable of (account_id, code, name, account_type,
                       total_debit, total_credit)

    For each account the NET balance is shown on its natural side:
      debits > credits  -> amount in the Debit column
      credits > debits  -> amount in the Credit column

    Returns a dict:
      lines            list of dicts (one per account that has activity)
      total_debits     sum of all raw debits
      total_credits    sum of all raw credits
      debit_balance    sum of the Debit column
      credit_balance   sum of the Credit column
      difference       debit_balance - credit_balance
      is_balanced      True only when difference is exactly zero
    """
    lines = []
    total_debits = ZERO
    total_credits = ZERO
    debit_balance = ZERO
    credit_balance = ZERO

    for account_id, code, name, account_type, debit, credit in rows:
        debit = money_round(to_decimal(debit))
        credit = money_round(to_decimal(credit))
        if debit == ZERO and credit == ZERO:
            continue  # hide accounts with no activity

        net = debit - credit
        debit_col = net if net > ZERO else ZERO
        credit_col = -net if net < ZERO else ZERO

        total_debits += debit
        total_credits += credit
        debit_balance += debit_col
        credit_balance += credit_col

        lines.append({
            "account_id": account_id,
            "code": code,
            "name": name,
            "account_type": account_type,
            "total_debit": debit,
            "total_credit": credit,
            "debit_balance": debit_col,
            "credit_balance": credit_col,
        })

    difference = debit_balance - credit_balance
    return {
        "lines": lines,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "debit_balance": debit_balance,
        "credit_balance": credit_balance,
        "difference": difference,
        "is_balanced": difference == ZERO,
    }
