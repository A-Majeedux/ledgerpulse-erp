"""
tests/test_statement_menu_and_suggestions.py

The small corrections of this round, each checked where it can actually break:

  StatementSignatureTests   the statement goes out without the four signature
                            boxes, while the invoice and the bill keep theirs.
  DocumentChromeTests       the print action lives in the gear menu on the
                            receipt page, the invoice form's lines are a panel of
                            their own, and the receipt chosen on an invoice page
                            carries the money it has left with it.
  SuggestionTests           the search bar offers what has really been used, and
                            core.searchbar.suggest keeps that list short, unique
                            and free of blanks.

The first class needs no database; the second and third use a throwaway company.
"""
import re
import unittest
import uuid

from core.searchbar import suggest


class FakeCursor(object):
    """Just enough of a cursor to feed suggest() a few answers."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.seen = []

    def execute(self, sql, params=()):
        self.seen.append(sql)

    def fetchall(self):
        return self.answers.pop(0)


class SuggestionTests(unittest.TestCase):

    def test_blanks_are_dropped_and_repeats_are_gone(self):
        cursor = FakeCursor([[(None,), ("",), ("  ",)]])
        self.assertEqual(suggest(cursor, [("ignored", ())]), [])

    def test_one_customer_is_offered_once_whatever_the_case(self):
        cursor = FakeCursor([[("Chaudhry Fertilizers",), ("CHaudhry Fertilizers",), ("Sindh Agro",)]])
        self.assertEqual(suggest(cursor, [("ignored", ())]), ["Chaudhry Fertilizers", "Sindh Agro"])

    def test_the_list_is_cut_at_the_limit(self):
        cursor = FakeCursor([[("A",), ("B",), ("C",)]])
        self.assertEqual(suggest(cursor, [("ignored", ())], limit=2), ["A", "B"])

    def test_values_from_every_question_come_together(self):
        cursor = FakeCursor([[("INV/2026/00001",)], [("Phosphate",)]])
        self.assertEqual(suggest(cursor, [("first", ()), ("second", ())]),
                         ["INV/2026/00001", "Phosphate"])


class _Pages(unittest.TestCase):
    """Shared setup: a throwaway company with one invoice and one receipt."""

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
                       (f"TEST_CHROME_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Chrome Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"{uuid.uuid4().hex[:8]}@chrome.local",))
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
                                 ("4000", "Freight Income", "Revenue")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO products (organization_id, name, sales_price) VALUES (%s, 'Cartage', 0) "
                       "RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) "
                       "VALUES (%s, 1, 'Chaudhry Fertilizers') RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Gamma Suppliers') "
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
            flask_session["user_name"] = "Chrome Tester"
            flask_session["csrf_token"] = "test-csrf"

    def db(self):
        from core.db import get_conn
        return get_conn()

    def invoice(self, total="1254.00"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date, "
                       "status, invoice_no) VALUES (%s, %s, '2026-11-14', '2026-11-18', 'Posted', "
                       "'INV/2026/00001') RETURNING sales_invoice_id", (self.org_id, self.customer_id))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, product_id, account_id, line_order, "
                       "label, unit, quantity, rate, total) VALUES (%s, %s, %s, 0, 'Phosphate', 'Bags', 1, %s, %s)",
                       (invoice_id, self.product_id, self.accounts["4000"], total, total))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def receipt(self, amount="900.00", voucher="CPV/2026/00001"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date, "
                       "voucher_no, deposit_to_account_id) VALUES (%s, %s, %s, '2026-11-15', %s, %s) "
                       "RETURNING customer_receipt_id",
                       (self.org_id, self.customer_id, amount, voucher, self.accounts["1100"]))
        receipt_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return receipt_id

    def page(self, url):
        response = self.client.get(url)
        return response.status_code, response.get_data(as_text=True)


class StatementSignatureTests(_Pages):

    def test_the_statement_goes_out_without_signature_boxes(self):
        self.invoice()
        status, page = self.page(f"/customers/{self.customer_id}/statement")
        self.assertEqual(status, 200)
        self.assertIn("Statement of Account", page)
        for box in ("Prepared By", "Checked By", "Approved By", "Authorized By"):
            self.assertNotIn(box, page)

    def test_the_invoice_and_the_bill_keep_their_signature_boxes(self):
        invoice_id = self.invoice()
        status, page = self.page(f"/sales/invoices/{invoice_id}/print")
        self.assertEqual(status, 200)
        self.assertIn("Prepared By", page)

        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendor_bills (organization_id, vendor_id, bill_date, bill_number, status, "
                       "journal_name) VALUES (%s, %s, '2026-11-14', 'V-1', 'Draft', 'Purchase Bills') "
                       "RETURNING vendor_bill_id", (self.org_id, self.vendor_id))
        bill_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, total, label, quantity, rate, "
                       "line_order, line_kind) VALUES (%s, %s, 100, 'Goods', 1, 100, 0, 'Goods')",
                       (bill_id, self.accounts["4000"]))
        conn.commit()
        cursor.close()
        conn.close()
        status, page = self.page(f"/vendors/bills/{bill_id}/print")
        self.assertEqual(status, 200)
        self.assertIn("Prepared By", page)


class DocumentChromeTests(_Pages):

    def test_the_receipt_prints_from_its_menu(self):
        receipt_id = self.receipt()
        status, page = self.page(f"/customers/receipts/{receipt_id}")
        self.assertEqual(status, 200)
        self.assertIn("Print / PDF voucher", page)                    # inside the gear menu
        self.assertIn(f"/customers/receipts/{receipt_id}/print", page)
        # and there is no second print button in the header beside it
        self.assertNotIn('class="lp-btn primary" target="_blank"', page)
        self.assertIn("lp-page-actions", page)

    def test_the_invoice_lines_are_a_panel_of_their_own(self):
        status, page = self.page("/sales/invoices/new")
        self.assertEqual(status, 200)
        self.assertIn("lines-panel", page)
        self.assertIn("Invoice lines", page)
        self.assertIn('id="lines-count"', page)
        # the Add line button belongs to the panel now
        panel = page[page.find("lines-panel"):page.find("invoice-summary")]
        self.assertIn("Add line", panel)
        # the total is still there, under the lines
        self.assertIn("Grand total", page)

    def test_the_receipt_offered_on_an_invoice_carries_its_unapplied_money(self):
        invoice_id = self.invoice(total="1254.00")
        self.receipt(amount="900.00")
        status, page = self.page(f"/sales/invoices/{invoice_id}")
        self.assertEqual(status, 200)
        self.assertIn('data-left="900.00"', page)                      # the receipt's money
        self.assertIn('data-still-due="1254.00"', page)                # the invoice's debt
        self.assertIn("match-receipt-amount", page)                    # the box it fills
        self.assertIn("parseFloat(option.dataset.left)", page)         # the script that fills it

    def test_the_receipt_form_shares_the_amount_out_by_itself(self):
        status, page = self.page("/customers/receipts/new")
        self.assertEqual(status, 200)
        self.assertIn("autoAllocate()", page)                          # asked for on the amount box
        self.assertIn("function autoAllocate", page)
        self.assertIn("data-remaining", page)                          # what each invoice still owes
        self.assertIn("touchedRows", page)                             # a row he types in stays his


class SearchSuggestionTests(_Pages):

    def test_the_invoice_bar_offers_numbers_and_customers_from_history(self):
        self.invoice()
        status, page = self.page("/sales/invoices")
        self.assertEqual(status, 200)
        self.assertIn('list="lp-search-q-suggestions"', page)
        self.assertIn('list="lp-search-partner-suggestions"', page)
        block = page[page.find("lp-search-q-suggestions"):]
        block = block[:block.find("</datalist>")]
        self.assertIn("INV/2026/00001", block)                         # a real number
        self.assertIn("Chaudhry Fertilizers", block)                   # a real customer
        partner = page[page.find("lp-search-partner-suggestions"):]
        self.assertIn("Chaudhry Fertilizers", partner[:partner.find("</datalist>")])

    def test_a_receipt_bar_offers_the_vouchers_it_has(self):
        self.receipt(voucher="CPV/2026/00001")
        _, page = self.page("/customers/receipts")
        block = page[page.find("lp-search-q-suggestions"):]
        self.assertIn("CPV/2026/00001", block[:block.find("</datalist>")])

    def test_every_list_offers_something_once_it_has_history(self):
        self.invoice()
        self.receipt()
        # the two lists with rows behind them must suggest
        for url in ("/sales/invoices", "/customers/receipts"):
            _, page = self.page(url)
            self.assertIn("lp-search-q-suggestions", page, url)
            block = page[page.find("lp-search-q-suggestions"):]
            block = block[:block.find("</datalist>")]
            self.assertTrue(re.search(r"<option value=", block), url)   # never an empty list

        # every list gets the bar; a suggestion list is only drawn when there is
        # something in it - an empty one would say nothing
        for url in ("/accounting/journal-entries", "/vendors/bills", "/vendors/payments"):
            status, page = self.page(url)
            self.assertEqual(status, 200, url)
            self.assertIn("lp-search", page, url)
            if "lp-search-q-suggestions" in page:
                block = page[page.find("lp-search-q-suggestions"):]
                self.assertTrue(re.search(r"<option value=", block[:block.find("</datalist>")]), url)


    def test_a_suggestion_is_only_a_suggestion(self):
        # what he types still decides the answer: a number not in the books finds nothing
        self.invoice()
        _, page = self.page("/sales/invoices?q=INV/2026/99999")
        table = page[page.find("lp-table-wrapper"):]
        self.assertNotIn("INV/2026/00001", table)


if __name__ == "__main__":
    unittest.main()
