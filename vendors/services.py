"""
The rules the Purchases screens follow, kept out of the pages so they can be
checked on their own.
"""

from core.money import to_decimal



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
               payment_reference, recipient_bank, COALESCE(bill_kind, 'Standard'),
               COALESCE(tax_total, 0)
        FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s
    """, (vendor_bill_id, organization_id))
    source = cursor.fetchone()
    if not source:
        raise ValueError("That bill is not one of this company's.")
    (vendor_id, due_date, bill_date, payment_terms, journal_name, lc_reference,
     purchase_journal, landed_cost_basis, journal_header_id, notes,
     payment_reference, recipient_bank, bill_kind, tax_total) = source

    new_due = None
    if due_date and bill_date:
        new_due = today + (due_date - bill_date)

    cursor.execute("""
        INSERT INTO vendor_bills
            (organization_id, vendor_id, bill_date, due_date, bill_number, payment_terms,
             journal_name, lc_reference, purchase_journal, landed_cost_basis, notes,
             payment_reference, recipient_bank, status, bill_kind, tax_total, amount_due)
        VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, 'Draft', %s, %s, %s)
        RETURNING vendor_bill_id
    """, (organization_id, vendor_id, today, new_due, payment_terms, journal_name,
          lc_reference, purchase_journal, landed_cost_basis, notes,
          payment_reference, recipient_bank, bill_kind, tax_total, tax_total))
    new_id = cursor.fetchone()[0]

    cursor.execute("""
        INSERT INTO vendor_bill_lines
            (vendor_bill_id, account_id, total, label, quantity, rate, line_order,
             discount_pct, product_id, line_kind, unit, discount_amount,
             tax_rate_id, tax_amount, asset_life_years)
        SELECT %s, account_id, total, label, quantity, rate, line_order,
               discount_pct, product_id, line_kind, unit, discount_amount,
               tax_rate_id, tax_amount, asset_life_years
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


def replace_vendor_bill(cursor, organization_id, vendor_bill_id, header, lines,
                        trade_payables_id, input_tax_id=None):
    """Correct a DRAFT vendor bill: same bill, same number, new contents.

    `lines` is what the form posted, already worked out by the route (quantity x
    rate, less the discount, plus the tax) - the same shape a new bill is
    written from, so a corrected bill and a fresh one cannot disagree. The bill
    keeps its vendor number (the paper the supplier sent), its kind, its
    landed-cost basis and its place in the books; the lines, the tax total and
    the draft journal entry are written again.
    """
    from accounting.services import create_journal_entry, refill_journal_entry

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
            accounting_date = %s, payment_reference = %s, recipient_bank = %s
        WHERE vendor_bill_id = %s
    """, (header["vendor_id"], header["bill_date"], header.get("due_date"),
          header.get("payment_terms"), header.get("lc_reference"), header.get("journal_name"),
          header.get("landed_cost_basis"), header.get("accounting_date") or header["bill_date"],
          header.get("payment_reference"), header.get("recipient_bank"), vendor_bill_id))

    cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id = %s", (vendor_bill_id,))
    for order, line in enumerate(lines):
        insert_bill_line(cursor, vendor_bill_id, line, order)

    entry_lines, bill_total, tax_total = bill_posting_lines(
        lines, vendor_name=vendor_name, bill_number=bill_number,
        trade_payables_id=trade_payables_id, input_tax_id=input_tax_id)

    cursor.execute("UPDATE vendor_bills SET amount_due = %s, tax_total = %s WHERE vendor_bill_id = %s",
                   (bill_total, tax_total, vendor_bill_id))

    # The bill's own entry is written again - the same entry, so anything
    # pointing at it still points at it.
    cursor.execute("SELECT journal_header_id FROM vendor_bills WHERE vendor_bill_id = %s", (vendor_bill_id,))
    old_header = cursor.fetchone()[0]
    reference = f"Vendor Bill {bill_number} - {vendor_name}"
    accounting_date = header.get("accounting_date") or header["bill_date"]
    journal_name = header.get("journal_name") or "Expenses"
    if old_header:
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


# ---------------------------------------------------------------------------
# What a bill line and a bill posting look like, shared by the new-bill
# screen and the correction screen, so a corrected bill and a fresh one
# cannot disagree about the arithmetic.
# ---------------------------------------------------------------------------
def bill_posting_lines(lines, *, vendor_name, bill_number, trade_payables_id, input_tax_id):
    """The journal entry a bill writes: each line net, the tax, and the debt.

    The tax of several lines that share a rate is one journal line, because the
    tax return only cares about the rate - and a bill with 40 lines at 18% does
    not deserve 40 more lines in the ledger.
    """
    entry, tax_by_rate = [], {}
    bill_total = to_decimal(0)
    for line in lines:
        bill_total += line["total"]
        entry.append({"account_id": line["account_id"], "label": line["label"],
                      "debit": line["net"], "credit": to_decimal(0)})
        if line["tax_amount"] > 0 and line["tax_rate_id"]:
            key = (line["tax_rate_id"], line["tax_label"], line["tax_percent"])
            tax_by_rate[key] = tax_by_rate.get(key, to_decimal(0)) + line["tax_amount"]

    tax_total = sum(tax_by_rate.values(), to_decimal(0))
    if tax_total > 0:
        if not input_tax_id:
            raise ValueError(
                "This company has no account 1250 Input Sales Tax Recoverable, so the sales tax "
                "on this bill has nowhere to go. Add that account in Chart of Accounts, or take "
                "the tax rates off the lines."
            )
        for (_rate_id, label, percent), amount in tax_by_rate.items():
            entry.append({"account_id": input_tax_id,
                          "label": f"Sales tax {percent:g}% - {label}",
                          "debit": amount, "credit": to_decimal(0)})

    entry.append({"account_id": trade_payables_id, "partner_name": vendor_name,
                  "label": f"Vendor Bill - {vendor_name} ({bill_number})",
                  "debit": to_decimal(0), "credit": bill_total})
    return entry, bill_total, tax_total


def insert_bill_line(cursor, vendor_bill_id, line, order):
    cursor.execute("""
        INSERT INTO vendor_bill_lines
            (vendor_bill_id, label, account_id, quantity, rate, total, line_order,
             product_id, line_kind, unit, discount_pct, discount_amount,
             tax_rate_id, tax_amount, asset_life_years)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (vendor_bill_id, line["label"], line["account_id"], line["quantity"], line["rate"],
          line["total"], order, line["product_id"], line["line_kind"], line["unit"],
          line["discount_percent"], line["discount_amount"], line["tax_rate_id"],
          line["tax_amount"], line["asset_life_years"]))
