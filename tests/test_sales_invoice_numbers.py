"""
tests/test_sales_invoice_numbers.py

Checks for the number on a sales invoice (sales/services.py): the shape of a
number, that it goes up, that it starts again each year, that it is never
repeated, that every company has its own, that the invoices made before
numbering existed get theirs once, and that the screens show it.

Needs DATABASE_URL set to a TEST database, like the other database tests:

    python -m pytest tests/test_sales_invoice_numbers.py -q
"""
import unittest
import uuid

from sales.services import (
    clean_date, clean_lc_reference, give_invoice_a_number, invoice_number,
    next_invoice_number, number_missing_invoices, year_of,
)


class InvoiceNumberRuleTests(unittest.TestCase):
    """The parts that need no database."""

    def test_the_shape_of_a_number(self):
        self.assertEqual(invoice_number(2026, 1), "INV/2026/00001")
        self.assertEqual(invoice_number(2026, 1749), "INV/2026/01749")   # as on his own screen
        self.assertEqual(invoice_number(2026, 123456), "INV/2026/123456")  # more digits is fine
        self.assertEqual(year_of("2026-11-13"), 2026)
        from datetime import date
        self.assertEqual(year_of(date(2027, 1, 1)), 2027)

    def test_the_lc_reference_box(self):
        self.assertEqual(clean_lc_reference("  LC 123/2026 "), "LC 123/2026")
        self.assertEqual(clean_lc_reference("LC   123"), "LC 123")     # tidied
        self.assertIsNone(clean_lc_reference(""))
        self.assertIsNone(clean_lc_reference(None))
        with self.assertRaises(ValueError):
            clean_lc_reference("x" * 101)

    def test_the_date_boxes(self):
        from datetime import date
        self.assertEqual(clean_date("2026-11-13"), date(2026, 11, 13))
        self.assertIsNone(clean_date(""))
        self.assertIsNone(clean_date(None))
        for bad in ("13/11/2026", "yesterday", "2026-13-01"):
            with self.assertRaises(ValueError):
                clean_date(bad)


class InvoiceNumberDatabaseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app = flask_app.app
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cls.org_name = f"TEST_INVOICE_NUMBERS_{uuid.uuid4().hex[:8]}"
        cls.other_org_name = f"TEST_INVOICE_NUMBERS_OTHER_{uuid.uuid4().hex[:8]}"
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (cls.org_name,))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (cls.other_org_name,))
        cls.other_org_id = cursor.fetchone()[0]
        cls.user_email = f"test_{uuid.uuid4().hex[:8]}@invoice-numbers.local"
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Invoice Number Tester', %s, 'hash', TRUE) RETURNING user_id", (cls.user_email,))
        cls.user_id = cursor.fetchone()[0]
        # 1100 Trade Receivables (the invoice debits it) and 4000 Sales Revenue
        for org_id in (cls.org_id, cls.other_org_id):
            for code, name, kind in (("1100", "Trade Receivables", "Asset"), ("4000", "Sales Revenue", "Revenue")):
                cursor.execute("""
                    INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                """, (org_id, code, name, kind))
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in (cls.org_id, cls.other_org_id):
            for table in ("sales_invoice_lines",):
                cursor.execute(f"DELETE FROM {table} WHERE sales_invoice_id IN "
                               "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                           "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM products WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM customers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    # -- helpers ---------------------------------------------------------
    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in (self.org_id, self.other_org_id):
            cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                           "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                           "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM products WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM customers WHERE organization_id = %s", (org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Invoice Number Tester"
            flask_session["csrf_token"] = "test-csrf"

    def raw_cursor(self):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    def make_customer(self, name="Numbered Traders", org_id=None):
        conn, cursor = self.raw_cursor()
        cursor.execute("INSERT INTO customers (organization_id, name) VALUES (%s, %s) RETURNING customer_id",
                       (org_id or self.org_id, name))
        customer_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return customer_id

    def make_invoice(self, customer_id, day, org_id=None, number_it=True):
        """An invoice written straight into the table, as the old version would have."""
        from datetime import date
        conn, cursor = self.raw_cursor()
        cursor.execute("INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date, status) "
                       "VALUES (%s, %s, %s, %s, 'Draft') RETURNING sales_invoice_id",
                       (org_id or self.org_id, customer_id, day, day))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, line_order, label, quantity, rate, total) "
                       "VALUES (%s, 0, 'Goods', 1, 100, 100)", (invoice_id,))
        if number_it:
            give_invoice_a_number(cursor, org_id or self.org_id, invoice_id, date.fromisoformat(day))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def number_of(self, invoice_id):
        conn, cursor = self.raw_cursor()
        cursor.execute("SELECT invoice_no FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        number = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return number

    def make_product_and_accounts(self):
        conn, cursor = self.raw_cursor()
        cursor.execute("INSERT INTO products (organization_id, name, unit) VALUES (%s, 'White Bottle', 'Units') "
                       "RETURNING product_id", (self.org_id,))
        product_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '4000'",
                       (self.org_id,))
        account_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return product_id, account_id

    # -- the numbers -----------------------------------------------------
    def test_the_first_invoice_is_number_one(self):
        customer_id = self.make_customer()
        invoice_id = self.make_invoice(customer_id, "2026-11-13")
        self.assertEqual(self.number_of(invoice_id), "INV/2026/00001")

    def test_the_numbers_go_up_and_start_again_each_year(self):
        customer_id = self.make_customer()
        first = self.make_invoice(customer_id, "2026-11-13")
        second = self.make_invoice(customer_id, "2026-11-14")
        next_year = self.make_invoice(customer_id, "2027-01-02")
        self.assertEqual(self.number_of(first), "INV/2026/00001")
        self.assertEqual(self.number_of(second), "INV/2026/00002")
        self.assertEqual(self.number_of(next_year), "INV/2027/00001")   # a new year starts again
        third = self.make_invoice(customer_id, "2026-12-31")
        self.assertEqual(self.number_of(third), "INV/2026/00003")

    def test_every_company_numbers_its_own_invoices(self):
        ours = self.make_invoice(self.make_customer(org_id=self.org_id), "2026-11-13")
        their_customer = self.make_customer("Other Co", org_id=self.other_org_id)
        theirs = self.make_invoice(their_customer, "2026-11-13", org_id=self.other_org_id)
        self.assertEqual(self.number_of(ours), "INV/2026/00001")
        self.assertEqual(self.number_of(theirs), "INV/2026/00001")      # both are number one

    def test_a_number_is_never_used_twice(self):
        customer_id = self.make_customer()
        numbers = [self.number_of(self.make_invoice(customer_id, "2026-11-13")) for _ in range(5)]
        self.assertEqual(numbers, [f"INV/2026/{number:05d}" for number in range(1, 6)])
        self.assertEqual(len(set(numbers)), 5)

    def test_the_next_number_is_what_the_number_after_next_will_be(self):
        conn, cursor = self.raw_cursor()
        self.assertEqual(next_invoice_number(cursor, self.org_id, "2026-11-13"), "INV/2026/00001")
        cursor.close()
        conn.close()
        customer_id = self.make_customer()
        self.make_invoice(customer_id, "2026-11-13")
        conn, cursor = self.raw_cursor()
        self.assertEqual(next_invoice_number(cursor, self.org_id, "2026-11-20"), "INV/2026/00002")
        self.assertEqual(next_invoice_number(cursor, self.org_id, "2027-02-01"), "INV/2027/00001")
        cursor.close()
        conn.close()

    # -- the invoices made before numbering existed ----------------------
    def test_old_invoices_are_numbered_once_oldest_first(self):
        customer_id = self.make_customer()
        newest = self.make_invoice(customer_id, "2026-11-20", number_it=False)
        oldest = self.make_invoice(customer_id, "2026-03-01", number_it=False)
        middle = self.make_invoice(customer_id, "2026-07-15", number_it=False)
        self.assertIsNone(self.number_of(newest))          # nothing yet

        conn, cursor = self.raw_cursor()
        numbered = number_missing_invoices(cursor)
        conn.commit()
        cursor.close()
        conn.close()

        self.assertEqual(numbered, 3)
        self.assertEqual(self.number_of(oldest), "INV/2026/00001")
        self.assertEqual(self.number_of(middle), "INV/2026/00002")
        self.assertEqual(self.number_of(newest), "INV/2026/00003")

        # running it again changes nothing - nobody is renumbered
        conn, cursor = self.raw_cursor()
        self.assertEqual(number_missing_invoices(cursor), 0)
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.number_of(oldest), "INV/2026/00001")

    def test_a_new_invoice_takes_the_number_after_the_old_ones(self):
        customer_id = self.make_customer()
        old = self.make_invoice(customer_id, "2026-03-01", number_it=False)
        conn, cursor = self.raw_cursor()
        number_missing_invoices(cursor)
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.number_of(old), "INV/2026/00001")
        self.assertEqual(self.number_of(self.make_invoice(customer_id, "2026-11-30")), "INV/2026/00002")

    # -- the screens -----------------------------------------------------
    def post_invoice(self, **overrides):
        product_id, account_id = self.make_product_and_accounts()
        customer_id = self.make_customer()
        form = {
            "csrf_token": "test-csrf",
            "customer_id": str(customer_id),
            "invoice_date": "2026-11-13",
            "due_date": "2026-12-13",
            "payment_terms": "30 days",
            "product_id[]": str(product_id),
            "label[]": "Carton Tape",
            "account_id[]": str(account_id),
            "unit[]": "Units",
            "quantity[]": "2",
            "rate[]": "500",
            "discount_percent[]": "0",
            "sales_tax_rate_id[]": "",
            "lc_reference": "LC 123/2026",
            "delivery_date": "2026-11-12",
        }
        form.update(overrides)
        return self.client.post("/sales/invoices/new", data=form, follow_redirects=False)

    def read_invoice(self, invoice_id):
        conn, cursor = self.raw_cursor()
        cursor.execute("SELECT invoice_no, lc_reference, delivery_date, status FROM sales_invoices "
                       "WHERE sales_invoice_id = %s", (invoice_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    def test_saving_an_invoice_gives_it_a_number_and_keeps_the_two_new_boxes(self):
        from datetime import date
        response = self.post_invoice()
        self.assertEqual(response.status_code, 302)
        invoice_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        number, lc_reference, delivery_date, status = self.read_invoice(invoice_id)
        self.assertEqual(number, "INV/2026/00001")
        self.assertEqual(lc_reference, "LC 123/2026")
        self.assertEqual(delivery_date, date(2026, 11, 12))
        self.assertEqual(status, "Draft")

        # the page shows it, and so does the list
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("INV/2026/00001", page)
        self.assertIn("LC 123/2026", page)
        self.assertIn("12 Nov 2026", page)                  # the delivery date
        page = self.client.get("/sales/invoices").get_data(as_text=True)
        self.assertIn("INV/2026/00001", page)
        self.assertIn("Number", page)

    def test_the_journal_entry_carries_the_number(self):
        response = self.post_invoice()
        invoice_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        conn, cursor = self.raw_cursor()
        cursor.execute("""
            SELECT header.reference, line.label
            FROM sales_invoices invoice
            JOIN journal_headers header ON header.journal_header_id = invoice.journal_header_id
            JOIN journal_lines line ON line.journal_header_id = header.journal_header_id
            WHERE invoice.sales_invoice_id = %s AND line.debit > 0
        """, (invoice_id,))
        reference, label = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertIn("INV/2026/00001", reference)
        self.assertIn("INV/2026/00001", label)

    def test_a_second_invoice_gets_the_next_number(self):
        first = self.post_invoice()
        second = self.post_invoice()
        first_id = int(first.headers["Location"].rsplit("/", 1)[-1])
        second_id = int(second.headers["Location"].rsplit("/", 1)[-1])
        self.assertEqual(self.read_invoice(first_id)[0], "INV/2026/00001")
        self.assertEqual(self.read_invoice(second_id)[0], "INV/2026/00002")

    def test_a_bad_date_or_a_long_lc_reference_is_refused(self):
        page = self.post_invoice(delivery_date="next week").get_data(as_text=True)
        self.assertIn("Please give a real date", page)
        page = self.post_invoice(lc_reference="x" * 101).get_data(as_text=True)
        self.assertIn("LC Reference is too long", page)
        conn, cursor = self.raw_cursor()
        cursor.execute("SELECT COUNT(*) FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)           # nothing was saved
        cursor.close()
        conn.close()

    def test_the_customer_card_shows_the_number(self):
        response = self.post_invoice()
        invoice_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        conn, cursor = self.raw_cursor()
        cursor.execute("SELECT customer_id FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        customer_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        page = self.client.get(f"/customers/{customer_id}/edit?tab=sales").get_data(as_text=True)
        self.assertIn("INV/2026/00001", page)
        # and in the books tab, because the journal line carries it
        page = self.client.get(f"/customers/{customer_id}/edit?tab=accounting").get_data(as_text=True)
        self.assertIn("INV/2026/00001", page)


if __name__ == "__main__":
    unittest.main()
