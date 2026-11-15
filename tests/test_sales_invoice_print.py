"""
tests/test_sales_invoice_print.py

Checks for the two Sales screens finished in the sixth round:

  * the print page of one sales invoice - one GET, one page, the number and
    the customer on it, every line counted, the totals adding up, and a
    company that does not own the invoice getting nothing;
  * the products screen as cards - the search box, Price and On hand on every
    card, and Price/On hand being kept when a product is saved.

Like the other database tests, this needs DATABASE_URL pointing at a TEST
database; every test makes its own company inside it and removes it again.

    python -m pytest tests/test_sales_invoice_print.py -q
"""
import unittest
import uuid


class _OneCompanyPerTest(unittest.TestCase):
    """The app, one client, and a company of this test's own."""

    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app = flask_app.app
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
            ("TEST_SALES_PRINT_" + uuid.uuid4().hex[:8],))
        self.org_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) "
            "VALUES ('Print Tester', %s, 'hash', TRUE) RETURNING user_id",
            (f"print_{uuid.uuid4().hex[:8]}@x.local",))
        self.user_id = cursor.fetchone()[0]
        # the accounts an invoice leans on
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("4000", "Sales Revenue", "Revenue"),
                                 ("2200", "Output Tax Payable", "Liability")):
            cursor.execute("""
                INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (self.org_id, code, name, kind))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Print Tester"
            flask_session["csrf_token"] = "test-csrf"

    def tearDown(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM customer_contacts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM products WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (self.user_id,))
        conn.commit()
        cursor.close()
        conn.close()

    # -- helpers ---------------------------------------------------------
    def raw(self):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    def sign_in(self, org_id, user_id):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = org_id
            flask_session["user_id"] = user_id
            flask_session["user_name"] = "Print Tester"
            flask_session["csrf_token"] = "test-csrf"

    def make_customer_and_product(self):
        conn, cursor = self.raw()
        cursor.execute("""
            INSERT INTO customers (organization_id, name, address, city, ntn)
            VALUES (%s, 'Gulf Agro Chemicals', 'Band road Pindi bhattian, Hafizabad', 'Hafizabad', '4900884-2')
            RETURNING customer_id
        """, (self.org_id,))
        customer_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, sales_price, on_hand)
            VALUES (%s, %s, 'Units', 1250.00, 140) RETURNING product_id
        """, (self.org_id, "Pymetrozine 60% WG"))
        product_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return customer_id, product_id

    def make_invoice(self, customer_id, product_id, number="INV/2026/01749"):
        conn, cursor = self.raw()
        cursor.execute("""
            INSERT INTO sales_invoices
                (organization_id, customer_id, invoice_date, due_date, delivery_date,
                 status, invoice_no, lc_reference, payment_terms)
            VALUES (%s, %s, '2026-11-12', '2026-11-13', '2026-11-12', 'Draft', %s, 'LC 123/2026', '30 days')
            RETURNING sales_invoice_id
        """, (self.org_id, customer_id, number))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '4000'",
                       (self.org_id,))
        account_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines
                (sales_invoice_id, product_id, label, account_id, unit, quantity, rate,
                 discount_percent, discount_amount, tax_amount, total, line_order)
            VALUES (%s, %s, %s, %s, 'Units', 1010, 9.40, 0, 0, 0, 9494.00, 0)
        """, (invoice_id, product_id, "Pymetrozine 60% WG", account_id))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id


class SalesInvoicePrintTests(_OneCompanyPerTest):

    def test_the_page_prints_the_invoice(self):
        customer_id, product_id = self.make_customer_and_product()
        invoice_id = self.make_invoice(customer_id, product_id)

        response = self.client.get(f"/sales/invoices/{invoice_id}/print")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        # the number and the customer, as on his own paper
        self.assertIn("INV/2026/01749", page)
        self.assertIn("Gulf Agro Chemicals", page)
        self.assertIn("4900884-2", page)                  # NTN
        self.assertIn("Band road Pindi bhattian", page)   # delivery address
        # a sales invoice carries no LC Reference - that is a purchase reference
        self.assertNotIn("LC 123/2026", page)
        self.assertNotIn("LC Reference", page)
        self.assertIn("12/11/2026", page)                 # invoice date
        self.assertIn("30 days", page)                    # payment terms
        # the line and its money, printed the way the vendor bills print
        self.assertIn("Pymetrozine 60% WG", page)
        self.assertIn("Account:", page)
        self.assertIn("9,494.00 Rs", page)
        # the frame: a toolbar to print, and the signature boxes at the bottom
        self.assertIn("print-toolbar", page)
        self.assertIn("Prepared By", page)

    def test_the_totals_add_up(self):
        customer_id, product_id = self.make_customer_and_product()
        invoice_id = self.make_invoice(customer_id, product_id)
        conn, cursor = self.raw()
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '4000'",
                       (self.org_id,))
        account_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines
                (sales_invoice_id, product_id, label, account_id, unit, quantity, rate,
                 discount_percent, discount_amount, tax_amount, total, line_order)
            VALUES (%s, %s, 'Handling', %s, 'Units', 2, 500, 0, 0, 180, 1180.00, 1)
        """, (invoice_id, product_id, account_id))
        conn.commit()
        cursor.close()
        conn.close()

        page = self.client.get(f"/sales/invoices/{invoice_id}/print").get_data(as_text=True)
        self.assertIn("Gross (Qty x Rate)", page)
        self.assertIn("10,494.00 Rs", page)   # 1010 x 9.40 + 2 x 500
        self.assertIn("180.00 Rs", page)      # the tax
        self.assertIn("10,674.00 Rs", page)   # 9,494 + 1,180

    def test_another_company_cannot_print_this_invoice(self):
        customer_id, product_id = self.make_customer_and_product()
        invoice_id = self.make_invoice(customer_id, product_id)

        conn, cursor = self.raw()
        cursor.execute(
            "INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
            ("TEST_SALES_PRINT_OTHER_" + uuid.uuid4().hex[:8],))
        other_org_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        try:
            self.sign_in(other_org_id, self.user_id)
            self.assertEqual(self.client.get(f"/sales/invoices/{invoice_id}/print").status_code, 404)
        finally:
            conn, cursor = self.raw()
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (other_org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (other_org_id,))
            conn.commit()
            cursor.close()
            conn.close()

    def test_the_invoice_page_offers_the_print_button(self):
        customer_id, product_id = self.make_customer_and_product()
        invoice_id = self.make_invoice(customer_id, product_id)
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("Print / Save PDF", page)
        self.assertIn(f"/sales/invoices/{invoice_id}/print", page)

    def test_an_invoice_that_does_not_exist(self):
        self.assertEqual(self.client.get("/sales/invoices/99999999/print").status_code, 404)


class ProductsCardTests(_OneCompanyPerTest):

    def test_the_cards_show_price_and_on_hand(self):
        self.make_customer_and_product()
        page = self.client.get("/sales/products").get_data(as_text=True)
        self.assertIn("Pymetrozine 60% WG", page)
        self.assertIn("Price", page)
        self.assertIn("On hand", page)
        self.assertIn("1250.00", page)
        self.assertIn("140", page)
        self.assertIn('type="search"', page)          # the search box on top
        self.assertIn("grid-template-columns", page)  # cards, not a table

    def test_the_search_box_narrows_the_cards(self):
        self.make_customer_and_product()
        conn, cursor = self.raw()
        cursor.execute("INSERT INTO products (organization_id, name, unit) VALUES (%s, 'Caustic Soda Flakes', 'Kg')",
                       (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()

        page = self.client.get("/sales/products?q=caustic").get_data(as_text=True)
        self.assertIn("Caustic Soda Flakes", page)
        self.assertNotIn("Pymetrozine", page)
        self.assertIn("1 of 2 product(s) match", page)

        empty = self.client.get("/sales/products?q=nothing-like-it").get_data(as_text=True)
        self.assertIn("No product matches", empty)

    def test_price_and_on_hand_are_saved(self):
        _customer_id, product_id = self.make_customer_and_product()

        edit = self.client.get(f"/sales/products/{product_id}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn("1250.00", edit.get_data(as_text=True))

        saved = self.client.post(f"/sales/products/{product_id}/edit", data={
            "name": "Pymetrozine 60% WG", "unit": "Units",
            "sales_price": "1375.50", "on_hand": "12.5",
            "default_account_id": "", "is_active": "on", "csrf_token": "test-csrf",
        }, follow_redirects=True)
        self.assertEqual(saved.status_code, 200)
        page = saved.get_data(as_text=True)
        self.assertIn("1375.50", page)
        self.assertIn("12.5", page)

        conn, cursor = self.raw()
        cursor.execute("SELECT sales_price, on_hand FROM products WHERE product_id = %s", (product_id,))
        price, stock = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(float(price), 1375.50)
        self.assertEqual(float(stock), 12.5)

    def test_a_price_that_is_not_a_number_is_refused(self):
        _customer_id, product_id = self.make_customer_and_product()
        response = self.client.post(f"/sales/products/{product_id}/edit", data={
            "name": "Pymetrozine 60% WG", "unit": "Units",
            "sales_price": "about a thousand", "on_hand": "140",
            "csrf_token": "test-csrf",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("must be a number", response.get_data(as_text=True))
        conn, cursor = self.raw()
        cursor.execute("SELECT sales_price FROM products WHERE product_id = %s", (product_id,))
        self.assertEqual(float(cursor.fetchone()[0]), 1250.00)   # unchanged
        cursor.close()
        conn.close()

    def test_a_new_product_keeps_its_price_and_stock(self):
        page = self.client.post("/sales/products/new", data={
            "name": "Glyphosate 41% SL", "unit": "Litre",
            "sales_price": "980", "on_hand": "60",
            "default_account_id": "", "csrf_token": "test-csrf",
        }, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Glyphosate 41% SL", page)
        self.assertIn("980.00", page)


if __name__ == "__main__":
    unittest.main()
