"""
tests/test_reset_to_draft.py

"Reset To Draft" - taking a posted voucher back so it can be corrected - and the
"Edit this draft" that goes with it.

The rule in one line: **only posted documents are reset, only drafts are edited,
and a document whose money has already moved is neither until that money is
unmatched.** Each of those is checked here by doing it.

Needs DATABASE_URL pointing at a TEST database.
"""
import unittest
import uuid
from decimal import Decimal as D


class ResetToDraftTests(unittest.TestCase):

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
                       (f"TEST_RESET_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Reset Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"{uuid.uuid4().hex[:8]}@reset.local",))
        cls.user_id = cursor.fetchone()[0]
        cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND organization_id IS NULL")
        role = cursor.fetchone()
        if role:
            cls.role_id = role[0]
        else:
            cursor.execute("INSERT INTO roles (name, description) VALUES ('Admin', 'All') RETURNING role_id")
            cls.role_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organization_memberships (user_id, organization_id, role_id) "
                       "VALUES (%s, %s, %s)", (cls.user_id, cls.org_id, cls.role_id))
        cls.accounts = {}
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("4000", "Freight Income", "Revenue"),
                                 ("2200", "Output Tax", "Liability"),
                                 ("2100", "Trade Payables", "Liability"),
                                 ("5000", "Purchases", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO products (organization_id, name, sales_price) VALUES (%s, 'Cartage', 0) "
                       "RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) "
                       "VALUES (%s, 1, 'Reset Traders') RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Reset Suppliers') "
                       "RETURNING vendor_id", (cls.org_id,))
        cls.vendor_id = cursor.fetchone()[0]
        # A reader: a member of the same company whose role holds no permission
        # to post anything. He must not be offered the reset, nor be able to post it.
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Reset Reader', %s, 'hash', TRUE) RETURNING user_id",
                       (f"{uuid.uuid4().hex[:8]}@reader.local",))
        cls.reader_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO roles (name, description) VALUES (%s, 'read only') RETURNING role_id",
                       (f"Reader {uuid.uuid4().hex[:6]}",))
        reader_role = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organization_memberships (user_id, organization_id, role_id) "
                       "VALUES (%s, %s, %s)", (cls.reader_id, cls.org_id, reader_role))
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_receipt_allocations WHERE customer_receipt_id IN "
                       "(SELECT customer_receipt_id FROM customer_receipts WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN "
                       "(SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (cls.org_id,))
        for table, column, parent in (("vendor_bill_lines", "vendor_bill_id", "vendor_bills"),
                                      ("sales_invoice_lines", "sales_invoice_id", "sales_invoices")):
            cursor.execute(f"DELETE FROM {table} WHERE {column} IN "
                           f"(SELECT {column} FROM {parent} WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM products WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organization_memberships WHERE user_id = %s", (cls.reader_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ANY(%s)", ([cls.user_id, cls.reader_id],))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_receipt_allocations WHERE customer_receipt_id IN "
                       "(SELECT customer_receipt_id FROM customer_receipts WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN "
                       "(SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (self.org_id,))
        for table, column, parent in (("vendor_bill_lines", "vendor_bill_id", "vendor_bills"),
                                      ("sales_invoice_lines", "sales_invoice_id", "sales_invoices")):
            cursor.execute(f"DELETE FROM {table} WHERE {column} IN "
                           f"(SELECT {column} FROM {parent} WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Reset Tester"
            flask_session["csrf_token"] = "test-csrf"

    # -- helpers ---------------------------------------------------------
    def db(self):
        from core.db import get_conn
        return get_conn()

    def make_invoice(self, status="Posted", total="1000.00", invoice_no="INV/2026/00001"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date,
                                        status, invoice_no)
            VALUES (%s, %s, '2026-09-01', '2026-10-01', %s, %s) RETURNING sales_invoice_id
        """, (self.org_id, self.customer_id, status, invoice_no))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines (sales_invoice_id, product_id, account_id, line_order,
                                             label, unit, quantity, rate, total)
            VALUES (%s, %s, %s, 0, 'Cartage', 'Units', 1, %s, %s)
        """, (invoice_id, self.product_id, self.accounts["4000"], total, total))
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type, source_id)
            VALUES (%s, %s, '2026-09-01', 'Customer Invoices', %s, 'SalesInvoice', %s)
            RETURNING journal_header_id
        """, (self.org_id, f"Invoice {invoice_no} - Reset Traders", status, invoice_id))
        header = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, "
                       "debit, credit, line_order) VALUES (%s, %s, 'Reset Traders', 'Customer Invoice', %s, 0, 0)",
                       (header, self.accounts["1100"], total))
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, "
                       "debit, credit, line_order) VALUES (%s, %s, 'Reset Traders', 'Sales', 0, %s, 1)",
                       (header, self.accounts["4000"], total))
        cursor.execute("UPDATE sales_invoices SET journal_header_id = %s WHERE sales_invoice_id = %s",
                       (header, invoice_id))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id, header

    def make_manual_entry(self, status="Posted"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type)
            VALUES (%s, 'Manual one', '2026-09-02', 'Miscellaneous Operations', %s, 'Manual')
            RETURNING journal_header_id
        """, (self.org_id, status))
        header = cursor.fetchone()[0]
        for account, debit, credit in ((self.accounts["1100"], "250.00", 0), (self.accounts["4000"], 0, "250.00")):
            cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, label, debit, credit, line_order) "
                           "VALUES (%s, %s, 'Manual', %s, %s, 0)", (header, account, debit, credit))
        conn.commit()
        cursor.close()
        conn.close()
        return header

    def make_bill(self, status="Posted", total="500.00", bill_number="BILL/2026/1"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendor_bills (organization_id, vendor_id, bill_number, bill_date, status,
                                      journal_name, amount_due)
            VALUES (%s, %s, %s, '2026-09-03', %s, 'Expenses', %s) RETURNING vendor_bill_id
        """, (self.org_id, self.vendor_id, bill_number, status, total))
        bill_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, total, label, quantity, rate, "
                       "line_order, line_kind) VALUES (%s, %s, %s, 'Goods', 1, %s, 0, 'Goods')",
                       (bill_id, self.accounts["5000"], total, total))
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type, source_id)
            VALUES (%s, %s, '2026-09-03', 'Expenses', %s, 'VendorBill', %s) RETURNING journal_header_id
        """, (self.org_id, f"Vendor Bill {bill_number}", status, bill_id))
        header = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, debit, "
                       "credit, line_order) VALUES (%s, %s, NULL, 'Goods', %s, 0, 0)",
                       (header, self.accounts["5000"], total))
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, debit, "
                       "credit, line_order) VALUES (%s, %s, 'Reset Suppliers', 'Payable', 0, %s, 1)",
                       (header, self.accounts["2100"], total))
        cursor.execute("UPDATE vendor_bills SET journal_header_id = %s WHERE vendor_bill_id = %s", (header, bill_id))
        conn.commit()
        cursor.close()
        conn.close()
        return bill_id, header

    def status_of_invoice(self, invoice_id):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status, invoice_no, journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s",
                       (invoice_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    def status_of_entry(self, header):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status, posted_at FROM journal_headers WHERE journal_header_id = %s", (header,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    def posted_balance(self, account_id):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
            FROM journal_lines jl JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
            WHERE jh.organization_id = %s AND jh.status = 'Posted' AND jl.account_id = %s
        """, (self.org_id, account_id))
        total = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return total

    # -- sales invoice ----------------------------------------------------
    def test_a_posted_invoice_goes_back_to_a_draft(self):
        invoice_id, header = self.make_invoice(status="Posted")
        before = self.posted_balance(self.accounts["1100"])
        self.assertEqual(before, D("1000.00"))

        response = self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("is a draft again", response.get_data(as_text=True))
        status, invoice_no, still_header = self.status_of_invoice(invoice_id)
        self.assertEqual(status, "Draft")
        self.assertEqual(invoice_no, "INV/2026/00001")          # the number never changes
        self.assertEqual(still_header, header)                  # it keeps its own entry
        entry_status, posted_at = self.status_of_entry(header)
        self.assertEqual(entry_status, "Draft")
        self.assertIsNone(posted_at)
        # the books stop carrying it
        self.assertEqual(self.posted_balance(self.accounts["1100"]), D("0"))

    def test_a_draft_invoice_cannot_be_reset(self):
        invoice_id, _ = self.make_invoice(status="Draft")
        response = self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("already a draft", response.get_data(as_text=True))

    def test_an_invoice_with_money_against_it_is_refused(self):
        invoice_id, header = self.make_invoice(status="Posted")
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date, "
                       "voucher_no, deposit_to_account_id) VALUES (%s, %s, 400, '2026-09-05', 'CPV/1', %s) "
                       "RETURNING customer_receipt_id",
                       (self.org_id, self.customer_id, self.accounts["1100"]))
        receipt_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customer_receipt_allocations (customer_receipt_id, sales_invoice_id, "
                       "allocated_amount) VALUES (%s, %s, 400)", (receipt_id, invoice_id))
        conn.commit()
        cursor.close()
        conn.close()

        response = self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("Unmatch that money first", page)
        self.assertEqual(self.status_of_invoice(invoice_id)[0], "Posted")
        self.assertEqual(self.status_of_entry(header)[0], "Posted")

    def test_a_draft_invoice_can_be_corrected(self):
        invoice_id, header = self.make_invoice(status="Draft", total="1000.00")
        response = self.client.post(f"/sales/invoices/{invoice_id}/edit", data={
            "csrf_token": "test-csrf",
            "customer_id": self.customer_id,
            "invoice_date": "2026-09-01",
            "due_date": "2026-10-01",
            "payment_terms": "30 days",
            "journal_name": "Customer Invoices",
            "product_id[]": [self.product_id],
            "label[]": ["Corrected cartage"],
            "account_id[]": [self.accounts["4000"]],
            "unit[]": ["Units"],
            "quantity[]": ["2"],
            "rate[]": ["750.00"],
            "discount_percent[]": ["0"],
            "sales_tax_rate_id[]": [""],
        }, follow_redirects=True)
        self.assertIn("is corrected", response.get_data(as_text=True))

        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(total), 0), COUNT(*) FROM sales_invoice_lines "
                       "WHERE sales_invoice_id = %s", (invoice_id,))
        line_total, line_count = cursor.fetchone()
        cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        new_header = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE journal_header_id = %s "
                       "AND account_id = %s", (new_header, self.accounts["1100"]))
        receivable = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(line_count, 1)
        self.assertEqual(D(str(line_total)), D("1500.00"))    # 2 x 750
        self.assertEqual(D(str(receivable)), D("1500.00"))    # the entry follows the lines
        self.assertEqual(new_header, header)                  # the same entry, corrected in place

    def test_a_posted_invoice_cannot_be_edited(self):
        invoice_id, _ = self.make_invoice(status="Posted")
        response = self.client.get(f"/sales/invoices/{invoice_id}/edit", follow_redirects=True)
        self.assertIn("Reset it to a draft before changing it", response.get_data(as_text=True))

    # -- journal entry ----------------------------------------------------
    def test_a_posted_manual_entry_goes_back_to_a_draft(self):
        header = self.make_manual_entry(status="Posted")
        response = self.client.post(f"/accounting/journal-entries/{header}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("is a draft again", response.get_data(as_text=True))
        self.assertEqual(self.status_of_entry(header)[0], "Draft")

    def test_an_entry_that_belongs_to_a_document_is_refused(self):
        invoice_id, header = self.make_invoice(status="Posted")
        response = self.client.post(f"/accounting/journal-entries/{header}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("Reset that document to a draft from its own page", page)
        self.assertEqual(self.status_of_entry(header)[0], "Posted")

    def test_a_draft_entry_can_be_corrected(self):
        header = self.make_manual_entry(status="Draft")
        response = self.client.post(f"/accounting/journal-entries/{header}/edit", data={
            "csrf_token": "test-csrf",
            "reference": "Corrected reference",
            "accounting_date": "2026-09-09",
            "journal_name": "Miscellaneous Operations",
            "account_id[]": [self.accounts["1100"], self.accounts["4000"]],
            "partner_name[]": ["", ""],
            "label[]": ["Corrected", "Corrected"],
            "debit[]": ["300.00", "0"],
            "credit[]": ["0", "300.00"],
        }, follow_redirects=True)
        self.assertIn("The entry is corrected", response.get_data(as_text=True))
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT reference FROM journal_headers WHERE journal_header_id = %s", (header,))
        self.assertEqual(cursor.fetchone()[0], "Corrected reference")
        cursor.execute("SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE journal_header_id = %s", (header,))
        self.assertEqual(D(str(cursor.fetchone()[0])), D("300.00"))
        cursor.close()
        conn.close()

    def test_an_unbalanced_correction_is_refused(self):
        header = self.make_manual_entry(status="Draft")
        response = self.client.post(f"/accounting/journal-entries/{header}/edit", data={
            "csrf_token": "test-csrf",
            "reference": "Still wrong",
            "accounting_date": "2026-09-09",
            "journal_name": "Miscellaneous Operations",
            "account_id[]": [self.accounts["1100"], self.accounts["4000"]],
            "partner_name[]": ["", ""],
            "label[]": ["a", "b"],
            "debit[]": ["300.00", "0"],
            "credit[]": ["0", "200.00"],
        }, follow_redirects=True)
        self.assertIn("does not balance", response.get_data(as_text=True))
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE journal_header_id = %s", (header,))
        self.assertEqual(D(str(cursor.fetchone()[0])), D("250.00"))   # untouched
        cursor.close()
        conn.close()

    # -- vendor bill ------------------------------------------------------
    def test_a_posted_bill_goes_back_to_a_draft(self):
        bill_id, header = self.make_bill(status="Posted")
        response = self.client.post(f"/vendors/bills/{bill_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("is a draft again", response.get_data(as_text=True))
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status, bill_number FROM vendor_bills WHERE vendor_bill_id = %s", (bill_id,))
        status, bill_number = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(status, "Draft")
        self.assertEqual(bill_number, "BILL/2026/1")
        self.assertEqual(self.status_of_entry(header)[0], "Draft")
        self.assertEqual(self.posted_balance(self.accounts["2100"]), D("0"))

    def test_a_bill_with_a_payment_against_it_is_refused(self):
        bill_id, header = self.make_bill(status="Posted")
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendor_payments (organization_id, vendor_id, voucher_number, amount, "
                       "payment_date, status, paid_from_account_id) VALUES (%s, %s, 'PV/1', 200, '2026-09-09', "
                       "'Posted', %s) RETURNING vendor_payment_id",
                       (self.org_id, self.vendor_id, self.accounts["1100"]))
        payment_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_payment_allocations (vendor_payment_id, vendor_bill_id, "
                       "allocated_amount) VALUES (%s, %s, 200)", (payment_id, bill_id))
        conn.commit()
        cursor.close()
        conn.close()
        response = self.client.post(f"/vendors/bills/{bill_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("Remove that payment match first", response.get_data(as_text=True))
        self.assertEqual(self.status_of_entry(header)[0], "Posted")

    def test_a_draft_bill_can_be_corrected(self):
        bill_id, _ = self.make_bill(status="Draft", total="500.00")
        response = self.client.post(f"/vendors/bills/{bill_id}/edit", data={
            "csrf_token": "test-csrf",
            "vendor_id": self.vendor_id,
            "bill_date": "2026-09-03",
            "due_date": "",
            "payment_terms": "",
            "lc_reference": "",
            "journal_name": "Expenses",
            "landed_cost_basis": "value",
            "label[]": ["Corrected goods"],
            "product_id[]": [""],
            "account_id[]": [self.accounts["5000"]],
            "quantity[]": ["1"],
            "rate[]": ["800.00"],
            "total[]": ["800.00"],
            "line_kind[]": ["Goods"],
        }, follow_redirects=True)
        self.assertIn("is corrected", response.get_data(as_text=True))
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(total), 0) FROM vendor_bill_lines WHERE vendor_bill_id = %s", (bill_id,))
        line_total = cursor.fetchone()[0]
        cursor.execute("SELECT amount_due FROM vendor_bills WHERE vendor_bill_id = %s", (bill_id,))
        due = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(D(str(line_total)), D("800.00"))
        self.assertEqual(D(str(due)), D("800.00"))

    # -- role authority ---------------------------------------------------
    def test_a_reader_is_not_offered_the_reset_and_cannot_force_it(self):
        invoice_id, header = self.make_invoice(status="Posted")
        with self.client.session_transaction() as flask_session:
            flask_session["user_id"] = self.reader_id
            flask_session["organization_id"] = self.org_id

        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertNotIn("/sales/invoices/%d/reset-to-draft" % invoice_id, page)

        response = self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft",
                                    data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status_of_entry(header)[0], "Posted")     # nothing changed

        # the edit page is behind the same wall
        response = self.client.get(f"/sales/invoices/{invoice_id}/edit")
        self.assertEqual(response.status_code, 403)

    # -- the menu tells the same story ------------------------------------
    def test_the_menu_offers_reset_only_on_a_posted_document(self):
        from core.docmenu import ACTIONS, build_menu
        import core.docmenu as menu_module
        original = menu_module._may
        menu_module._may = lambda *args: True
        try:
            posted = dict((item["key"], item) for item in
                          build_menu("sales_invoice", 5, status="Posted", user_id=1, organization_id=2))
            draft = dict((item["key"], item) for item in
                         build_menu("sales_invoice", 5, status="Draft", user_id=1, organization_id=2))
        finally:
            menu_module._may = original
        self.assertTrue(posted["reset"]["allowed"])
        self.assertFalse(posted["edit"]["allowed"])
        self.assertIn("resetting it to a draft", posted["edit"]["note"])
        self.assertTrue(draft["edit"]["allowed"])
        self.assertFalse(draft["reset"]["allowed"])
        self.assertEqual(posted["reset"]["url"], "/sales/invoices/5/reset-to-draft")
        self.assertEqual(draft["edit"]["url"], "/sales/invoices/5/edit")
        for kind in ("sales_invoice", "journal_entry", "vendor_bill"):
            keys = [action["key"] for action in ACTIONS[kind]]
            self.assertIn("reset", keys, kind)
            self.assertIn("edit", keys, kind)


if __name__ == "__main__":
    unittest.main()
