"""
accounting/services.py

The ONE place that actually writes a journal entry to the database.
Every other segment (Vendors, Sales, Assets, WHT, etc.) will import
create_journal_entry() from here instead of writing to journal_headers/
journal_lines directly - that's what makes the confirmed Dr/Cr posting
rules from the build brief enforceable in one place instead of being
re-implemented slightly differently in every segment.

USAGE (from any future segment, e.g. vendors/services.py):
    from accounting.services import create_journal_entry

    create_journal_entry(
        cursor,
        organization_id=org_id,
        reference="Vendor Payment PBHL-C/2026/0001",
        accounting_date=today,
        journal_name="Vendor Payments",
        lines=[
            {"account_id": trade_creditors_id, "partner_name": vendor_name, "label": "...", "debit": 12938, "credit": 0},
            {"account_id": bank_account_id,   "partner_name": vendor_name, "label": "...", "debit": 0,     "credit": 12226.41},
            {"account_id": wht_payable_id,    "partner_name": vendor_name, "label": "...", "debit": 0,     "credit": 711.59},
        ],
        source_type="VendorPayment",
        source_id=payment_id,
        auto_post=True,
    )
"""
from datetime import datetime
from core.money import to_decimal, money_round
from accounting.schema import CASH_AND_BANK_KINDS


class JournalEntryError(Exception):
    """
    "This journal entry cannot be saved / posted." The message is written
    for the bookkeeper and is safe to show on the form as-is.

    Routes should catch THIS class (not just one of the two below), so
    every kind of refusal comes back as a message instead of a crash page.
    """
    pass


class UnbalancedEntryError(JournalEntryError):
    """Raised when a journal entry's total debits don't equal total credits."""
    pass


class InvalidEntryError(JournalEntryError, ValueError):
    """
    Raised when a journal entry is unacceptable for a reason other than
    balance: no lines, a line with no account, an account that belongs to
    another organization, a negative amount, or one line carrying both a
    debit and a credit.

    (It is also a ValueError because that is what the cross-organization
    account check raised before this class existed - code that already
    catches ValueError keeps working.)
    """
    pass


def create_journal_entry(cursor, organization_id, reference, accounting_date,
                          lines, journal_name="Miscellaneous Operations",
                          source_type="Manual", source_id=None, auto_post=False):
    """
    Creates a journal entry (header + lines). Always validates that debits
    equal credits before writing anything - an out-of-balance entry is a
    bug, not just a warning, so this refuses to create one.

    It also refuses, for EVERY segment that posts through here:
      - an entry with no lines
      - a line with no account, or an account of another organization
      - a negative debit or credit (a reversal is entered the other way
        round, never as a negative amount)
      - one line carrying both a debit and a credit

    Raises JournalEntryError (UnbalancedEntryError / InvalidEntryError)
    with a message that is safe to show to the user. Nothing is written
    when it raises.

    Returns the new journal_header_id.
    """
    if not lines:
        raise InvalidEntryError("A journal entry needs at least one line.")

    for line in lines:
        acc_id = line.get("account_id")
        if acc_id is None:
            raise InvalidEntryError(
                "A journal line has no account. If this came from a bill, invoice, "
                "payment or receipt, one of the control accounts it posts to is "
                "missing from the Chart of Accounts."
            )
        cursor.execute(
            "SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s",
            (acc_id, organization_id)
        )
        if not cursor.fetchone():
            raise InvalidEntryError(
                f"Account ID {acc_id} does not belong to organization {organization_id}. "
                "If you switched organization in another tab, reload this page and "
                "pick the accounts again."
            )

    normalized_lines = []
    for number, line in enumerate(lines, start=1):
        debit_raw = to_decimal(line.get("debit", 0))
        credit_raw = to_decimal(line.get("credit", 0))
        # "NaN" / "Infinity" are valid Decimals but must never reach the ledger.
        if not (debit_raw.is_finite() and credit_raw.is_finite()):
            raise InvalidEntryError(f"Line {number}: the amount is not a valid number.")
        debit_val = money_round(debit_raw)
        credit_val = money_round(credit_raw)
        if debit_val < 0 or credit_val < 0:
            raise InvalidEntryError(
                f"Line {number}: a debit or credit amount cannot be negative."
            )
        if debit_val != 0 and credit_val != 0:
            raise InvalidEntryError(
                f"Line {number}: a line can carry a debit or a credit, not both."
            )
        normalized_lines.append({
            **line,
            "debit": debit_val,
            "credit": credit_val,
        })

    total_debit = sum((l["debit"] for l in normalized_lines), to_decimal(0))
    total_credit = sum((l["credit"] for l in normalized_lines), to_decimal(0))

    if total_debit != total_credit:
        raise UnbalancedEntryError(
            f"Journal entry does not balance: debits {total_debit} != credits {total_credit}"
        )

    status = "Posted" if auto_post else "Draft"
    posted_at = datetime.utcnow() if auto_post else None

    cursor.execute("""
        INSERT INTO journal_headers
            (organization_id, reference, accounting_date, journal_name,
             status, source_type, source_id, posted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING journal_header_id
    """, (organization_id, reference, accounting_date, journal_name,
          status, source_type, source_id, posted_at))
    journal_header_id = cursor.fetchone()[0]

    for order, line in enumerate(normalized_lines):
        cursor.execute("""
            INSERT INTO journal_lines
                (journal_header_id, account_id, partner_name, label,
                 debit, credit, line_order)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            journal_header_id,
            line["account_id"],
            line.get("partner_name"),
            line.get("label"),
            line["debit"],
            line["credit"],
            order,
        ))

    return journal_header_id


def post_journal_entry(cursor, journal_header_id):
    """
    Moves a Draft journal entry to Posted. Re-validates the balance first,
    in case lines were edited after the draft was created but before
    posting (shouldn't normally happen once we lock editing on Draft
    entries, but this is cheap insurance).

    Only a DRAFT entry is changed. Calling this on an entry that is already
    Posted (a double click, a stale tab) does nothing at all - in
    particular it does NOT overwrite the original posted_at timestamp,
    which is part of the audit trail of a posted document.

    Returns True if this call posted the entry, False if it was already
    posted.
    """
    cursor.execute(
        "SELECT SUM(debit), SUM(credit) FROM journal_lines WHERE journal_header_id = %s",
        (journal_header_id,)
    )
    row = cursor.fetchone()
    total_debit = to_decimal(row[0] if row else 0)
    total_credit = to_decimal(row[1] if row else 0)

    if total_debit != total_credit:
        raise UnbalancedEntryError(
            f"Cannot post: debits {total_debit} != credits {total_credit}"
        )

    cursor.execute("""
        UPDATE journal_headers
        SET status = 'Posted', posted_at = %s
        WHERE journal_header_id = %s AND status = 'Draft'
    """, (datetime.utcnow(), journal_header_id))
    return cursor.rowcount == 1


# ---------------------------------------------------------------------
# Bank and cash accounts
# ---------------------------------------------------------------------
# Money can only be paid out of, or received into, an account the Chart of
# Accounts marks as Bank or Cash (accounting/schema.py, account_kind).
# Every screen that moves money - vendor payments, customer receipts and,
# when it is built, the Bank Internal Transfer - gets its account list from
# here and has its chosen account checked here, so the rule lives in one
# place.

def list_cash_and_bank_accounts(cursor, organization_id):
    """
    [(account_id, code, name, account_kind), ...] - this organization's
    ACTIVE bank and cash accounts, in code order. This is the whole list a
    "Paid from" / "Deposit to" box offers.
    """
    cursor.execute("""
        SELECT account_id, code, name, account_kind
        FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE AND account_kind IN %s
        ORDER BY code, account_id
    """, (organization_id, CASH_AND_BANK_KINDS))
    return cursor.fetchall()


def cash_or_bank_account_problem(cursor, organization_id, account_id, box_name):
    """
    Checks the account picked in a "Paid from" / "Deposit to" box.

    Returns None when it is one of this organization's active bank or cash
    accounts. Otherwise returns the sentence to show on the form.
    `box_name` is how the form labels the box, e.g. "Paid from account".
    (An account of ANOTHER organization is not this function's business:
    the caller has already refused that.)
    """
    cursor.execute("""
        SELECT code, name, account_kind, is_active
        FROM chart_of_accounts
        WHERE account_id = %s AND organization_id = %s
    """, (account_id, organization_id))
    row = cursor.fetchone()
    if row is None:
        return f"{box_name}: choose one of this company's bank or cash accounts."
    code, name, account_kind, is_active = row
    if account_kind not in CASH_AND_BANK_KINDS:
        return (
            f"{box_name} must be a bank or cash account. {code} - {name} is not marked "
            "as Bank or Cash on the Chart of Accounts."
        )
    if not is_active:
        return (
            f"{box_name}: {code} - {name} is inactive. Choose another bank or cash "
            "account, or make it active again on the Chart of Accounts."
        )
    return None
