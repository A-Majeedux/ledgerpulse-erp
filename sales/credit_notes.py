"""
sales/credit_notes.py

Customer credit notes - what the customer sends back, and what he is owed for it.

A vendor bill has had a credit note all along; the sales side had nothing, so a
returned bag of urea could only be written off by hand in a journal entry that
nobody could print for the customer. This is the document itself:

  * numbered the way invoices are - CN/2026/00001 - and never renumbered;
  * posted with its own entry: the revenue goes back down (debit the sales
    account that was credited), the output sales tax reverses with it, and the
    customer owes that much less (credit Trade Receivables);
  * the goods come back into stock at what they cost when they left, so a return
    cannot quietly make a profit or a loss of its own;
  * applied to the invoice it names, so the invoice's still-due figure follows.

Posting twice is impossible and resetting is allowed - the same rules as every
other transaction in the app.
"""
from core.money import money_round, to_decimal
from core import stock, audit
from accounting.services import create_journal_entry, post_journal_entry

REASONS = ["Return", "Allowance", "Correction", "Discount"]
TRADE_RECEIVABLES = "1100"
OUTPUT_TAX = "2200"


class CreditNoteError(ValueError):
    """Something the person can fix by changing what he typed."""


def _account_id_by_code(cursor, organization_id, code):
    cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
                   (organization_id, code))
    row = cursor.fetchone()
    return row[0] if row else None


def next_number(cursor, organization_id, note_date):
    """CN/2026/00001 - per company, per year, in order of creation."""
    year = getattr(note_date, "year", None) or note_date[:4]
    cursor.execute("""
        SELECT COALESCE(MAX(CAST(SPLIT_PART(credit_note_no, '/', 3) AS INTEGER)), 0)
        FROM customer_credit_notes
        WHERE organization_id = %s AND credit_note_no LIKE %s
    """, (organization_id, f"CN/{year}/%"))
    return f"CN/{year}/{cursor.fetchone()[0] + 1:05d}"


def give_number(cursor, organization_id, credit_note_id):
    cursor.execute("SELECT credit_note_no, note_date FROM customer_credit_notes "
                   "WHERE customer_credit_note_id = %s AND organization_id = %s",
                   (credit_note_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise CreditNoteError("That credit note is not one of this company's.")
    number, note_date = row
    if number:
        return number
    number = next_number(cursor, organization_id, note_date)
    cursor.execute("UPDATE customer_credit_notes SET credit_note_no = %s WHERE customer_credit_note_id = %s",
                   (number, credit_note_id))
    return number


def read_lines(cursor, organization_id, credit_note_id):
    cursor.execute("""
        SELECT ccnl.customer_credit_note_line_id, ccnl.product_id, p.name, ccnl.account_id,
               coa.code, coa.name, ccnl.label, ccnl.unit, ccnl.quantity, ccnl.rate,
               ccnl.sales_tax_rate_id, COALESCE(str.label, ''), COALESCE(str.rate_percent, 0),
               ccnl.tax_amount, ccnl.total, ccnl.unit_cost
        FROM customer_credit_note_lines ccnl
        LEFT JOIN products p ON p.product_id = ccnl.product_id
        LEFT JOIN chart_of_accounts coa ON coa.account_id = ccnl.account_id
        LEFT JOIN sales_tax_rates str ON str.sales_tax_rate_id = ccnl.sales_tax_rate_id
        WHERE ccnl.customer_credit_note_id = %s
        ORDER BY ccnl.line_order, ccnl.customer_credit_note_line_id
    """, (credit_note_id,))
    return cursor.fetchall()


def _cost_when_it_left(cursor, organization_id, credit_note_id, sales_invoice_id, product_id):
    """What the goods cost when they were sold: from the invoice, or the average.

    A return has to come back in at the cost it went out at, or the profit the
    company made on the sale is quietly rewritten by the return.
    """
    if sales_invoice_id:
        cursor.execute("""
            SELECT unit_cost FROM stock_movements
            WHERE organization_id = %s AND product_id = %s AND source_type = 'SalesInvoice'
              AND source_id = %s
            ORDER BY stock_movement_id DESC LIMIT 1
        """, (organization_id, product_id, sales_invoice_id))
        row = cursor.fetchone()
        if row and to_decimal(row[0]) > 0:
            return to_decimal(row[0])
    return stock.average_cost(cursor, organization_id, product_id)


def build_lines(cursor, organization_id, credit_note_id):
    """Work out each line's money and cost, and write the totals on the note."""
    lines = read_lines(cursor, organization_id, credit_note_id)
    subtotal = tax_total = to_decimal(0)
    for line in lines:
        subtotal += to_decimal(line[14]) - to_decimal(line[13])
        tax_total += to_decimal(line[13])
    total = subtotal + tax_total
    cursor.execute("""
        UPDATE customer_credit_notes SET subtotal = %s, tax_total = %s, total = %s
        WHERE customer_credit_note_id = %s AND organization_id = %s
    """, (money_round(subtotal), money_round(tax_total), money_round(total), credit_note_id, organization_id))
    return lines


def save_lines(cursor, organization_id, credit_note_id, form):
    """Replace the note's lines from the form it was submitted on."""
    products = form.getlist("product_id[]")
    accounts = form.getlist("account_id[]")
    labels = form.getlist("label[]")
    units = form.getlist("unit[]")
    quantities = form.getlist("quantity[]")
    rates = form.getlist("rate[]")
    taxes = form.getlist("sales_tax_rate_id[]")

    cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s", (organization_id,))
    own_accounts = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT product_id FROM products WHERE organization_id = %s", (organization_id,))
    own_products = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT sales_tax_rate_id, label, rate_percent FROM sales_tax_rates WHERE organization_id = %s",
                   (organization_id,))
    tax_rates = {row[0]: (row[1], to_decimal(row[2])) for row in cursor.fetchall()}

    cursor.execute("SELECT COALESCE(sales_invoice_id, 0) FROM customer_credit_notes "
                   "WHERE customer_credit_note_id = %s AND organization_id = %s",
                   (credit_note_id, organization_id))
    row = cursor.fetchone()
    sales_invoice_id = row[0] if row and row[0] else None

    def at(rows, index, default=None):
        return rows[index] if index < len(rows) else default

    prepared = []
    for index, account_id in enumerate(accounts):
        quantity = to_decimal(at(quantities, index) or 0)
        rate = to_decimal(at(rates, index) or 0)
        label = (at(labels, index) or "").strip()
        if not quantity and not rate:
            continue                      # an empty row on the form is not a line
        if not account_id or (str(account_id).isdigit() and int(account_id) not in own_accounts):
            raise CreditNoteError("Every line needs an account from this company's own chart.")
        account_id = int(account_id)
        product_id = at(products, index)
        product_id = int(product_id) if product_id and str(product_id).isdigit() and int(product_id) in own_products else None
        unit = (at(units, index) or "Units")[:20]

        tax_id = at(taxes, index)
        tax_id = int(tax_id) if tax_id and str(tax_id).isdigit() and int(tax_id) in tax_rates else None
        net = money_round(quantity * rate)
        tax_amount = money_round(net * tax_rates[tax_id][1] / 100) if tax_id else to_decimal(0)
        total = net + tax_amount
        unit_cost = _cost_when_it_left(cursor, organization_id, credit_note_id, sales_invoice_id, product_id) \
            if product_id else to_decimal(0)

        prepared.append((product_id, account_id, label or None, unit, quantity, rate,
                         tax_id, tax_amount, total, unit_cost, index))

    cursor.execute("DELETE FROM customer_credit_note_lines WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    for line in prepared:
        cursor.execute("""
            INSERT INTO customer_credit_note_lines
                (customer_credit_note_id, product_id, account_id, label, unit, quantity, rate,
                 sales_tax_rate_id, tax_amount, total, unit_cost, line_order)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (credit_note_id,) + line)


def journal_lines_for(cursor, organization_id, credit_note_id, customer_name, number):
    """The contra entry: revenue down, tax down, the customer owes less.

    And, because the goods are back on the shelf, the cost comes back out of the
    expense with them: Stock on Hand is debited and Cost of Goods Sold credited
    with what those goods cost when they left. Without that pair the return would
    look profitable - the sale's cost would stay in the expense while its revenue
    came back out of it.
    """
    lines = read_lines(cursor, organization_id, credit_note_id)
    cursor.execute("SELECT restock FROM customer_credit_notes "
                   "WHERE customer_credit_note_id = %s AND organization_id = %s",
                   (credit_note_id, organization_id))
    row = cursor.fetchone()
    restock = bool(row and row[0])
    receivable = _account_id_by_code(cursor, organization_id, TRADE_RECEIVABLES)
    output_tax = _account_id_by_code(cursor, organization_id, OUTPUT_TAX)
    if not receivable:
        raise CreditNoteError("This company has no 1100 Trade Receivables account, "
                              "so a credit note cannot reduce what the customer owes.")

    total = to_decimal(0)
    by_account = {}
    tax_total = to_decimal(0)
    for line in lines:
        account_id = line[3]
        net = to_decimal(line[14]) - to_decimal(line[13])
        total += to_decimal(line[14])
        tax_total += to_decimal(line[13])
        if account_id:
            by_account[account_id] = by_account.get(account_id, to_decimal(0)) + net

    if total <= 0:
        raise CreditNoteError("A credit note with nothing on it cannot be posted.")

    entry = [{
        "account_id": receivable, "partner_name": customer_name,
        "label": f"Credit note {number} - {customer_name}",
        "debit": 0, "credit": money_round(total),
    }]
    for account_id, amount in by_account.items():
        if amount > 0:
            entry.append({
                "account_id": account_id, "partner_name": customer_name,
                "label": f"Credit note {number} - {customer_name}",
                "debit": money_round(amount), "credit": 0,
            })
    if tax_total > 0:
        if not output_tax:
            raise CreditNoteError("This company has no 2200 Output Tax Payable account, "
                                  "so the sales tax on the credit note cannot be reversed.")
        entry.append({
            "account_id": output_tax, "partner_name": customer_name,
            "label": f"Output sales tax reversed - {number}",
            "debit": money_round(tax_total), "credit": 0,
        })

    # the cost of the goods that came back, out of the expense and into stock
    if restock:
        cost_back = to_decimal(0)
        for line in lines:
            if line[1] and to_decimal(line[8]) > 0:
                cost_back += money_round(to_decimal(line[8]) * to_decimal(line[15]))
        cost_back = money_round(cost_back)
        if cost_back > 0:
            stock_account = stock.stock_account_id(cursor, organization_id)
            cogs_account = stock.cogs_account_id(cursor, organization_id)
            if not stock_account or not cogs_account:
                raise CreditNoteError(
                    "This company has no 1200 Stock on Hand or 5000 Cost of Goods Sold account, "
                    "so the cost of the returned goods cannot be put back.")
            entry.append({
                "account_id": stock_account, "partner_name": None,
                "label": f"{stock.COGS_LABEL} returned - {number}",
                "debit": cost_back, "credit": 0,
            })
            entry.append({
                "account_id": cogs_account, "partner_name": None,
                "label": f"{stock.COGS_LABEL} returned - {number}",
                "debit": 0, "credit": cost_back,
            })
    return entry


def post(cursor, organization_id, credit_note_id):
    """Post the note: entry, the goods back in, and the invoice it settles."""
    cursor.execute("""
        SELECT ccn.status, ccn.customer_id, ccn.credit_note_no, ccn.note_date, ccn.sales_invoice_id,
               ccn.restock, ccn.journal_header_id, c.name
        FROM customer_credit_notes ccn JOIN customers c ON c.customer_id = ccn.customer_id
        WHERE ccn.customer_credit_note_id = %s AND ccn.organization_id = %s
        FOR UPDATE
    """, (credit_note_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise CreditNoteError("That credit note is not one of this company's.")
    status, customer_id, number, note_date, sales_invoice_id, restock, header_id, customer_name = row
    if status == "Posted":
        raise CreditNoteError("That credit note is already posted.")

    number = number or give_number(cursor, organization_id, credit_note_id)
    build_lines(cursor, organization_id, credit_note_id)
    entry = journal_lines_for(cursor, organization_id, credit_note_id, customer_name, number)

    if header_id:
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id = %s", (header_id,))
        cursor.execute("DELETE FROM journal_headers WHERE journal_header_id = %s", (header_id,))
    header_id = create_journal_entry(
        cursor, organization_id=organization_id,
        reference=f"Credit note {number} - {customer_name}",
        accounting_date=note_date.isoformat() if hasattr(note_date, "isoformat") else str(note_date),
        journal_name="Customer Credit Notes", lines=entry,
        source_type="CustomerCreditNote", source_id=credit_note_id, auto_post=False,
    )
    cursor.execute("UPDATE customer_credit_notes SET journal_header_id = %s WHERE customer_credit_note_id = %s",
                   (header_id, credit_note_id))
    post_journal_entry(cursor, header_id)

    returned = []
    if restock:
        for line in read_lines(cursor, organization_id, credit_note_id):
            product_id, quantity, unit_cost = line[1], to_decimal(line[8]), to_decimal(line[15])
            if not product_id or quantity <= 0:
                continue
            stock.record(cursor, organization_id, product_id, quantity, unit_cost,
                         source_type="CustomerCreditNote", source_id=credit_note_id,
                         reference=f"Credit note {number}",
                         movement_date=note_date)
            returned.append((product_id, quantity))

    cursor.execute("UPDATE customer_credit_notes SET status = 'Posted', accounting_date = %s "
                   "WHERE customer_credit_note_id = %s",
                   (note_date, credit_note_id))

    if sales_invoice_id:
        totals = total_of(cursor, organization_id, credit_note_id)
        settle_invoice(cursor, organization_id, sales_invoice_id, totals)
    audit.ensure_schema(cursor)
    audit.note(cursor, organization_id, "CustomerCreditNote", credit_note_id, "Posted",
               f"Credit note {number} posted"
               + (f": {len(returned)} product line(s) came back into stock" if returned else ""))
    return number


def settle_invoice(cursor, organization_id, sales_invoice_id, amount):
    """The customer owes this much less, so the invoice's still-due drops with it."""
    cursor.execute("""
        UPDATE sales_invoices
        SET paid_amount = COALESCE(paid_amount, 0) + %s
        WHERE sales_invoice_id = %s AND organization_id = %s
    """, (money_round(amount), sales_invoice_id, organization_id))


def unsettle_invoice(cursor, organization_id, sales_invoice_id, amount):
    cursor.execute("""
        UPDATE sales_invoices
        SET paid_amount = COALESCE(paid_amount, 0) - %s
        WHERE sales_invoice_id = %s AND organization_id = %s
    """, (money_round(amount), sales_invoice_id, organization_id))


def total_of(cursor, organization_id, credit_note_id):
    cursor.execute("SELECT COALESCE(total, 0) FROM customer_credit_notes "
                   "WHERE customer_credit_note_id = %s AND organization_id = %s",
                   (credit_note_id, organization_id))
    row = cursor.fetchone()
    return to_decimal(row[0]) if row else to_decimal(0)


def reset(cursor, organization_id, credit_note_id):
    """Back to a draft: the entry undone, the goods out again, the invoice owed again."""
    cursor.execute("""
        SELECT status, credit_note_no, sales_invoice_id, total
        FROM customer_credit_notes
        WHERE customer_credit_note_id = %s AND organization_id = %s FOR UPDATE
    """, (credit_note_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise CreditNoteError("That credit note is not one of this company's.")
    status, number, sales_invoice_id, total = row
    if status != "Posted":
        raise CreditNoteError("Only a posted credit note can be reset.")

    from accounting.services import set_linked_entry_to_draft
    cursor.execute("SELECT journal_header_id FROM customer_credit_notes WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    header_id = cursor.fetchone()[0]
    if header_id:
        set_linked_entry_to_draft(cursor, organization_id, header_id)
    stock.undo_document(cursor, organization_id, "CustomerCreditNote", credit_note_id)
    if sales_invoice_id:
        unsettle_invoice(cursor, organization_id, sales_invoice_id, to_decimal(total))

    cursor.execute("UPDATE customer_credit_notes SET status = 'Draft' WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    audit.ensure_schema(cursor)
    audit.note(cursor, organization_id, "CustomerCreditNote", credit_note_id, "Reset",
               f"{number or 'The credit note'} went back to a draft: its entry, its stock movements "
               "and what it took off the invoice were all undone")
    return number


def delete(cursor, organization_id, credit_note_id):
    """Delete a draft. A posted note is refused - reset it first."""
    cursor.execute("SELECT status, credit_note_no FROM customer_credit_notes "
                   "WHERE customer_credit_note_id = %s AND organization_id = %s FOR UPDATE",
                   (credit_note_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise CreditNoteError("That credit note is not one of this company's.")
    status, number = row
    if status != "Draft":
        raise CreditNoteError("A posted credit note cannot be deleted - reset it to a draft first.")
    cursor.execute("SELECT journal_header_id FROM customer_credit_notes WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    header_id = cursor.fetchone()[0]
    if header_id:
        cursor.execute("UPDATE customer_credit_notes SET journal_header_id = NULL WHERE customer_credit_note_id = %s",
                       (credit_note_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id = %s", (header_id,))
        cursor.execute("DELETE FROM journal_headers WHERE journal_header_id = %s", (header_id,))
    audit.ensure_schema(cursor)
    audit.note(cursor, organization_id, "CustomerCreditNote", credit_note_id, "Deleted",
               f"Draft credit note {number or ''} was deleted")
    cursor.execute("DELETE FROM customer_credit_note_lines WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    cursor.execute("DELETE FROM customer_credit_notes WHERE customer_credit_note_id = %s",
                   (credit_note_id,))
    return number
