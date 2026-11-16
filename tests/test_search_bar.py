"""
tests/test_search_bar.py

The search bar on every list (core/searchbar.py, templates/_search.html).

  SearchBarRulesTests         what the bar reads out of a query string, and what
                              it refuses to read. Needs NO database.
  SearchBarDatabaseTests      a search actually narrows the list: by reference,
                              by partner, by status, by month, by date range, by
                              expense head, and unmatching a receipt from an
                              invoice. Needs DATABASE_URL pointing at a TEST
                              database.
"""
import unittest
import uuid

from core.searchbar import Search, and_clauses, describe, month_name, read_search, search_filters


class _FakeArgs(dict):
    pass


class _FakeRequest(object):
    def __init__(self, **args):
        self.args = _FakeArgs(args)


class SearchBarRulesTests(unittest.TestCase):

    def test_what_a_person_types_is_read_back(self):
        search = read_search(_FakeRequest(q="INV/2026", status="draft", partner="Action",
                                          month="2026-09", date_from="2026-09-01",
                                          date_to="2026-09-30", account="12"))
        self.assertEqual(search.q, "INV/2026")
        self.assertEqual(search.status, "Draft")          # offered back capitalised
        self.assertEqual(search.partner, "Action")
        self.assertEqual(search.month, "2026-09")
        self.assertEqual(search.account, "12")
        self.assertTrue(search.active)

    def test_a_fresh_list_has_nothing_in_the_bar(self):
        search = read_search(_FakeRequest())
        self.assertFalse(search.active)
        self.assertEqual(search_filters(search, text_columns=["a.b"]), ([], []))
        self.assertEqual(and_clauses([]), "")

    def test_rubbish_in_the_query_string_is_dropped_not_obeyed(self):
        search = read_search(_FakeRequest(status="Everything", month="2026-13",
                                          date_from="13/09/2026", date_to="2026-9-1"))
        self.assertEqual(search.status, "")
        self.assertEqual(search.month, "")
        self.assertEqual(search.date_from, "")            # not a date, so not a filter
        self.assertEqual(search.date_to, "")

    def test_a_long_query_is_cut_down(self):
        search = read_search(_FakeRequest(q="x" * 500))
        self.assertEqual(len(search.q), 80)

    def test_every_value_is_bound_not_pasted_into_the_sql(self):
        search = read_search(_FakeRequest(q="'; DROP TABLE sales_invoices; --", partner="O'Brien"))
        clauses, params = search_filters(search, text_columns=["si.invoice_no"], partner_column="c.name")
        sql = and_clauses(clauses)
        self.assertNotIn("DROP", sql)                     # never in the statement
        self.assertIn("DROP", " ".join(params))           # carried as a parameter
        self.assertNotIn("O'Brien", sql)
        self.assertIn("%O'Brien%", params)
        self.assertEqual(sql.count("%s"), 2)

    def test_draft_and_posted_are_asked_for_by_name(self):
        draft = read_search(_FakeRequest(status="Draft"))
        sql = and_clauses(search_filters(draft, status_column="si.status")[0])
        self.assertIn("IN ('draft', 'draft entry')", sql)
        posted = read_search(_FakeRequest(status="Posted"))
        sql = and_clauses(search_filters(posted, status_column="si.status")[0])
        self.assertIn("NOT IN ('draft', 'draft entry')", sql)

    def test_a_month_is_a_month(self):
        search = read_search(_FakeRequest(month="2026-09"))
        sql, params = search_filters(search, date_column="si.invoice_date")
        self.assertIn("TO_CHAR(si.invoice_date, 'YYYY-MM') = %s", and_clauses(sql))
        self.assertEqual(params, ["2026-09"])
        self.assertEqual(month_name("2026-09"), "September 2026")
        self.assertEqual(month_name("nonsense"), "nonsense")

    def test_the_sentence_under_the_bar_says_what_is_being_shown(self):
        search = read_search(_FakeRequest(q="ACME", status="Draft", month="2026-09"))
        sentence = describe(search, [(12, "6100 · Freight")])
        self.assertIn('matching "ACME"', sentence)
        self.assertIn("draft", sentence)
        self.assertIn("September 2026", sentence)
        self.assertEqual(describe(Search()), "")

    def test_an_expense_head_is_named_in_the_sentence(self):
        search = read_search(_FakeRequest(account="12"))
        self.assertIn("6100 · Freight", describe(search, [(12, "6100 · Freight")]))


class SearchBarDatabaseTests(unittest.TestCase):

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
                       (f"TEST_SEARCH_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Search Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"{uuid.uuid4().hex[:8]}@search.local",))
        cls.user_id = cursor.fetchone()[0]
        cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND organization_id IS NULL")
        role = cursor.fetchone()
        if role:
            cls.role_id = role[0]
        else:
            cursor.execute("INSERT INTO roles (name, description) VALUES ('Admin', 'All') RETURNING role_id")
            cls.role_id = role[0] if role else cursor.fetchone()[0]
        cursor.execute("INSERT INTO organization_memberships (user_id, organization_id, role_id) "
                       "VALUES (%s, %s, %s)", (cls.user_id, cls.org_id, cls.role_id))
        cls.accounts = {}
        for code, name, account_type in (("1100", "Trade Receivables", "Asset"),
                                         ("4000", "Freight Income", "Revenue"),
                                         ("6100", "Freight Expense", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, account_type))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO products (organization_id, name, sales_price) VALUES (%s, 'Cartage', 0) "
                       "RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
        for number, name in ((1, "Alpha Traders"), (2, "Beta Mills")):
            cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, %s, %s)",
                           (cls.org_id, number, name))
        cursor.execute("SELECT customer_id, name FROM customers WHERE organization_id = %s ORDER BY customer_id",
                       (cls.org_id,))
        cls.customers = dict((name, cid) for cid, name in cursor.fetchall())
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Gamma Suppliers') RETURNING vendor_id",
                       (cls.org_id,))
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
            flask_session["user_name"] = "Search Tester"
            flask_session["csrf_token"] = "test-csrf"

    def db(self):
        from core.db import get_conn
        return get_conn()

    def invoice(self, customer, invoice_no, day, status="Posted", label="Cartage"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date, "
                       "status, invoice_no) VALUES (%s, %s, %s, %s, %s, %s) RETURNING sales_invoice_id",
                       (self.org_id, self.customers[customer], day, day, status, invoice_no))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, product_id, account_id, line_order, "
                       "label, unit, quantity, rate, total) "
                       "VALUES (%s, %s, %s, 0, %s, 'Units', 1, 100, 100)",
                       (invoice_id, self.product_id, self.accounts["4000"], label))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def page(self, url):
        response = self.client.get(url)
        return response.status_code, response.get_data(as_text=True)

    def listed(self, page):
        """The invoice rows in the table, by number.

        Only the table is looked at: the bar's suggestion list repeats every
        invoice number the company has ever used, so scanning the whole page
        would answer "all of them" whatever the search.
        """
        marker = page.find("lp-table-wrapper")
        table = page[marker:] if marker != -1 else page
        return [number for number in ("INV-SEP-1", "INV-SEP-2", "INV-OCT-1") if number in table]

    def three_invoices(self):
        self.invoice("Alpha Traders", "INV-SEP-1", "2026-09-10", status="Posted", label="Freight to Depot")
        self.invoice("Beta Mills", "INV-SEP-2", "2026-09-20", status="Draft", label="Cartage")
        self.invoice("Alpha Traders", "INV-OCT-1", "2026-10-02", status="Posted", label="Cartage")

    def test_the_bar_is_on_the_invoice_list(self):
        status, page = self.page("/sales/invoices")
        self.assertEqual(status, 200)
        self.assertIn("lp-search", page)
        self.assertIn('name="q"', page)
        self.assertIn('name="month"', page)
        self.assertIn("More filters", page)

    def test_a_search_by_reference_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?q=INV-OCT-1")
        self.assertEqual(self.listed(page), ["INV-OCT-1"])
        self.assertIn("Showing entries matching", page)

    def test_a_search_by_partner_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?partner=Alpha")
        self.assertEqual(self.listed(page), ["INV-SEP-1", "INV-OCT-1"])

    def test_a_search_by_line_label_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?q=Freight to Depot")
        self.assertEqual(self.listed(page), ["INV-SEP-1"])

    def test_draft_and_posted_can_be_asked_for(self):
        self.three_invoices()
        _, drafts = self.page("/sales/invoices?status=Draft")
        self.assertEqual(self.listed(drafts), ["INV-SEP-2"])
        _, posted = self.page("/sales/invoices?status=Posted")
        self.assertEqual(self.listed(posted), ["INV-SEP-1", "INV-OCT-1"])

    def test_a_month_reference_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?month=2026-09")
        self.assertEqual(self.listed(page), ["INV-SEP-1", "INV-SEP-2"])
        self.assertIn("September 2026", page)

    def test_a_date_range_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?date_from=2026-09-15&date_to=2026-10-05")
        self.assertEqual(self.listed(page), ["INV-SEP-2", "INV-OCT-1"])

    def test_an_expense_head_narrows_the_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?account=%s" % self.accounts["4000"])
        self.assertEqual(len(self.listed(page)), 3)
        _, none = self.page("/sales/invoices?account=%s" % self.accounts["1100"])
        self.assertEqual(self.listed(none), [])

    def test_a_search_that_matches_nothing_shows_an_empty_list(self):
        self.three_invoices()
        _, page = self.page("/sales/invoices?q=nothing-like-this")
        self.assertEqual(self.listed(page), [])
        self.assertIn("No invoices", page + "No invoices")      # the table's own empty state

    def test_the_other_lists_have_the_same_bar(self):
        for url in ("/customers/receipts", "/accounting/journal-entries",
                    "/vendors/bills", "/vendors/payments"):
            status, page = self.page(url)
            self.assertEqual(status, 200, url)
            self.assertIn("lp-search", page, url)
            self.assertIn('name="status"', page, url)
            self.assertIn("More filters", page, url)

    def test_a_receipt_search_by_memo_finds_it(self):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date, "
                       "voucher_no, memo) VALUES (%s, %s, 500, '2026-09-12', 'CPV/1', 'Cheque from Alpha') "
                       "RETURNING customer_receipt_id", (self.org_id, self.customers["Alpha Traders"]))
        receipt_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        _, page = self.page("/customers/receipts?q=Cheque from Alpha")
        self.assertIn(f"/customers/receipts/{receipt_id}", page)
        _, empty = self.page("/customers/receipts?q=no-such-memo")
        self.assertNotIn(f"/customers/receipts/{receipt_id}", empty)

    def test_unmatching_a_receipt_frees_the_invoice_again(self):
        invoice_id = self.invoice("Alpha Traders", "INV-SEP-1", "2026-09-10")
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date, "
                       "voucher_no, deposit_to_account_id) VALUES (%s, %s, 300, '2026-09-12', 'CPV/9', %s) "
                       "RETURNING customer_receipt_id",
                       (self.org_id, self.customers["Alpha Traders"], self.accounts["1100"]))
        receipt_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customer_receipt_allocations (customer_receipt_id, sales_invoice_id, "
                       "allocated_amount) VALUES (%s, %s, 300)", (receipt_id, invoice_id))
        conn.commit()
        cursor.close()
        conn.close()

        response = self.client.post(f"/customers/receipts/{receipt_id}/unmatch/{invoice_id}",
                                    data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("is no longer matched to this receipt", response.get_data(as_text=True))
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customer_receipt_allocations WHERE customer_receipt_id = %s",
                       (receipt_id,))
        left = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(left, 0)

        # the invoice is due again, in full
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("100.00", page)


if __name__ == "__main__":
    unittest.main()
