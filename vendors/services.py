"""
The rules the Purchases screens follow, kept out of the pages so they can be
checked on their own.
"""

from core.money import money_round, to_decimal



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


def reset_vendor_bill_to_draft(cursor, organization_id, vendor_bill_id):
    """Take a posted vendor bill back to a draft so it can be corrected.

    Refused while a payment is matched to it (the money is already in the books),
    and refused when it is already a draft. The entry the bill wrote goes back to
    Draft with it, and the bill keeps its number, its lines and its landed costs.
    """
    from accounting.services import set_linked_entry_to_draft

    cursor.execute("""
        SELECT bill_number, status, journal_header_id
        FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s
    """, (vendor_bill_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise ValueError("That bill is not one of this company's.")
    bill_number, status, header_id = row
    if (status or "").lower() == "draft":
        raise ValueError(f"Bill {bill_number or ''} is already a draft - open it and edit it.")
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(allocated_amount), 0) "
                   "FROM vendor_payment_allocations WHERE vendor_bill_id = %s", (vendor_bill_id,))
    matched, amount = cursor.fetchone()
    if matched:
        raise ValueError(
            "Bill %s has %s paid against it. Remove that payment match first, then reset the bill."
            % (bill_number or "", "{:,.2f}".format(to_decimal(amount or 0))))
    cursor.execute("UPDATE vendor_bills SET status = 'Draft', paid_amount = 0 "
                   "WHERE vendor_bill_id = %s", (vendor_bill_id,))
    set_linked_entry_to_draft(cursor, organization_id, header_id)
    return bill_number


def replace_vendor_bill(cursor, organization_id, vendor_bill_id, header, raw_lines, trade_payables_id):
    """Correct a DRAFT vendor bill: same bill, same number, new contents.

    `raw_lines` is a list of dicts with label, account_id, quantity, rate, total,
    product_id and line_kind - what the form posts. The bill keeps its vendor
    number (the paper the supplier sent), its landed-cost basis and its place in
    the books; the lines and the draft journal entry are written again.
    """
    from accounting.services import create_journal_entry

    cursor.execute("""
        SELECT bill_number, status FROM vendor_bills
        WHERE vendor_bill_id = %s AND organization_id = %s
    """, (vendor_bill_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise ValueError("That bill is not one of this company's.")
    bill_number, status = row
    if (status or "").lower() != "draft":
        raise ValueError(f"Bill {bill_number or ''} is posted. Reset it to a draft before changing it.")
    cursor.execute("SELECT COUNT(*) FROM vendor_payment_allocations WHERE vendor_bill_id = %s",
                   (vendor_bill_id,))
    if cursor.fetchone()[0]:
        raise ValueError("A payment is matched to this bill, so it cannot be changed. Remove the match first.")

    cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s AND organization_id = %s",
                   (header["vendor_id"], organization_id))
    vendor_row = cursor.fetchone()
    if not vendor_row:
        raise ValueError("That vendor is not one of this company's.")
    vendor_name = vendor_row[0]

    cursor.execute("""
        UPDATE vendor_bills
        SET vendor_id = %s, bill_date = %s, due_date = %s, payment_terms = %s,
            lc_reference = %s, journal_name = %s, landed_cost_basis = %s,
            accounting_date = %s
        WHERE vendor_bill_id = %s
    """, (header["vendor_id"], header["bill_date"], header.get("due_date"),
          header.get("payment_terms"), header.get("lc_reference"), header.get("journal_name"),
          header.get("landed_cost_basis"), header.get("accounting_date") or header["bill_date"],
          vendor_bill_id))

    cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id = %s", (vendor_bill_id,))

    entry_lines = []
    bill_total = to_decimal(0)
    for order, line in enumerate(raw_lines):
        quantity = to_decimal(line.get("quantity") or 1)
        rate = to_decimal(line.get("rate") or 0)
        raw_total = line.get("total")
        total = money_round(raw_total) if raw_total not in (None, "") else money_round(quantity * rate)
        bill_total += total
        cursor.execute("""
            INSERT INTO vendor_bill_lines
                (vendor_bill_id, label, account_id, quantity, rate, total, line_order,
                 product_id, line_kind)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (vendor_bill_id, line.get("label"), line["account_id"], quantity, rate, total, order,
              line.get("product_id"), line.get("line_kind") or "Goods"))
        entry_lines.append({"account_id": line["account_id"], "label": line.get("label"),
                            "debit": total, "credit": to_decimal(0)})

    entry_lines.append({
        "account_id": trade_payables_id,
        "partner_name": vendor_name,
        "label": f"Vendor Bill - {vendor_name} ({bill_number})",
        "debit": 0,
        "credit": bill_total,
    })

    cursor.execute("UPDATE vendor_bills SET amount_due = %s WHERE vendor_bill_id = %s",
                   (bill_total, vendor_bill_id))

    # The bill's own entry is written again - the same entry, so anything
    # pointing at it still points at it.
    cursor.execute("SELECT journal_header_id FROM vendor_bills WHERE vendor_bill_id = %s", (vendor_bill_id,))
    old_header = cursor.fetchone()[0]
    reference = f"Vendor Bill {bill_number} - {vendor_name}"
    accounting_date = header.get("accounting_date") or header["bill_date"]
    journal_name = header.get("journal_name") or "Expenses"
    if old_header:
        from accounting.services import refill_journal_entry
        refill_journal_entry(cursor, organization_id, old_header, reference,
                             accounting_date, journal_name, entry_lines)
    else:
        new_header = create_journal_entry(
            cursor, organization_id=organization_id, reference=reference,
            accounting_date=accounting_date, journal_name=journal_name,
            lines=entry_lines, source_type="VendorBill", source_id=vendor_bill_id, auto_post=False)
        cursor.execute("UPDATE vendor_bills SET journal_header_id = %s WHERE vendor_bill_id = %s",
                       (new_header, vendor_bill_id))
    return bill_number
