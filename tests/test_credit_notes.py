"""
tests/test_credit_notes.py

The customer sends the goods back.

The vendor side has had a credit note all along; the sales side had nothing, so a
return could only be squared off by hand in a journal entry nobody could print
for the customer. These tests follow one return through the whole app: the note
is created, posted, and every figure it touches is checked against the ledger and
the stock ledger - the revenue, the sales tax, the customer's balance, the goods
back on the shelf, and the invoice it was raised against.

The last test is the one that matters most: reset puts every one of those figures
back exactly as it was, and posting again lands in the same place. A return is
not a one-way door.
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member


class _OneReturn(unittest.TestCase):
    """A company that has sold five bags, and now gets two of them back."""

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
                       (f"TEST_CREDIT_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Accountant', %s, 'hash', TRUE) RETURNING user_id",
                       (f"credit_{tag}@notes.local",))
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

        # the label is passed, not written into the statement: a bare % in SQL
        # that also has parameters is a placeholder to psycopg2
        cursor.execute("INSERT INTO sales_tax_rates (organization_id, label, rate_percent) "
                       "VALUES (%s, %s, 18) RETURNING sales_tax_rate_id", (cls.org_id, "Sales Tax 18%"))
        cls.tax_rate_id = cursor.fetchone()[0]

        cursor.execute("INSERT INTO customers (organization_id, customer_no, name, nature_of_supply) "
                       "VALUES (%s, 1, 'Returning Customer', 'Supplies') RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, sales_price, default_account_id, is_active)
            VALUES (%s, 'Urea 50kg', 'Bag', 3000, %s, TRUE) RETURNING product_id
        """, (cls.org_id, cls.accounts["4000"]))
        cls.product_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        cls.invoice_id = cls.make_and_post_invoice()

    # ---- the sale that comes back ------------------------------------

    @classmethod
    def db(cls):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    @classmethod
    def make_and_post_invoice(cls):
        """Eight bags in at 2,000, five sold at 3,000 + 18% - through the app's own screens.

        The invoice is raised the way an invoice is really raised (its own form,
        which writes its entry), then posted. Nothing is written into the tables
        by hand, so what the return is tested against is what the app itself did.
        """
        from core import stock
        from accounting.services import create_journal_entry, post_journal_entry
        conn, cursor = cls.db()
        # the goods are bought in: 8 bags at 2,000
        header = create_journal_entry(
            cursor, organization_id=cls.org_id, reference="Opening purchase",
            accounting_date="2026-11-01", journal_name="Miscellaneous Operations",
            lines=[{"account_id": cls.accounts["1200"], "partner_name": None, "label": "stock in",
                    "debit": D("16000"), "credit": D("0")},
                   {"account_id": cls.accounts["5000"], "partner_name": None, "label": "stock in",
                    "debit": D("0"), "credit": D("16000")}], auto_post=False)
        post_journal_entry(cursor, header)
        stock.record(cursor, cls.org_id, cls.product_id, 8, D("2000"),
                     source_type="Opening", source_id=0, reference="Opening stock",
                     movement_date="2026-11-01")
        conn.commit()
        cursor.close()
        conn.close()

        with cls.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["organization_id"] = cls.org_id
                flask_session["user_id"] = cls.user_id
                flask_session["user_name"] = "Accountant"
                flask_session["csrf_token"] = "test-csrf"
            created = client.post("/sales/invoices/new", data={
                "csrf_token": "test-csrf",
                "customer_id": str(cls.customer_id),
                "invoice_date": "2026-11-02",
                "due_date": "2026-12-02",
                "journal_name": "Customer Invoices",
                "product_id[]": [str(cls.product_id)],
                "label[]": ["Urea 50kg"],
                "account_id[]": [str(cls.accounts["4000"])],
                "unit[]": ["Bag"],
                "quantity[]": ["5"],
                "rate[]": ["3000"],
                "discount_percent[]": ["0"],
                "sales_tax_rate_id[]": [str(cls.tax_rate_id)],
            }, follow_redirects=False)
            assert created.status_code == 302, created.get_data(as_text=True)[:600]
            invoice_id = int(created.headers["Location"].rstrip("/").split("/")[-1])
            posted = client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
            assert posted.status_code == 302, posted.get_data(as_text=True)[:600]
        return invoice_id

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for statement in (
            "DELETE FROM transaction_history WHERE organization_id = %s",
            "DELETE FROM stock_movements WHERE organization_id = %s",
            "DELETE FROM customer_credit_note_lines WHERE customer_credit_note_id IN "
            "(SELECT customer_credit_note_id FROM customer_credit_notes WHERE organization_id = %s)",
            "DELETE FROM customer_credit_notes WHERE organization_id = %s",
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

    def setUp(self):
        # every test starts from "the sale happened, no return yet"
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transaction_history WHERE organization_id = %s", (self.org_id,))
        # the previous test's credit notes go, with the entries they wrote -
        # an entry left behind would still be counted by the ledger
        cursor.execute("""
            DELETE FROM journal_lines WHERE journal_header_id IN (
                SELECT journal_header_id FROM journal_headers
                WHERE organization_id = %s AND source_type = 'CustomerCreditNote')
        """, (self.org_id,))
        cursor.execute("""
            DELETE FROM journal_headers
            WHERE organization_id = %s AND source_type = 'CustomerCreditNote'
        """, (self.org_id,))
        cursor.execute("DELETE FROM customer_credit_note_lines WHERE customer_credit_note_id IN "
                       "(SELECT customer_credit_note_id FROM customer_credit_notes WHERE organization_id = %s)",
                       (self.org_id,))
        cursor.execute("DELETE FROM customer_credit_notes WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM stock_movements WHERE organization_id = %s "
                       "AND source_type = 'CustomerCreditNote'", (self.org_id,))
        cursor.execute("UPDATE products SET on_hand = 3 WHERE product_id = %s", (self.product_id,))
        cursor.execute("UPDATE sales_invoices SET paid_amount = 0, status = 'Posted' "
                       "WHERE sales_invoice_id = %s", (self.invoice_id,))
        conn.commit()
        cursor.close()
        conn.close()

    def sign_in(self):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Accountant"
            flask_session["csrf_token"] = "test-csrf"

    # ---- helpers -------------------------------------------------------

    def raise_return(self, quantity=2, rate=3000, restock="on", tax=True, invoice=True):
        self.sign_in()
        data = {
            "csrf_token": "test-csrf",
            "customer_id": str(self.customer_id),
            "note_date": "2026-11-10",
            "reason": "Return",
            "product_id[]": [str(self.product_id)],
            "label[]": ["Urea 50kg returned"],
            "account_id[]": [str(self.accounts["4000"])],
            "unit[]": ["Bag"],
            "quantity[]": [str(quantity)],
            "rate[]": [str(rate)],
            "sales_tax_rate_id[]": [str(self.tax_rate_id) if tax else ""],
        }
        if restock:
            data["restock"] = "on"
        if invoice:
            data["sales_invoice_id"] = str(self.invoice_id)
        response = self.client.post("/sales/credit-notes/new", data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        return int(response.headers["Location"].rstrip("/").split("/")[-1])

    def balance_of(self, code):
        conn, cursor = self.db()
        cursor.execute("""
            SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
            FROM journal_lines jl
            JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
            JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
            WHERE jh.organization_id = %s AND jh.status = 'Posted' AND coa.code = %s
        """, (self.org_id, code))
        value = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return value

    def on_hand(self):
        conn, cursor = self.db()
        cursor.execute("SELECT on_hand FROM products WHERE product_id = %s", (self.product_id,))
        value = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return value

    def invoice_paid(self):
        conn, cursor = self.db()
        cursor.execute("SELECT COALESCE(paid_amount, 0) FROM sales_invoices WHERE sales_invoice_id = %s",
                       (self.invoice_id,))
        value = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return value

    def note_status(self, note_id):
        conn, cursor = self.db()
        cursor.execute("SELECT status, credit_note_no, total FROM customer_credit_notes "
                       "WHERE customer_credit_note_id = %s", (note_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    # ---- the document --------------------------------------------------

    def test_a_credit_note_is_numbered_and_saved_as_a_draft(self):
        note_id = self.raise_return()
        status, number, total = self.note_status(note_id)
        self.assertEqual(status, "Draft", "a new credit note must not touch the books")
        self.assertTrue(number.startswith("CN/2026/"), number)
        self.assertEqual(total, D("7080.00"))          # 2 x 3,000 + 18%

    def test_a_draft_credit_note_changes_nothing(self):
        self.raise_return()
        self.assertEqual(self.balance_of("4000"), D("-15000.00"))   # the sale, untouched
        self.assertEqual(self.on_hand(), 3)

    def test_the_list_and_detail_screens_open(self):
        note_id = self.raise_return()
        self.sign_in()
        self.assertEqual(self.client.get("/sales/credit-notes").status_code, 200)
        detail = self.client.get(f"/sales/credit-notes/{note_id}")
        self.assertEqual(detail.status_code, 200)
        body = detail.get_data(as_text=True)
        self.assertIn("Urea 50kg returned", body)
        self.assertIn("Activity", body)

    # ---- posting -------------------------------------------------------

    def test_posting_takes_the_sale_and_the_tax_back(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.note_status(note_id)[0], "Posted")
        # revenue was -15,000 for five bags; two come back, so 6,000 goes back
        self.assertEqual(self.balance_of("4000"), D("-9000.00"))
        # and so does the 18% that was charged on them: 18% of the 6,000 returned
        self.assertEqual(self.balance_of("2200"), D("-1620.00"))    # was -2,700

    def test_the_customer_owes_that_much_less(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        # receivables were 17,700; the returned 7,080 is no longer owed
        self.assertEqual(self.balance_of("1100"), D("10620.00"))
        self.assertEqual(self.invoice_paid(), D("7080.00"), "the invoice must show it is settled to that extent")

    def test_the_goods_come_back_onto_the_shelf_at_what_they_cost(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.on_hand(), 5)          # 3 + the 2 that came back
        conn, cursor = self.db()
        cursor.execute("""
            SELECT quantity, unit_cost FROM stock_movements
            WHERE organization_id = %s AND source_type = 'CustomerCreditNote' AND source_id = %s
        """, (self.org_id, note_id))
        quantity, unit_cost = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(quantity, D("2.000"))
        self.assertEqual(unit_cost, D("2000.0000"),
                         "goods must come back at what they cost, not at today's average")

    def test_the_cost_comes_out_of_the_expense_with_the_goods(self):
        before = (self.balance_of("1200"), self.balance_of("5000"))
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        after = (self.balance_of("1200"), self.balance_of("5000"))
        self.assertEqual(after[0] - before[0], D("4000.00"), "the goods are stock again")
        self.assertEqual(after[1] - before[1], D("-4000.00"), "and no longer an expense")

    def test_the_stock_account_agrees_with_the_shelf_after_a_return(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        conn, cursor = self.db()
        cursor.execute("SELECT COALESCE(SUM(value), 0) FROM stock_movements WHERE organization_id = %s",
                       (self.org_id,))
        shelf = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(self.balance_of("1200"), shelf)

    def test_an_allowance_puts_no_goods_back(self):
        note_id = self.raise_return(quantity=1, restock=None)
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.on_hand(), 3)
        self.assertEqual(self.balance_of("4000"), D("-12000.00"))

    def test_posting_twice_is_refused(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.balance_of("4000"), D("-9000.00"), "the sale was credited twice")

    def test_the_entry_it_writes_is_balanced(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        conn, cursor = self.db()
        cursor.execute("""
            SELECT SUM(debit), SUM(credit) FROM journal_lines
            WHERE journal_header_id = (SELECT journal_header_id FROM customer_credit_notes
                                       WHERE customer_credit_note_id = %s)
        """, (note_id,))
        debit, credit = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(debit, credit)
        # the entry carries what the customer owes (7,080) and, because the goods
        # came back, the 4,000 they cost when they left
        self.assertEqual(debit, D("11080.00"))

    # ---- taking it back ------------------------------------------------

    def test_reset_puts_every_figure_back(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/credit-notes/{note_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.note_status(note_id)[0], "Draft")
        self.assertEqual(self.balance_of("4000"), D("-15000.00"))
        self.assertEqual(self.balance_of("2200"), D("-2700.00"))
        self.assertEqual(self.balance_of("1100"), D("17700.00"))
        self.assertEqual(self.invoice_paid(), D("0.00"))
        self.assertEqual(self.on_hand(), 3)

    def test_posting_after_a_reset_lands_in_the_same_place(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        once = (self.balance_of("4000"), self.balance_of("1100"), self.on_hand(), self.invoice_paid())
        self.client.post(f"/sales/credit-notes/{note_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        twice = (self.balance_of("4000"), self.balance_of("1100"), self.on_hand(), self.invoice_paid())
        self.assertEqual(once, twice)

    def test_a_posted_note_cannot_be_deleted(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/credit-notes/{note_id}/delete", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.note_status(note_id)[0], "Posted")

    def test_a_draft_note_can_be_deleted(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/delete", data={"csrf_token": "test-csrf"})
        self.assertIsNone(self.note_status(note_id))

    # ---- the paper -----------------------------------------------------

    def test_the_credit_note_prints(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        response = self.client.get(f"/sales/credit-notes/{note_id}/print")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Credit Note", body)
        self.assertIn("Returning Customer", body)
        self.assertIn("7080.00", body)

    def test_the_note_keeps_its_own_history(self):
        note_id = self.raise_return()
        self.client.post(f"/sales/credit-notes/{note_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/credit-notes/{note_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        from core.audit import history
        entries = history(self.org_id, "CustomerCreditNote", note_id)
        self.assertEqual([entry["action"] for entry in entries], ["Reset", "Posted", "Created"])
        self.assertEqual(entries[1]["who"], "Accountant")


class _WhoMayRaiseOne(unittest.TestCase):
    """A reader may see a credit note and must not be able to raise or post one."""

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
                       (f"TEST_CREDIT_PERM_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Reader', %s, 'hash', TRUE) RETURNING user_id", (f"reader_{tag}@notes.local",))
        cls.user_id = cursor.fetchone()[0]
        from _people import viewer
        viewer(cursor, cls.user_id, cls.org_id)
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def sign_in(self):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Reader"
            flask_session["csrf_token"] = "test-csrf"

    def test_a_reader_may_look(self):
        self.sign_in()
        self.assertEqual(self.client.get("/sales/credit-notes").status_code, 200)

    def test_a_reader_may_not_raise_one(self):
        self.sign_in()
        self.assertEqual(self.client.get("/sales/credit-notes/new").status_code, 403)
        self.assertEqual(self.client.post("/sales/credit-notes/new", data={"csrf_token": "test-csrf"}).status_code, 403)

    def test_a_reader_may_not_post_or_reset_one(self):
        self.sign_in()
        self.assertEqual(self.client.post("/sales/credit-notes/1/post",
                                          data={"csrf_token": "test-csrf"}).status_code, 403)
        self.assertEqual(self.client.post("/sales/credit-notes/1/reset-to-draft",
                                          data={"csrf_token": "test-csrf"}).status_code, 403)

    def test_the_reader_is_not_shown_the_new_button(self):
        self.sign_in()
        body = self.client.get("/sales/credit-notes").get_data(as_text=True)
        self.assertNotIn("New Credit Note", body)


if __name__ == "__main__":
    unittest.main()
