

# ---------------------------------------------------------------------------
# Duplicating a vendor bill (the gear menu on the bill's page)
#
# A duplicate is a new DRAFT with the same vendor, the same lines, the same
# landed costs and the same accounts, dated today. Two things are deliberately
# NOT copied:
#
#   * the vendor's own bill number - that number belongs to the paper the vendor
#     sent, and two bills in the books cannot carry the same one;
#   * its posting - the copy is a draft, so nothing reaches the ledger until it
#     is posted on purpose.
# ---------------------------------------------------------------------------
def duplicate_vendor_bill(cursor, organization_id, vendor_bill_id, today=None):
    """Copy a bill into a new draft. Returns the new vendor_bill_id."""
    from datetime import date as _date

    from accounting.services import create_journal_entry
    today = today or _date.today()
    cursor.execute("""
        SELECT vendor_id, due_date, bill_date, payment_terms, journal_name, lc_reference,
               purchase_journal, landed_cost_basis, journal_header_id, notes,
               payment_reference, recipient_bank
        FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s
    """, (vendor_bill_id, organization_id))
    source = cursor.fetchone()
    if not source:
        raise ValueError("That bill is not one of this company's.")
    (vendor_id, due_date, bill_date, payment_terms, journal_name, lc_reference,
     purchase_journal, landed_cost_basis, journal_header_id, notes,
     payment_reference, recipient_bank) = source

    new_due = None
    if due_date and bill_date:
        new_due = today + (due_date - bill_date)

    cursor.execute("""
        INSERT INTO vendor_bills
            (organization_id, vendor_id, bill_date, due_date, bill_number, payment_terms,
             journal_name, lc_reference, purchase_journal, landed_cost_basis, notes,
             payment_reference, recipient_bank, status)
        VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, 'Draft')
        RETURNING vendor_bill_id
    """, (organization_id, vendor_id, today, new_due, payment_terms, journal_name,
          lc_reference, purchase_journal, landed_cost_basis, notes,
          payment_reference, recipient_bank))
    new_id = cursor.fetchone()[0]

    cursor.execute("""
        INSERT INTO vendor_bill_lines
            (vendor_bill_id, account_id, total, label, quantity, rate, line_order,
             discount_pct, product_id, line_kind)
        SELECT %s, account_id, total, label, quantity, rate, line_order,
               discount_pct, product_id, line_kind
        FROM vendor_bill_lines WHERE vendor_bill_id = %s ORDER BY line_order
    """, (new_id, vendor_bill_id))

    if journal_header_id:
        cursor.execute("""
            SELECT account_id, partner_name, label, debit, credit
            FROM journal_lines WHERE journal_header_id = %s ORDER BY line_order, journal_line_id
        """, (journal_header_id,))
        lines = [{"account_id": row[0], "partner_name": row[1], "label": row[2],
                  "debit": row[3], "credit": row[4]} for row in cursor.fetchall()]
        if lines:
            new_header_id = create_journal_entry(
                cursor, organization_id=organization_id,
                reference=f"Vendor Bill (copy) - {today.isoformat()}",
                accounting_date=today, journal_name=journal_name or "Purchase Bills",
                lines=lines, source_type="VendorBill", source_id=new_id, auto_post=False)
            cursor.execute("UPDATE vendor_bills SET journal_header_id = %s WHERE vendor_bill_id = %s",
                           (new_header_id, new_id))
    return new_id
