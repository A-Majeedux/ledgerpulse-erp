"""
tests/test_vendors_module.py

The Purchases screens, checked against the reference system's paper.

Three things this round added, and each is tried here by doing it:

  * **the two faces of a bill** - Invoice Lines, Journal Items, Other Info, with
    the ledger entry reachable from the tab that shows it;
  * **the voucher's two tabs** - Cheques and Withholding;
  * **Pay** - a posted bill is paid from its own page, the payment is matched to
    it as it is saved, and the bill becomes paid. Over-paying is refused, and so
    is matching more than the payment is worth.

It also pins the thing that was quietly broken: printing the journal entry a
vendor document wrote used to ask for columns this ledger does not keep
(`voucher_number`, `entry_date`, `memo`, `analytic_tag`) and answered 500.
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member, viewer


class _OneCompany(unittest.TestCase):
    """A company with a chart, a bank, a vendor and a posted bill."""

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
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_VEND_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cls.people = {}
        for who in ("admin", "viewer"):
            cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                           "VALUES (%s, %s, 'hash', TRUE) RETURNING user_id",
                           (who.title(), f"{who}_{uuid.uuid4().hex[:8]}@vend.local"))
            cls.people[who] = cursor.fetchone()[0]
        member(cursor, cls.people["admin"], cls.org_id)
        viewer(cursor, cls.people["viewer"], cls.org_id)

        cls.accounts = {}
        for code, name, kind in (("2100", "Trade Payables", "Liability"),
                                 ("2110", "WHT Payable - Services", "Liability"),
                                 ("1010", "Meezan Bank", "Asset"),
                                 ("1100", "Trade Receivables", "Asset"),
                                 ("5000", "Office Expense", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Tab Suppliers') "
                       "RETURNING vendor_id", (cls.org_id,))
        cls.vendor_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN "
                       "(SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
                       "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(cls.people.values()),))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN "
                       "(SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
                       "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()

    def sign_in(self, who="admin"):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.people[who]
            flask_session["user_name"] = who.title()
            flask_session["csrf_token"] = "test-csrf"

    def make_bill(self, total="1000.00", status="Posted", number="BILL/2026/1", with_entry=True):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendor_bills (organization_id, vendor_id, bill_number, bill_date, due_date,
                                      status, journal_name, amount_due, paid_amount, receipt_date,
                                      accounting_date, payment_reference, lc_reference)
            VALUES (%s, %s, %s, '2026-11-01', '2026-12-01', %s, 'Expenses', %s, 0,
                    '2026-11-01', '2026-11-01', 'REF-9', 'LC/2026/7')
            RETURNING vendor_bill_id
        """, (self.org_id, self.vendor_id, number, status, total))
        bill_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, total, label, quantity, rate, "
                       "line_order, line_kind) VALUES (%s, %s, %s, 'Cartons', 1, %s, 0, 'Goods')",
                       (bill_id, self.accounts["5000"], total, total))
        if not with_entry:
            conn.commit()
            cursor.close()
            conn.close()
            return bill_id
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type, source_id)
            VALUES (%s, %s, '2026-11-01', 'Expenses', %s, 'VendorBill', %s) RETURNING journal_header_id
        """, (self.org_id, f"Vendor Bill {number}", status, bill_id))
        header = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, label, debit, credit, line_order) "
                       "VALUES (%s, %s, 'Cartons', %s, 0, 0)", (header, self.accounts["5000"], total))
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, debit, "
                       "credit, line_order) VALUES (%s, %s, 'Tab Suppliers', 'Payable', 0, %s, 1)",
                       (header, self.accounts["2100"], total))
        cursor.execute("UPDATE vendor_bills SET journal_header_id = %s WHERE vendor_bill_id = %s", (header, bill_id))
        conn.commit()
        cursor.close()
        conn.close()
        return bill_id

    def bill_state(self, bill_id):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT status, amount_due, COALESCE(paid_amount, 0) FROM vendor_bills "
                       "WHERE vendor_bill_id = %s", (bill_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    # -- the bill's three faces -------------------------------------------
    def test_the_bill_shows_its_lines_its_entry_and_its_terms(self):
        bill_id = self.make_bill()
        self.sign_in()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        for tab in ("Invoice Lines", "Journal Items", "Other Info"):
            self.assertIn(tab, page)
        self.assertEqual(page.count('class="lp-tab-panel"'), 3)
        self.assertIn("Cartons", page)                       # a line
        self.assertIn("Trade Payables", page)                # the entry it posted
        self.assertIn("REF-9", page)                         # the bill reference
        self.assertIn("LC/2026/7", page)                     # the LC reference
        self.assertIn("/accounting/journal-entries/", page)  # the entry is one click away

    def test_a_bill_with_no_entry_says_so_instead_of_showing_an_empty_tab(self):
        bill_id = self.make_bill(status="Draft", with_entry=False)
        self.sign_in()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("still a draft", page)

    def test_a_draft_bill_still_shows_its_draft_entry(self):
        bill_id = self.make_bill(status="Draft")
        self.sign_in()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("Trade Payables", page)                 # the entry exists, in draft
        self.assertNotIn("still a draft", page)

    # -- the voucher's two tabs -------------------------------------------
    def test_the_voucher_shows_its_cheque_and_its_withholding(self):
        bill_id = self.make_bill(total="1000.00")
        self.sign_in()
        response = self.client.post(f"/vendors/payments/new?bill_id={bill_id}", data={
            "csrf_token": "test-csrf", "vendor_id": self.vendor_id,
            "paid_from_account_id": self.accounts["1010"], "amount": "1000.00",
            "payment_date": "2026-11-14", "memo": "Payment against BILL/2026/1",
            "payment_method": "Cheque", "cheque_number": "CHQ-77", "cheque_date": "2026-11-14",
            "bill_id[]": bill_id, "alloc_amount[]": "1000.00",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Cheques", page)
        self.assertIn("Withholding", page)
        self.assertIn("CHQ-77", page)
        self.assertIn("No withholding tax was taken out of this payment", page)

    # -- Pay ---------------------------------------------------------------
    def test_pay_a_posted_bill_and_the_bill_becomes_paid(self):
        bill_id = self.make_bill(total="1000.00")
        self.sign_in()
        form = self.client.get(f"/vendors/payments/new?bill_id={bill_id}").get_data(as_text=True)
        self.assertIn("Pay bill BILL/2026/1", form)
        self.assertIn(f'name="bill_id[]" value="{bill_id}"', form)
        self.assertIn('name="alloc_amount[]" value="1000.00"', form)

        response = self.client.post(f"/vendors/payments/new?bill_id={bill_id}", data={
            "csrf_token": "test-csrf", "vendor_id": self.vendor_id,
            "paid_from_account_id": self.accounts["1010"], "amount": "1000.00",
            "payment_date": "2026-11-14", "memo": "Payment against BILL/2026/1",
            "payment_method": "Manual", "bill_id[]": bill_id, "alloc_amount[]": "1000.00",
        }, follow_redirects=True)
        self.assertIn("Voucher", response.get_data(as_text=True))
        status, amount_due, paid = self.bill_state(bill_id)
        self.assertEqual(status, "Paid")
        self.assertEqual(D(str(paid)), D("1000.00"))
        self.assertEqual(D(str(amount_due)), D("1000.00"))   # what the bill was for

    def test_a_payment_cannot_be_matched_past_what_the_bill_owes(self):
        bill_id = self.make_bill(total="1000.00")
        self.sign_in()
        response = self.client.post(f"/vendors/payments/new?bill_id={bill_id}", data={
            "csrf_token": "test-csrf", "vendor_id": self.vendor_id,
            "paid_from_account_id": self.accounts["1010"], "amount": "1500.00",
            "payment_date": "2026-11-14", "payment_method": "Manual",
            "bill_id[]": bill_id, "alloc_amount[]": "1500.00",
        }, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("still owes", page)
        self.assertEqual(self.bill_state(bill_id)[0], "Posted")          # untouched
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vendor_payments WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)                        # and nothing was written
        cursor.close()
        conn.close()

    def test_matching_more_than_the_payment_is_worth_is_refused(self):
        bill_id = self.make_bill(total="1000.00")
        self.sign_in()
        response = self.client.post(f"/vendors/payments/new?bill_id={bill_id}", data={
            "csrf_token": "test-csrf", "vendor_id": self.vendor_id,
            "paid_from_account_id": self.accounts["1010"], "amount": "400.00",
            "payment_date": "2026-11-14", "payment_method": "Manual",
            "bill_id[]": bill_id, "alloc_amount[]": "1000.00",
        }, follow_redirects=True)
        self.assertIn("raise the amount first", response.get_data(as_text=True))
        self.assertEqual(self.bill_state(bill_id)[2], 0)

    def test_a_draft_bill_is_not_offered_pay(self):
        bill_id = self.make_bill(status="Draft")
        self.sign_in()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertNotIn(f"/vendors/payments/new?bill_id={bill_id}", page)
        self.assertIn("Only a posted bill can be paid", page)

    # -- the prints that used to fall over ---------------------------------
    def test_the_entry_a_bill_wrote_can_be_printed(self):
        bill_id = self.make_bill()
        self.sign_in()
        response = self.client.get(f"/vendors/bills/{bill_id}/journal-print")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Journal Entry", page)
        self.assertIn("Written by", page)
        self.assertIn("Vendor Bill", page)
        self.assertNotIn("Analytic", page)          # a column this ledger does not keep
        self.assertIn("1,000.00 Rs", page)          # its own lines, and their total

    def test_the_menu_holds_both_prints_and_the_pay(self):
        from core.docmenu import build_menu
        import core.docmenu as menu_module
        original = menu_module._may
        menu_module._may = lambda *args: True
        try:
            posted = dict((item["key"], item) for item in
                          build_menu("vendor_bill", 9, status="Posted", user_id=1, organization_id=2))
            draft = dict((item["key"], item) for item in
                         build_menu("vendor_bill", 9, status="Draft", user_id=1, organization_id=2))
            payment = dict((item["key"], item) for item in build_menu("vendor_payment", 4, user_id=1,
                                                                      organization_id=2))
        finally:
            menu_module._may = original
        self.assertEqual(posted["print_journal"]["url"], "/vendors/bills/9/journal-print")
        self.assertEqual(payment["print_journal"]["url"], "/vendors/payments/4/journal-print")
        self.assertTrue(posted["pay"]["allowed"])
        self.assertFalse(draft["pay"]["allowed"])
        self.assertEqual(draft["pay"]["note"], "Only a posted bill can be paid - post it first.")
        self.assertEqual(posted["pay"]["url"], "/vendors/payments/new?bill_id=9")

    # -- role authority ----------------------------------------------------
    def test_a_viewer_may_read_the_purchases_and_change_none_of_it(self):
        bill_id = self.make_bill()
        self.sign_in("viewer")
        self.assertEqual(self.client.get("/vendors/bills").status_code, 200)
        self.assertEqual(self.client.get(f"/vendors/bills/{bill_id}").status_code, 200)
        attempts = [
            ("GET", "/vendors/new"), ("POST", "/vendors/new"),
            ("GET", "/vendors/bills/new"), ("POST", "/vendors/bills/new"),
            ("POST", f"/vendors/bills/{bill_id}/post"),
            ("GET", f"/vendors/bills/{bill_id}/edit"),
            ("POST", f"/vendors/bills/{bill_id}/duplicate"),
            ("POST", f"/vendors/bills/{bill_id}/delete"),
            ("GET", "/vendors/payments/new"), ("POST", "/vendors/payments/new"),
            ("GET", "/vendors/credit-notes/new"), ("POST", "/vendors/batch-payments/new"),
        ]
        for method, url in attempts:
            response = self.client.open(url, method=method, data={"csrf_token": "test-csrf"})
            self.assertEqual(response.status_code, 403, f"{method} {url} let a viewer through")


if __name__ == "__main__":
    unittest.main()
