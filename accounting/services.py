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


class UnbalancedEntryError(Exception):
    """Raised when a journal entry's total debits don't equal total credits."""
    pass


def create_journal_entry(cursor, organization_id, reference, accounting_date,
                          lines, journal_name="Miscellaneous Operations",
                          source_type="Manual", source_id=None, auto_post=False):
    """
    Creates a journal entry (header + lines). Always validates that debits
    equal credits before writing anything - an out-of-balance entry is a
    bug, not just a warning, so this refuses to create one.

    Returns the new journal_header_id.
    """
    for line in lines:
        acc_id = line.get("account_id")
        if acc_id is not None:
            cursor.execute(
                "SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s",
                (acc_id, organization_id)
            )
            if not cursor.fetchone():
                raise ValueError(f"Account ID {acc_id} does not belong to organization {organization_id}")

    normalized_lines = []
    for line in lines:
        debit_val = money_round(line.get("debit", 0))
        credit_val = money_round(line.get("credit", 0))
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
        WHERE journal_header_id = %s
    """, (datetime.utcnow(), journal_header_id))
