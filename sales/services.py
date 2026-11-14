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
from datetime import date

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
