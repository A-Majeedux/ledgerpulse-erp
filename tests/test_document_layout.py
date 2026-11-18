"""
tests/test_document_layout.py

A transaction's screen has to separate two things that are easy to confuse: what
the *document* says, and who it is *for*.

The reference systems (Sage, Odoo) lay a transaction out as cards: the customer
in one, the document's own dates and journal in another, the lines below them,
and the money in a narrow rail on the right. Read down the left and you have the
paper; read the rail and you have what is still owed and what you can do about
it.

This file holds that shape in place: two key-value cards, a tax breakdown by
rate, a rail that carries the money, and a register that says who posted each
invoice and whether it is overdue.
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member


class _OneInvoice(unittest.TestCase):
    """A company, a customer with details, and one taxed invoice."""

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
        tag = uuid.uuid4().hex[:8]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_LAYOUT_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Amina Khan', %s, 'hash', TRUE) RETURNING user_id",
                       (f"amina_{tag}@layout.local",))
        cls.user_id = cursor.fetchone()[0]
        member(cursor, cls.user_id, cls.org_id)

        cls.accounts = {}
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("1200", "Stock on Hand", "Asset"),
                                 ("2200", "Output Tax Payable", "Liability"),
                                 ("4000", "Sales", "Revenue"),
                                 ("5000", "Cost of Goods Sold", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]

        cursor.execute("INSERT INTO sales_tax_rates (organization_id, label, rate_percent) "
                       "VALUES (%s, %s, 18) RETURNING sales_tax_rate_id", (cls.org_id, "Sales Tax 18%"))
        cls.tax_rate_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO customers (organization_id, customer_no, name, ntn, strn, phone, email,
                                   address, city, province, nature_of_supply, registration_type)
            VALUES (%s, 7, 'Al-Noor Agri Traders', '2481936-4', '3277876154321', '0300 7362194',
                    'accounts@alnooragri.com.pk', 'Bohar Gate, Near Sabzi Mandi', 'Multan',
                    'Punjab', 'Agrichem sales', 'Registered')
            RETURNING customer_id
        """, (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, sales_price, default_account_id, is_active)
            VALUES (%s, 'Urea 50kg', 'Bag', 3000, %s, TRUE) RETURNING product_id
        """, (cls.org_id, cls.accounts["4000"]))
        cls.product_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        cls.invoice_id = cls.raise_invoice(due_date="2026-11-05")     # already past

    @classmethod
    def raise_invoice(cls, due_date):
        with cls.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["organization_id"] = cls.org_id
                flask_session["user_id"] = cls.user_id
                flask_session["user_name"] = "Amina Khan"
                flask_session["csrf_token"] = "test-csrf"
            response = client.post("/sales/invoices/new", data={
                "csrf_token": "test-csrf",
                "customer_id": str(cls.customer_id),
                "invoice_date": "2026-11-01",
                "due_date": due_date,
                "journal_name": "Customer Invoices",
                "product_id[]": [str(cls.product_id)],
                "label[]": ["Urea 50kg"],
                "account_id[]": [str(cls.accounts["4000"])],
                "unit[]": ["Bag"],
                "quantity[]": ["2"],
                "rate[]": ["3000"],
                "discount_percent[]": ["0"],
                "sales_tax_rate_id[]": [str(cls.tax_rate_id)],
            }, follow_redirects=False)
            assert response.status_code == 302, response.get_data(as_text=True)[:500]
            return int(response.headers["Location"].rstrip("/").split("/")[-1])

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for statement in (
            "DELETE FROM transaction_history WHERE organization_id = %s",
            "DELETE FROM stock_movements WHERE organization_id = %s",
            "DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
            "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)",
            "DELETE FROM sales_invoices WHERE organization_id = %s",
            "DELETE FROM journal_lines WHERE journal_header_id IN "
            "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
            "DELETE FROM journal_headers WHERE organization_id = %s",
            "DELETE FROM products WHERE organization_id = %s",
            "DELETE FROM sales_tax_rates WHERE organization_id = %s",
            "DELETE FROM customers WHERE organization_id = %s",
            "DELETE FROM chart_of_accounts WHERE organization_id = %s",
            "DELETE FROM organization_memberships WHERE organization_id = %s",
            "DELETE FROM organizations WHERE organization_id = %s",
        ):
            cursor.execute(statement, (cls.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def sign_in(self):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Amina Khan"
            flask_session["csrf_token"] = "test-csrf"

    def page(self):
        self.sign_in()
        response = self.client.get(f"/sales/invoices/{self.invoice_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    # ---- the two cards --------------------------------------------------

    def test_the_customer_and_the_invoice_are_two_cards(self):
        body = self.page()
        self.assertIn("lp-doc-grid", body)
        self.assertIn("lp-doc-rail", body)
        # one small table for the customer, one for the invoice itself
        self.assertEqual(body.count('class="lp-kv"'), 2,
                         "the customer and the invoice each need their own details table")
        self.assertIn("<h2>Customer</h2>", body)
        self.assertIn("<h2>Invoice details</h2>", body)

    def test_the_customer_card_holds_his_own_details(self):
        body = self.page()
        customer_card = body.split("<h2>Customer</h2>", 1)[1].split("</section>", 1)[0]
        for fact in ("Al-Noor Agri Traders", "2481936-4", "3277876154321", "0300 7362194",
                     "accounts@alnooragri.com.pk", "Bohar Gate", "Multan", "Registered"):
            self.assertIn(fact, customer_card, f"{fact} is a customer detail and belongs in his card")

    def test_the_invoice_card_holds_the_document_s_own_facts(self):
        body = self.page()
        invoice_card = body.split("<h2>Invoice details</h2>", 1)[1].split("</section>", 1)[0]
        for fact in ("Invoice number", "Invoice date", "Due date", "Payment terms", "Currency"):
            self.assertIn(fact, invoice_card)
        self.assertIn("Customer Invoices", invoice_card)          # the journal

    def test_a_customer_fact_never_leaks_into_the_invoice_card(self):
        body = self.page()
        invoice_card = body.split("<h2>Invoice details</h2>", 1)[1].split("</section>", 1)[0]
        self.assertNotIn("2481936-4", invoice_card, "the NTN belongs in the customer's card")

    # ---- the rail -------------------------------------------------------

    def test_the_rail_carries_the_money(self):
        body = self.page()
        rail = body.split('class="lp-doc-rail"', 1)[1]
        self.assertIn("Amount paid", rail)
        self.assertIn("Amount outstanding", rail)
        # 2 x 3,000 + 18% = 7,080.00, nothing paid yet
        self.assertIn("7080.00", rail)

    def test_the_rail_offers_the_work_in_one_place(self):
        # a fresh draft, so the rail is also offering the thing a draft needs
        draft_id = self.raise_invoice(due_date="2026-12-01")
        self.sign_in()
        body = self.client.get(f"/sales/invoices/{draft_id}").get_data(as_text=True)
        rail = body.split('class="lp-doc-rail"', 1)[1]
        for action in ("Post Invoice", "Print / PDF invoice", "Customer card", "Statement", "Add credit note"):
            self.assertIn(action, rail, f"{action} belongs in the rail")

    def test_a_posted_invoice_offers_no_post_button(self):
        self.sign_in()
        self.client.post(f"/sales/invoices/{self.invoice_id}/post", data={"csrf_token": "test-csrf"})
        body = self.client.get(f"/sales/invoices/{self.invoice_id}").get_data(as_text=True)
        rail = body.split('class="lp-doc-rail"', 1)[1]
        self.assertNotIn("Post Invoice", rail, "an invoice that is already posted cannot be posted again")

    # ---- the lines and the tax -----------------------------------------

    def test_the_tax_is_broken_down_by_rate(self):
        body = self.page()
        self.assertIn("Tax breakdown", body)
        self.assertIn("Sales Tax 18% on 6000.00", body)
        self.assertIn("1080.00", body)          # the tax itself

    def test_the_lines_carry_the_columns_the_paper_carries(self):
        body = self.page()
        for column in ("Description", "Qty", "Price", "Disc %", "Tax", "Net"):
            self.assertIn(column, body)
        self.assertIn("4000 · Sales", body)      # the account named under the description

    # ---- the register ---------------------------------------------------

    def test_the_list_says_who_posted_it(self):
        self.sign_in()
        self.client.post(f"/sales/invoices/{self.invoice_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/invoices/{self.invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/invoices/{self.invoice_id}/post", data={"csrf_token": "test-csrf"})
        body = self.client.get("/sales/invoices").get_data(as_text=True)
        self.assertIn("Posted by", body)
        self.assertIn("Amina Khan", body)

    def test_a_draft_has_no_posted_by(self):
        draft_id = self.raise_invoice(due_date="2026-12-01")
        self.sign_in()
        body = self.client.get("/sales/invoices").get_data(as_text=True)
        rows = body.split("<tbody>", 1)[1].split("</tr>")
        mine = [row for row in rows if f"/sales/invoices/{draft_id}" in row]
        self.assertEqual(len(mine), 1, "the draft invoice has no row in the register")
        self.assertIn("Draft", mine[0])
        self.assertNotIn("Amina Khan", mine[0],
                         "nobody has posted that draft, so nobody belongs in that column")

    def test_an_unpaid_invoice_past_its_date_is_marked_overdue(self):
        self.sign_in()
        self.client.post(f"/sales/invoices/{self.invoice_id}/post", data={"csrf_token": "test-csrf"})
        body = self.client.get("/sales/invoices").get_data(as_text=True)
        self.assertIn("Overdue", body)

    def test_the_register_shows_the_customer_s_telephone(self):
        self.sign_in()
        body = self.client.get("/sales/invoices").get_data(as_text=True)
        self.assertIn("Telephone", body)
        self.assertIn("0300 7362194", body)


if __name__ == "__main__":
    unittest.main()
