"""
sales/services.py

The rules the Sales screens follow, kept out of the pages so they can be
checked on their own.

For now this is one thing: **the number on a sales invoice**.

His own screen numbers invoices like INV/2026/01749 - the letters INV, the
year, and a running number that starts again at 1 every year, inside each
company. ZYRO does the same:

    INV / 2026 / 00001
     |     |       |
     |     |       +-- the running number: 1, 2, 3... in that company, that year
     |     +---------- the year of the invoice date
     +---------------- the letters every sales invoice starts with

Why it is done here and not in the page:

  * a number is given when the invoice is saved, and never changes after that
    (a number on a paper invoice cannot be taken back);
  * a number is never used twice, even if two invoices are saved in the same
    second: a unique index on (company, number) refuses the second one, and
    the save simply takes the next number;
  * the invoices made before numbering existed get their numbers once, at
    startup, oldest first - exactly like the customers got their IDs.

The numbers are book numbers, not database IDs: sales_invoice_id stays what
it always was, and nothing that already uses it is affected.
"""
from datetime import date, timedelta

import psycopg2

PREFIX = "INV"          # what every sales invoice number starts with
NUMBER_DIGITS = 5       # 1 is written 00001, as on his own screen
ATTEMPTS = 5            # tries before giving up, if two saves fight for a number


def invoice_number(year, number):
    """(2026, 1749) -> 'INV/2026/01749'."""
    return f"{PREFIX}/{year}/{int(number):0{NUMBER_DIGITS}d}"


def year_of(invoice_date):
    """The year a number belongs to: the year of the invoice date."""
    if isinstance(invoice_date, str):
        invoice_date = date.fromisoformat(invoice_date)
    return invoice_date.year


def _last_number(cursor, organization_id, year):
    """The highest number already used this year in this company (0 if none)."""
    cursor.execute("""
        SELECT COALESCE(MAX(CAST(SPLIT_PART(invoice_no, '/', 3) AS INTEGER)), 0)
        FROM sales_invoices
        WHERE organization_id = %s AND invoice_no LIKE %s
    """, (organization_id, f"{PREFIX}/{year}/%"))
    return cursor.fetchone()[0] or 0


def next_invoice_number(cursor, organization_id, invoice_date):
    """The number the next invoice of that year in that company will get."""
    year = year_of(invoice_date)
    return invoice_number(year, _last_number(cursor, organization_id, year) + 1)


def give_invoice_a_number(cursor, organization_id, sales_invoice_id, invoice_date):
    """Write the invoice's number. Returns it.

    Two invoices saved at the same moment can work out the same next number;
    the unique index stops the second one, and this simply tries again with
    the number after it. Nothing is left half-saved: the failed try is rolled
    back to its own savepoint, not the whole invoice.
    """
    for _attempt in range(ATTEMPTS):
        number = next_invoice_number(cursor, organization_id, invoice_date)
        cursor.execute("SAVEPOINT invoice_number")
        try:
            cursor.execute("UPDATE sales_invoices SET invoice_no = %s WHERE sales_invoice_id = %s",
                           (number, sales_invoice_id))
            cursor.execute("RELEASE SAVEPOINT invoice_number")
            return number
        except psycopg2.errors.UniqueViolation:
            cursor.execute("ROLLBACK TO SAVEPOINT invoice_number")
    raise RuntimeError("Could not give the invoice a number - please try again.")


def number_missing_invoices(cursor):
    """Give a number to every invoice that has none, oldest first.

    The invoices made before numbering existed are numbered once, on the first
    start of this version: oldest invoice first, per company per year, so the
    order on the screen is the order on paper. An invoice that already has a
    number keeps it, so running this again changes nothing.
    """
    cursor.execute("""
        SELECT sales_invoice_id, organization_id, invoice_date
        FROM sales_invoices
        WHERE invoice_no IS NULL
        ORDER BY organization_id, invoice_date, sales_invoice_id
    """)
    numbered = 0
    for sales_invoice_id, organization_id, invoice_date in cursor.fetchall():
        give_invoice_a_number(cursor, organization_id, sales_invoice_id, invoice_date)
        numbered += 1
    return numbered


def clean_lc_reference(text, max_length=100):
    """The LC Reference box: free text, tidied, empty means none."""
    text = " ".join((text or "").split())
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"LC Reference is too long (maximum {max_length} characters).")
    return text


def clean_date(text):
    """A date box as the browser sends it. Empty means none; nonsense is refused."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Please give a real date.")


# ---------------------------------------------------------------------------
# Duplicating and deleting a sales invoice
#
# Both are the gear menu on the invoice's own page (core/docmenu.py). They live
# here, not in the page, because they must leave the books exactly as they
# found them:
#
#   * a duplicate is a NEW DRAFT. It gets its own number, its own lines and its
#     own draft journal entry; the invoice it was copied from is not touched
#     and nothing is posted, so the ledger does not change until someone posts
#     the copy deliberately.
#   * only a DRAFT can be deleted. A posted invoice is in the books, and taking
#     a posted document out of the ledger is not a "delete" - it is a
#     correction, and it has to be seen by the person who keeps the accounts.
#     The menu says so instead of failing quietly.
# ---------------------------------------------------------------------------
def _due_date_for(cursor, organization_id, customer_id, invoice_date, source_due, source_date):
    """The copy's due date: the customer's own terms, else the same gap as the source."""
    cursor.execute("SELECT payment_terms_days FROM customers WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, organization_id))
    row = cursor.fetchone()
    days = row[0] if row else None
    if days:
        return invoice_date + timedelta(days=int(days))
    if source_due and source_date:
        return invoice_date + (source_due - source_date)
    return None


def duplicate_invoice(cursor, organization_id, sales_invoice_id, today=None):
    """Copy an invoice into a new draft. Returns (new_id, new_number).

    Raises ValueError when the invoice is not this company's.
    """
    today = today or date.today()
    cursor.execute("""
        SELECT customer_id, invoice_date, due_date, payment_terms, journal_name,
               delivery_date, lc_reference
        FROM sales_invoices
        WHERE sales_invoice_id = %s AND organization_id = %s
    """, (sales_invoice_id, organization_id))
    source = cursor.fetchone()
    if not source:
        raise ValueError("That invoice is not one of this company's invoices.")
    customer_id, source_date, source_due, payment_terms, journal_name, delivery_date, lc_reference = source
    due_date = _due_date_for(cursor, organization_id, customer_id, today, source_due, source_date)

    cursor.execute("""
        INSERT INTO sales_invoices
            (organization_id, customer_id, invoice_date, due_date, payment_terms,
             journal_name, delivery_date, lc_reference, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Draft')
        RETURNING sales_invoice_id
    """, (organization_id, customer_id, today, due_date, payment_terms,
          journal_name, delivery_date, lc_reference))
    new_id = cursor.fetchone()[0]

    # The same lines, in the same order, with the same tax and discount.
    cursor.execute("""
        INSERT INTO sales_invoice_lines
            (sales_invoice_id, product_id, label, account_id, unit, quantity, rate,
             discount_percent, discount_amount, sales_tax_rate_id, tax_amount, total, line_order)
        SELECT %s, product_id, label, account_id, unit, quantity, rate,
               discount_percent, discount_amount, sales_tax_rate_id, tax_amount, total, line_order
        FROM sales_invoice_lines
        WHERE sales_invoice_id = %s
        ORDER BY line_order
    """, (new_id, sales_invoice_id))

    new_number = give_invoice_a_number(cursor, organization_id, new_id, today)

    # Its own draft journal entry, copied from the source's, so that posting the
    # copy writes the same account lines the original would have.
    cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s", (sales_invoice_id,))
    source_header_id = (cursor.fetchone() or [None])[0]
    if source_header_id:
        cursor.execute("""
            SELECT account_id, partner_name, label, debit, credit
            FROM journal_lines WHERE journal_header_id = %s ORDER BY line_order, journal_line_id
        """, (source_header_id,))
        cursor.execute("SELECT name FROM customers WHERE customer_id = %s", (customer_id,))
        customer_name = (cursor.fetchone() or [""])[0]
        reference = f"Invoice {new_number} - {customer_name}"
        # The same accounts and the same money. Any line that names the source
        # invoice is relabelled with the copy's own number, so a report can never
        # show the copy under the original's name.
        lines = []
        for account_id, partner_name, label, debit, credit in cursor.fetchall():
            if label and label.startswith("Customer Invoice "):
                label = reference
            lines.append({"account_id": account_id, "partner_name": partner_name, "label": label,
                          "debit": debit, "credit": credit})
        from accounting.services import create_journal_entry
        new_header_id = create_journal_entry(
            cursor, organization_id=organization_id, reference=reference,
            accounting_date=today, journal_name=journal_name or "Customer Invoices",
            lines=lines, source_type="SalesInvoice", source_id=new_id, auto_post=False)
        cursor.execute("UPDATE sales_invoices SET journal_header_id = %s WHERE sales_invoice_id = %s",
                       (new_header_id, new_id))
    return new_id, new_number


def delete_draft_invoice(cursor, organization_id, sales_invoice_id):
    """Delete a DRAFT invoice, its lines and its draft journal entry.

    A posted invoice is refused (ValueError). Returns the number of the invoice
    that was deleted, for the message on the list.
    """
    cursor.execute("SELECT invoice_no, status, journal_header_id FROM sales_invoices "
                   "WHERE sales_invoice_id = %s AND organization_id = %s",
                   (sales_invoice_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise ValueError("That invoice is not one of this company's invoices.")
    invoice_no, status, header_id = row
    if (status or "").lower() != "draft":
        raise ValueError(f"{invoice_no or 'That invoice'} is posted, so it cannot be deleted.")
    if header_id:
        cursor.execute("SELECT status FROM journal_headers WHERE journal_header_id = %s", (header_id,))
        header = cursor.fetchone()
        if header and (header[0] or "").lower() != "draft":
            raise ValueError("Its journal entry is already posted, so the invoice cannot be deleted.")
        # the invoice is unlinked first: it names the entry, and the entry cannot
        # go while a document still points at it
        cursor.execute("UPDATE sales_invoices SET journal_header_id = NULL WHERE sales_invoice_id = %s",
                       (sales_invoice_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id = %s", (header_id,))
        cursor.execute("DELETE FROM journal_headers WHERE journal_header_id = %s", (header_id,))
    cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id = %s", (sales_invoice_id,))
    cursor.execute("DELETE FROM sales_invoices WHERE sales_invoice_id = %s", (sales_invoice_id,))
    return invoice_no
