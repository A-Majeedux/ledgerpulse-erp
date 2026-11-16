"""
tests/test_document_actions.py

The gear menu on a transaction (core/docmenu.py) and what its actions do:
Duplicating, Deleting, and the trail of related documents on the page.

Two kinds of check, kept apart by class:

  DocumentMenuRulesTests   needs NO database. What the menu offers for a draft,
                           for a posted document, for a receipt that is already
                           matched, and to a person without the permission.

  DocumentActionsDatabaseTests  duplicates and deletes real documents in a
                           throwaway organization and reads the pages, to prove
                           the rules hold where it matters. Needs DATABASE_URL
                           pointed at a TEST database:

    python -m pytest tests/test_document_actions.py -q
"""
import unittest
import uuid
from decimal import Decimal as D

from core.docmenu import ACTIONS, build_menu


class DocumentMenuRulesTests(unittest.TestCase):

    def menu(self, kind, status=None, extra=None, permission=None):
        return build_menu(kind, 7, status=status, user_id=1, organization_id=2,
                          extra=extra or {})

    def test_a_draft_invoice_can_be_copied_and_deleted(self):
        import core.docmenu as menu_module
        # no permissions table here: every action is offered
        original = menu_module._may
        menu_module._may = lambda *args: True
        try:
            items = build_menu("sales_invoice", 7, status="Draft", user_id=1, organization_id=2)
        finally:
            menu_module._may = original
        labels = [item["label"] for item in items]
        # printing comes first, then the two that change the document
        self.assertEqual(labels, ["Print / PDF invoice", "Duplicate as a new draft", "Delete this draft"])
        self.assertTrue(all(item["allowed"] for item in items))
        by_key = dict((item["key"], item) for item in items)
        self.assertEqual(by_key["duplicate"]["url"], "/sales/invoices/7/duplicate")
        self.assertTrue(by_key["duplicate"]["post"])            # a form button, not a link
        self.assertEqual(by_key["delete"]["tone"], "danger")
        self.assertTrue(by_key["delete"]["confirm"])            # asked before it happens

    def test_a_posted_invoice_cannot_be_deleted_and_says_why(self):
        import core.docmenu as menu_module
        original = menu_module._may
        menu_module._may = lambda *args: True
        try:
            items = build_menu("sales_invoice", 7, status="Posted", user_id=1, organization_id=2)
        finally:
            menu_module._may = original
        delete = [item for item in items if item["key"] == "delete"][0]
        self.assertFalse(delete["allowed"])
        self.assertIn("posted invoice cannot be deleted", delete["note"])
        duplicate = [item for item in items if item["key"] == "duplicate"][0]
        self.assertTrue(duplicate["allowed"])                   # a posted one may still be copied

    def test_a_matched_receipt_cannot_be_deleted(self):
        import core.docmenu as menu_module
        original = menu_module._may
        menu_module._may = lambda *args: True
        try:
            items = build_menu("customer_receipt", 3, status="Posted", user_id=1, organization_id=2,
                               extra={"has_allocations": True})
        finally:
            menu_module._may = original
        delete = [item for item in items if item["key"] == "delete"][0]
        self.assertFalse(delete["allowed"])
        self.assertIn("unmatch it before deleting", delete["note"])

    def test_a_person_without_the_permission_is_offered_nothing_that_changes_anything(self):
        # has_permission() answers False in a database-less run. Printing changes
        # nothing, so it is still offered; duplicate and delete are not.
        items = build_menu("sales_invoice", 7, status="Draft", user_id=None, organization_id=None)
        self.assertEqual([item["key"] for item in items], ["print"])
        self.assertEqual(build_menu("unknown_kind", 7, status="Draft", user_id=1, organization_id=2), [])

    def test_a_receipt_is_copied_through_the_form_not_behind_the_users_back(self):
        item = [action for action in ACTIONS["customer_receipt"] if action["key"] == "duplicate"][0]
        self.assertEqual(item["url"], "/customers/receipts/new?from={id}")
        self.assertFalse(item["post"])                          # opens the form, saves nothing

    def test_every_document_offers_its_printed_copy_from_the_menu(self):
        for kind, suffix in (("sales_invoice", "print"), ("customer_receipt", "print"),
                             ("vendor_bill", "print"), ("vendor_payment", "print")):
            first = ACTIONS[kind][0]
            self.assertEqual(first["key"], "print", kind)
            self.assertTrue(first["url"].endswith("/print"), kind)
            self.assertFalse(first["post"], kind)                # a link, not a form
            self.assertNotIn("permission", first, kind)          # printing asks for nothing


class DocumentActionsDatabaseTests(unittest.TestCase):

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
                       (f"TEST_DOC_ACTIONS_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Actions Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"{uuid.uuid4().hex[:8]}@doc-actions.local",))
        cls.user_id = cursor.fetchone()[0]
        # an Admin role with every permission, so the menu is fully offered
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
        for code, name, account_type in (("1100", "Trade Receivables", "Asset"),
                                         ("4000", "Sales Revenue", "Revenue"),
                                         ("2200", "Output Tax Payable", "Liability"),
                                         ("1000", "Cash", "Asset"),
                                         ("2100", "Trade Payables", "Liability"),
                                         ("5000", "Purchases", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, account_type))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO products (organization_id, name, sales_price) VALUES (%s, 'Freight', 0) "
                       "RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name, payment_terms_days) "
                       "VALUES (%s, 1, 'Action Traders', 30) RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Action Suppliers') "
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
        cursor.execute("DELETE FROM customer_receipt_allocations WHERE customer_receipt_id IN "
                       "(SELECT customer_receipt_id FROM customer_receipts WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN "
                       "(SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (cls.org_id,))
        for table in ("vendor_bill_lines", "sales_invoice_lines"):
            column = "vendor_bill_id" if table == "vendor_bill_lines" else "sales_invoice_id"
            parent = "vendor_bills" if table == "vendor_bill_lines" else "sales_invoices"
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
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
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
            flask_session["user_name"] = "Actions Tester"
            flask_session["csrf_token"] = "test-csrf"

    # -- helpers ---------------------------------------------------------
    def db(self):
        from core.db import get_conn
        return get_conn()

    def invoice(self, status="Posted", total="1000.00", day="2026-09-01"):
        from sales.services import give_invoice_a_number
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING sales_invoice_id
        """, (self.org_id, self.customer_id, day, day, status))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, product_id, account_id, line_order, "
                       "label, unit, quantity, rate, total) "
                       "VALUES (%s, %s, %s, 0, 'Line', 'Units', 1, %s, %s)",
                       (invoice_id, self.product_id, self.accounts["4000"], total, total))
        give_invoice_a_number(cursor, self.org_id, invoice_id, day)
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type, source_id)
            VALUES (%s, %s, %s, 'Customer Invoices', 'Draft', 'SalesInvoice', %s) RETURNING journal_header_id
        """, (self.org_id, "Invoice " + str(invoice_id), day, invoice_id))
        header = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, debit, credit, line_order) "
                       "VALUES (%s, %s, 'Action Traders', 'Customer Invoice', %s, 0, 0)",
                       (header, self.accounts["1100"], total))
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, partner_name, label, debit, credit, line_order) "
                       "VALUES (%s, %s, 'Action Traders', 'Sales', 0, %s, 1)",
                       (header, self.accounts["4000"], total))
        cursor.execute("UPDATE sales_invoices SET journal_header_id = %s WHERE sales_invoice_id = %s",
                       (header, invoice_id))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def receipt(self, amount="400.00", allocations=()):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date, voucher_no, "
                       "deposit_to_account_id) "
                       "VALUES (%s, %s, %s, '2026-09-05', 'CPV/TEST/1', %s) RETURNING customer_receipt_id",
                       (self.org_id, self.customer_id, amount, self.accounts["1000"]))
        receipt_id = cursor.fetchone()[0]
        for invoice_id, applied in allocations:
            cursor.execute("INSERT INTO customer_receipt_allocations (customer_receipt_id, sales_invoice_id, "
                           "allocated_amount) VALUES (%s, %s, %s)", (receipt_id, invoice_id, applied))
        conn.commit()
        cursor.close()
        conn.close()
        return receipt_id

    def invoice_count(self):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count

    # -- the menu on the page --------------------------------------------
    def test_the_menu_is_on_the_invoice_page(self):
        invoice_id = self.invoice(status="Draft")
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("lp-docmenu", page)
        self.assertIn(f"/sales/invoices/{invoice_id}/duplicate", page)
        self.assertIn(f"/sales/invoices/{invoice_id}/delete", page)

    def test_the_page_shows_where_the_invoice_came_from(self):
        invoice_id = self.invoice()
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("lp-trail", page)
        self.assertIn(f"/customers/{self.customer_id}", page)                 # the customer
        self.assertIn("Statement", page)
        self.assertIn("/accounting/journal-entries/", page)                   # its entry, one click

    # -- duplicating ------------------------------------------------------
    def test_duplicating_an_invoice_makes_a_new_draft(self):
        invoice_id = self.invoice(status="Posted", total="1000.00")
        response = self.client.post(f"/sales/invoices/{invoice_id}/duplicate", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT sales_invoice_id, status, invoice_no FROM sales_invoices "
                       "WHERE organization_id = %s ORDER BY sales_invoice_id", (self.org_id,))
        rows = cursor.fetchall()
        cursor.execute("SELECT COALESCE(SUM(total), 0) FROM sales_invoice_lines WHERE sales_invoice_id = %s",
                       (rows[-1][0],))
        new_total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM journal_headers WHERE organization_id = %s AND source_type = 'SalesInvoice'",
                       (self.org_id,))
        headers = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "Posted")                              # the original is untouched
        self.assertEqual(rows[1][1], "Draft")                               # the copy is a draft
        self.assertNotEqual(rows[0][2], rows[1][2])                         # its own number
        self.assertEqual(new_total, D("1000.00"))                           # the same money
        self.assertEqual(headers, 2)                                        # its own draft entry
        self.assertEqual(self.client.get(f"/sales/invoices/{rows[1][0]}").status_code, 200)

    def test_deleting_a_draft_invoice_removes_its_lines_and_entry(self):
        invoice_id = self.invoice(status="Draft")
        response = self.client.post(f"/sales/invoices/{invoice_id}/delete", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        invoices = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM sales_invoice_lines WHERE sales_invoice_id = %s", (invoice_id,))
        lines = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        headers = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual((invoices, lines, headers), (0, 0, 0))

    def test_a_posted_invoice_is_not_deleted_and_the_page_says_so(self):
        invoice_id = self.invoice(status="Posted")
        response = self.client.post(f"/sales/invoices/{invoice_id}/delete", data={"csrf_token": "test-csrf"},
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("is posted, so it cannot be deleted", page)
        self.assertEqual(self.invoice_count(), 1)                           # still there

    # -- the receipt, and the money it brings -----------------------------
    def test_a_receipt_can_be_copied_into_a_fresh_form(self):
        receipt_id = self.receipt(amount="400.00")
        page = self.client.get(f"/customers/receipts/new?from={receipt_id}").get_data(as_text=True)
        self.assertIn('value="400.00"', page)                               # the amount is carried over
        self.assertIn("selected", page)                                     # the customer too

    def test_an_unmatched_receipt_can_be_deleted(self):
        receipt_id = self.receipt(amount="400.00")
        response = self.client.post(f"/customers/receipts/{receipt_id}/delete", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        receipts = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(receipts, 0)

    def test_a_matched_receipt_cannot_be_deleted(self):
        invoice_id = self.invoice()
        receipt_id = self.receipt(amount="400.00", allocations=[(invoice_id, "400.00")])
        response = self.client.post(f"/customers/receipts/{receipt_id}/delete", data={"csrf_token": "test-csrf"},
                                    follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("matched to an invoice, so it cannot be deleted", page)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        receipts = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(receipts, 1)

    def test_matching_a_receipt_to_an_invoice_from_the_invoice_page(self):
        invoice_id = self.invoice(total="1000.00")
        receipt_id = self.receipt(amount="400.00")
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("Match a receipt of", page)                           # the panel is offered
        self.assertIn(f'value="{receipt_id}"', page)                        # with the receipt in the list

        response = self.client.post(f"/sales/invoices/{invoice_id}/apply-receipt",
                                    data={"csrf_token": "test-csrf", "customer_receipt_id": receipt_id,
                                          "amount": "250.00"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("is now matched to this invoice", page)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(allocated_amount), 0) FROM customer_receipt_allocations "
                       "WHERE sales_invoice_id = %s", (invoice_id,))
        applied = cursor.fetchone()[0]
        cursor.execute("SELECT paid_amount FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        paid = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual((applied, paid), (D("250.00"), D("250.00")))

        # and the invoice's own page now shows the receipt as a chip
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("CPV/TEST/1", page)
        self.assertIn(f"/customers/receipts/{receipt_id}", page)

    def test_an_amount_bigger_than_the_invoice_is_refused(self):
        invoice_id = self.invoice(total="100.00")
        receipt_id = self.receipt(amount="400.00")
        response = self.client.post(f"/sales/invoices/{invoice_id}/apply-receipt",
                                    data={"csrf_token": "test-csrf", "customer_receipt_id": receipt_id,
                                          "amount": "400.00"}, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("still owes 100.00", page)            # the invoice's own limit is what refuses
        self.assertIn("cannot be applied to it", page)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customer_receipt_allocations WHERE sales_invoice_id = %s", (invoice_id,))
        rows = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(rows, 0)

    # -- the journal entry -------------------------------------------------
    def test_a_journal_entry_leads_back_to_the_document_that_wrote_it(self):
        invoice_id = self.invoice()
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        header = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        page = self.client.get(f"/accounting/journal-entries/{header}").get_data(as_text=True)
        self.assertIn("Sales invoice", page)
        self.assertIn(f"/sales/invoices/{invoice_id}", page)

    def test_a_draft_entry_can_be_duplicated_and_deleted(self):
        invoice_id = self.invoice(status="Draft")
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        header = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        response = self.client.post(f"/accounting/journal-entries/{header}/duplicate", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT journal_header_id, status, reference FROM journal_headers "
                       "WHERE organization_id = %s ORDER BY journal_header_id", (self.org_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "Draft")
        self.assertEqual(rows[1][2], rows[0][2])                            # the same wording

        # the copy can be deleted; the one that belongs to the invoice cannot
        copy_id = rows[1][0]
        self.assertEqual(self.client.post(f"/accounting/journal-entries/{copy_id}/delete",
                                          data={"csrf_token": "test-csrf"}).status_code, 302)
        response = self.client.post(f"/accounting/journal-entries/{header}/delete",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertIn("belongs to a SalesInvoice", response.get_data(as_text=True))

    # -- a vendor bill -----------------------------------------------------
    def test_a_bill_can_be_duplicated_without_its_vendor_number(self):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendor_bills (organization_id, vendor_id, bill_date, bill_number, status, journal_name)
            VALUES (%s, %s, '2026-09-01', 'VENDOR-777', 'Draft', 'Purchase Bills')
            RETURNING vendor_bill_id
        """, (self.org_id, self.vendor_id))
        bill_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, total, label, quantity, rate, "
                       "line_order, line_kind) VALUES (%s, %s, 500, 'Goods', 1, 500, 0, 'Goods')",
                       (bill_id, self.accounts["5000"]))
        conn.commit()
        cursor.close()
        conn.close()

        response = self.client.post(f"/vendors/bills/{bill_id}/duplicate", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_bill_id, bill_number, status FROM vendor_bills "
                       "WHERE organization_id = %s ORDER BY vendor_bill_id", (self.org_id,))
        rows = cursor.fetchall()
        cursor.execute("SELECT total FROM vendor_bill_lines WHERE vendor_bill_id = %s", (rows[-1][0],))
        total = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], "Draft")
        self.assertIsNone(rows[1][1])                                       # the vendor's number is not copied
        self.assertEqual(D(str(total)), D("500.00"))                        # the line is


if __name__ == "__main__":
    unittest.main()
