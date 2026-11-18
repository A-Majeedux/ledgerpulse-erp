"""
tests/test_audit_trail.py

Who posted that invoice, and who took it back to a draft?

A posted invoice is a statement to somebody outside the company. When it goes
back to a draft the books move, and the person reading the invoice six months on
should not have to guess who did it. Every document keeps its own short history,
written inside the same transaction as the change - so a document never moves
without a note saying who moved it.

These tests sign in as two people and check that the document's own screen tells
the two of them apart.
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member


class _TwoPeople(unittest.TestCase):
    """One company, one bill, and two people who both may post."""

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
        cls.tag = tag
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_AUDIT_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cls.people = {}
        for who in ("amina", "bilal"):
            cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                           "VALUES (%s, %s, 'hash', TRUE) RETURNING user_id",
                           (who.title(), f"{who}_{tag}@audit.local"))
            cls.people[who] = cursor.fetchone()[0]
            member(cursor, cls.people[who], cls.org_id)

        cls.accounts = {}
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("4000", "Sales", "Revenue"),
                                 ("5000", "Cost of Goods Sold", "Expense"),
                                 ("1200", "Stock on Hand", "Asset"),
                                 ("2000", "Trade Payables", "Liability")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]

        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) "
                       "VALUES (%s, 1, 'Audited Customer') RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) "
                       "VALUES (%s, 'Audited Vendor') RETURNING vendor_id", (cls.org_id,))
        cls.vendor_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

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
            "DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
            "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)",
            "DELETE FROM vendor_bills WHERE organization_id = %s",
            "DELETE FROM journal_lines WHERE journal_header_id IN "
            "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
            "DELETE FROM journal_headers WHERE organization_id = %s",
            "DELETE FROM customers WHERE organization_id = %s",
            "DELETE FROM vendors WHERE organization_id = %s",
            "DELETE FROM chart_of_accounts WHERE organization_id = %s",
            "DELETE FROM organization_memberships WHERE organization_id = %s",
            "DELETE FROM organizations WHERE organization_id = %s",
        ):
            cursor.execute(statement, (cls.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(cls.people.values()),))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transaction_history WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        # A bill points at its entry, so the bill goes first.
        cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
                       "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()

    def sign_in(self, who):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.people[who]
            flask_session["user_name"] = who.title()
            flask_session["csrf_token"] = "test-csrf"

    def make_invoice(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date,
                                        status, invoice_no)
            VALUES (%s, %s, '2026-11-01', '2026-12-01', 'Draft', %s)
            RETURNING sales_invoice_id
        """, (self.org_id, self.customer_id, f"INV/AUD/{self.tag[:4]}"))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines (sales_invoice_id, account_id, label, quantity, rate, total, line_order)
            VALUES (%s, %s, 'Something sold', 2, 500, 1000, 0)
        """, (invoice_id, self.accounts["4000"]))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def diary(self, invoice_id):
        from core.audit import history
        return history(self.org_id, "SalesInvoice", invoice_id)

    # -- the trail itself --------------------------------------------------

    def test_posting_writes_down_who_posted_it(self):
        invoice_id = self.make_invoice()
        self.sign_in("amina")
        self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        entries = self.diary(invoice_id)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "Posted")
        self.assertEqual(entries[0]["who"], "Amina")

    def test_the_two_people_are_told_apart(self):
        invoice_id = self.make_invoice()
        self.sign_in("amina")
        self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        self.sign_in("bilal")
        self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        entries = self.diary(invoice_id)
        self.assertEqual([entry["action"] for entry in entries], ["Reset", "Posted"],
                         "newest first")
        self.assertEqual(entries[0]["who"], "Bilal")   # who undid it
        self.assertEqual(entries[1]["who"], "Amina")   # who did it

    def test_the_invoice_screen_shows_the_trail(self):
        invoice_id = self.make_invoice()
        self.sign_in("amina")
        self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        body = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("Activity", body)
        self.assertIn("Amina", body)
        self.assertIn("posted", body.lower())

    def test_a_draft_says_nothing_has_happened_to_it(self):
        invoice_id = self.make_invoice()
        self.sign_in("amina")
        body = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("Nothing has happened to this document yet", body)

    def test_deleting_leaves_the_note_behind(self):
        invoice_id = self.make_invoice()
        self.sign_in("amina")
        self.client.post(f"/sales/invoices/{invoice_id}/delete", data={"csrf_token": "test-csrf"})
        entries = self.diary(invoice_id)
        self.assertEqual(entries[0]["action"], "Deleted")
        self.assertEqual(entries[0]["who"], "Amina")

    def make_bill(self):
        """A posted bill written straight into the tables, so posting is the only step left."""
        from core.db import get_conn
        from accounting.services import create_journal_entry, post_journal_entry
        conn = get_conn()
        cursor = conn.cursor()
        header = create_journal_entry(
            cursor, organization_id=self.org_id, reference=f"BILL/AUD/{self.tag[:4]}",
            accounting_date="2026-11-02", journal_name="Vendor Bills",
            lines=[
                {"account_id": self.accounts["5000"], "partner_name": "Audited Vendor",
                 "label": "Something bought", "debit": 1000, "credit": 0},
                {"account_id": self.accounts["2000"], "partner_name": "Audited Vendor",
                 "label": "Something bought", "debit": 0, "credit": 1000},
            ], source_type="VendorBill", auto_post=False,
        )
        cursor.execute("""
            INSERT INTO vendor_bills (organization_id, vendor_id, bill_date, due_date, bill_number,
                                      status, amount_due, journal_header_id)
            VALUES (%s, %s, '2026-11-02', '2026-12-02', %s, 'Draft', 1000, %s)
            RETURNING vendor_bill_id
        """, (self.org_id, self.vendor_id, f"BILL/AUD/{self.tag[:4]}", header))
        bill_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, label, quantity, rate, total, line_order)
            VALUES (%s, %s, 'Something bought', 1, 1000, 1000, 0)
        """, (bill_id, self.accounts["5000"]))
        cursor.execute("UPDATE journal_headers SET source_id = %s WHERE journal_header_id = %s",
                       (bill_id, header))
        conn.commit()
        cursor.close()
        conn.close()
        return bill_id

    def test_a_bill_keeps_its_own_trail_too(self):
        bill_id = self.make_bill()
        self.sign_in("bilal")
        self.client.post(f"/vendors/bills/{bill_id}/post", data={"csrf_token": "test-csrf"})
        self.client.post(f"/vendors/bills/{bill_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        from core.audit import history
        entries = history(self.org_id, "VendorBill", bill_id)
        self.assertEqual([entry["action"] for entry in entries], ["Reset", "Posted"])
        self.assertTrue(all(entry["who"] == "Bilal" for entry in entries))
        body = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("Activity", body)

    def test_the_note_and_the_change_are_one_thing(self):
        """A reset that is refused must not leave 'Reset' behind in the diary."""
        from core.db import get_conn
        invoice_id = self.make_invoice()
        conn = get_conn()
        cursor = conn.cursor()
        # a draft has never been posted, so resetting it must be refused
        cursor.execute("UPDATE sales_invoices SET status = 'Draft' WHERE sales_invoice_id = %s", (invoice_id,))
        conn.commit()
        cursor.close()
        conn.close()
        self.sign_in("amina")
        self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.diary(invoice_id), [],
                         "the diary recorded a reset that never happened")


if __name__ == "__main__":
    unittest.main()
