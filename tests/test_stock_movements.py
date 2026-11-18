"""
tests/test_stock_movements.py

On hand used to be a figure typed on the product form that nothing ever moved:
buying ten bags left the stock where it was and selling four left it there too.
Now every change is a row in `stock_movements`, `products.on_hand` is the sum of
those rows, and this file tries the whole path:

  * a draft bill moves nothing; posting it brings the goods in at their **landed**
    cost, LC charges and all;
  * posting an invoice takes them out at their **average** cost;
  * resetting either document to a draft takes its movement back off again;
  * stock typed on a product form becomes one **Opening** movement the first time
    anything else moves, so the history adds up from the beginning;
  * selling what we do not have is allowed - a shop sells before the bill arrives
    - but the person posting is told which product went short;
  * an asset line or an LC charge is not stock of the product it names.

Run: python -m pytest tests/test_stock_movements.py -q
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member


class _Stocked(unittest.TestCase):
    """A company with one product on the shelf, a vendor, a customer and a chart."""

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
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') "
                       "RETURNING organization_id", (f"TEST_STOCK_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Stock Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"stock_{uuid.uuid4().hex[:8]}@x.local",))
        cls.user_id = cursor.fetchone()[0]
        member(cursor, cls.user_id, cls.org_id)

        cls.accounts = {}
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("1200", "Stock on Hand", "Asset"),
                                 ("1250", "Input Sales Tax Recoverable", "Asset"),
                                 ("2100", "Trade Payables", "Liability"),
                                 ("4000", "Sales Revenue", "Revenue"),
                                 ("5000", "Cost of Goods Sold", "Expense"),
                                 ("5200", "Freight & Delivery", "Expense"),
                                 ("1530", "Vehicles", "Asset")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]

        cursor.execute("INSERT INTO products (organization_id, name, unit, sales_price, on_hand, is_active) "
                       "VALUES (%s, 'Urea 50kg', 'Bag', 3000, 0, TRUE) RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO products (organization_id, name, unit, sales_price, on_hand, is_active) "
                       "VALUES (%s, 'Typed Stock', 'Bag', 100, 100, TRUE) RETURNING product_id", (cls.org_id,))
        cls.typed_product_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Stock Suppliers') "
                       "RETURNING vendor_id", (cls.org_id,))
        cls.vendor_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, name) VALUES (%s, 'Stock Buyers') "
                       "RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        # stock_movements cascade with the products and the company
        for statement, params in (
            ("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
             "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (cls.org_id,)),
            ("DELETE FROM vendor_bills WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
             "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (cls.org_id,)),
            ("DELETE FROM sales_invoices WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM journal_lines WHERE journal_header_id IN "
             "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (cls.org_id,)),
            ("DELETE FROM journal_headers WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM products WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM vendors WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM customers WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM sales_tax_rates WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM chart_of_accounts WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM organization_memberships WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM organizations WHERE organization_id = %s", (cls.org_id,)),
            ("DELETE FROM users WHERE user_id = %s", (cls.user_id,)),
        ):
            cursor.execute(statement, params)
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for statement in (
            "DELETE FROM stock_movements WHERE organization_id = %s",
            "DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
            "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)",
            "DELETE FROM vendor_bills WHERE organization_id = %s",
            "DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
            "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)",
            "DELETE FROM sales_invoices WHERE organization_id = %s",
            "DELETE FROM journal_lines WHERE journal_header_id IN "
            "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
            "DELETE FROM journal_headers WHERE organization_id = %s",
            "UPDATE products SET on_hand = 0, last_landed_cost = NULL WHERE organization_id = %s",
        ):
            cursor.execute(statement, (self.org_id,))
        cursor.execute("UPDATE products SET on_hand = 100 WHERE product_id = %s", (self.typed_product_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Stock Tester"
            flask_session["csrf_token"] = "test-csrf"

    # -- the two documents ------------------------------------------------

    def db(self):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    def post_bill(self, lines, *, post=True, bill_date="2026-11-01"):
        """lines: list of (product_id, quantity, rate, kind, account_code)."""
        form = {"csrf_token": "test-csrf", "vendor_id": str(self.vendor_id),
                "bill_date": bill_date, "journal_name": "Expenses",
                "label[]": [], "product_id[]": [], "account_id[]": [], "quantity[]": [],
                "rate[]": [], "total[]": [], "line_kind[]": [], "unit[]": [],
                "discount_percent[]": [], "tax_rate_id[]": [], "asset_life[]": []}
        for product_id, quantity, rate, kind, account in lines:
            form["label[]"].append("Line")
            form["product_id[]"].append(str(product_id or ""))
            form["account_id[]"].append(str(self.accounts[account]))
            form["quantity[]"].append(str(quantity))
            form["rate[]"].append(str(rate))
            form["total[]"].append(str(D(str(quantity)) * D(str(rate))))
            form["line_kind[]"].append(kind)
            form["unit[]"].append("Bag")
            form["discount_percent[]"].append("0")
            form["tax_rate_id[]"].append("")
            form["asset_life[]"].append("")
        response = self.client.post("/vendors/bills/new", data=form)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conn, cursor = self.db()
        cursor.execute("SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s "
                       "ORDER BY vendor_bill_id DESC LIMIT 1", (self.org_id,))
        bill_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        if post:
            self.assertEqual(self.client.post(f"/vendors/bills/{bill_id}/post",
                                              data={"csrf_token": "test-csrf"}).status_code, 302)
        return bill_id

    def post_invoice(self, product_id, quantity, rate="3000", post=True):
        form = {
            "csrf_token": "test-csrf", "customer_id": str(self.customer_id),
            "invoice_date": "2026-11-02", "due_date": "2026-12-02",
            "journal_name": "Customer Invoices",
            "product_id[]": [str(product_id)], "label[]": ["Sale"],
            "account_id[]": [str(self.accounts["4000"])], "unit[]": ["Bag"],
            "quantity[]": [str(quantity)], "rate[]": [str(rate)],
            "discount_percent[]": ["0"], "sales_tax_rate_id[]": [""],
        }
        response = self.client.post("/sales/invoices/new", data=form)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conn, cursor = self.db()
        cursor.execute("SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s "
                       "ORDER BY sales_invoice_id DESC LIMIT 1", (self.org_id,))
        invoice_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        if post:
            self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        return invoice_id

    # -- reading it back --------------------------------------------------

    def movements(self, product_id=None):
        conn, cursor = self.db()
        cursor.execute("""
            SELECT quantity, COALESCE(unit_cost, 0), COALESCE(value, 0), source_type, reference
            FROM stock_movements WHERE organization_id = %s AND product_id = %s
            ORDER BY (source_type <> 'Opening'), stock_movement_id
        """, (self.org_id, product_id or self.product_id))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    def on_hand(self, product_id=None):
        conn, cursor = self.db()
        cursor.execute("SELECT COALESCE(on_hand, 0) FROM products WHERE product_id = %s",
                       (product_id or self.product_id,))
        value = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return value

    # -- the tests --------------------------------------------------------

    def test_a_draft_bill_moves_no_stock(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")], post=False)
        self.assertEqual(self.movements(), [])
        self.assertEqual(self.on_hand(), 0)

    def test_posting_a_bill_brings_the_goods_in(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        rows = self.movements()
        self.assertEqual(len(rows), 1)
        quantity, unit_cost, value, source, reference = rows[0]
        self.assertEqual((quantity, unit_cost, value), (10, 2500, 25000))
        self.assertEqual(source, "VendorBill")
        self.assertIn("BILL", reference)
        self.assertEqual(self.on_hand(), 10)

    def test_the_goods_come_in_at_their_landed_cost(self):
        """10 bags at 2,500 plus 20,000 of freight are 4,500 a bag, not 2,500."""
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000"),
                        (None, 1, 20000, "Landed Cost", "5200")])
        rows = self.movements()
        self.assertEqual(len(rows), 1)                  # the freight is not stock of the product
        quantity, unit_cost, value, _, _ = rows[0]
        self.assertEqual(quantity, 10)
        self.assertEqual(unit_cost, 4500)
        self.assertEqual(value, 45000)
        conn, cursor = self.db()
        cursor.execute("SELECT last_landed_cost FROM products WHERE product_id = %s", (self.product_id,))
        self.assertEqual(cursor.fetchone()[0], 4500)
        cursor.close()
        conn.close()

    def test_recoverable_sales_tax_is_not_part_of_what_the_goods_cost(self):
        """The 18% on a purchase sits in 1250 and comes back, so it is no cost.

        Goods 10 x 2,500 and freight 20,000, both taxed: the landed cost is
        (25,000 + 20,000) / 10 = 4,500 a bag, not the 4,860 the tax would make it.
        """
        conn, cursor = self.db()
        cursor.execute("INSERT INTO sales_tax_rates (organization_id, label, rate_percent, is_active) "
                       "VALUES (%s, 'Purchase Tax 18%%', 18, TRUE) RETURNING sales_tax_rate_id", (self.org_id,))
        tax_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        form = {"csrf_token": "test-csrf", "vendor_id": str(self.vendor_id),
                "bill_date": "2026-11-01", "journal_name": "Expenses",
                "label[]": ["Goods", "Freight"], "product_id[]": [str(self.product_id), ""],
                "account_id[]": [str(self.accounts["5000"]), str(self.accounts["5200"])],
                "quantity[]": ["10", "1"], "rate[]": ["2500", "20000"],
                "total[]": ["29500", "23600"], "line_kind[]": ["Goods", "Landed Cost"],
                "unit[]": ["Bag", "Units"], "discount_percent[]": ["0", "0"],
                "tax_rate_id[]": [str(tax_id), str(tax_id)], "asset_life[]": ["", ""]}
        response = self.client.post("/vendors/bills/new", data=form)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        conn, cursor = self.db()
        cursor.execute("SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s "
                       "ORDER BY vendor_bill_id DESC LIMIT 1", (self.org_id,))
        bill_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.client.post(f"/vendors/bills/{bill_id}/post", data={"csrf_token": "test-csrf"})

        quantity, unit_cost, value, _, _ = self.movements()[0]
        self.assertEqual(unit_cost, 4500)              # not 4,860
        self.assertEqual(value, 45000)
        conn, cursor = self.db()
        cursor.execute("DELETE FROM sales_tax_rates WHERE sales_tax_rate_id = %s", (tax_id,))
        conn.commit()
        cursor.close()
        conn.close()

    def test_an_invoice_takes_the_goods_out_at_average_cost(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000"),
                        (None, 1, 20000, "Landed Cost", "5200")])
        invoice_id = self.post_invoice(self.product_id, 4)
        out = [row for row in self.movements() if row[3] == "SalesInvoice"]
        self.assertEqual(len(out), 1)
        quantity, unit_cost, value, _, reference = out[0]
        self.assertEqual(quantity, -4)
        self.assertEqual(unit_cost, 4500)               # the same cost it came in at
        self.assertEqual(value, -18000)
        self.assertEqual(self.on_hand(), 6)
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("What this invoice took out of stock", page)
        self.assertIn("-18,000.00", page)               # 4 x 4,500, written as it went out

    def test_resetting_the_invoice_puts_the_goods_back(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 4)
        self.assertEqual(self.on_hand(), 6)
        self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.on_hand(), 10)
        self.assertEqual([row for row in self.movements() if row[3] == "SalesInvoice"], [])

    def test_resetting_the_bill_takes_the_goods_out_again(self):
        bill_id = self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        self.assertEqual(self.on_hand(), 10)
        self.client.post(f"/vendors/bills/{bill_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.on_hand(), 0)
        self.assertEqual(self.movements(), [])

    def test_stock_typed_on_the_product_becomes_the_opening_movement(self):
        self.post_bill([(self.typed_product_id, 10, 40, "Goods", "5000")])
        rows = self.movements(self.typed_product_id)
        self.assertEqual([row[3] for row in rows], ["Opening", "VendorBill"])
        self.assertEqual(rows[0][0], 100)               # what the product form said
        self.assertEqual(rows[0][4], "Opening stock")
        self.assertEqual(self.on_hand(self.typed_product_id), 110)

    def test_goods_sold_out_of_typed_opening_stock_are_not_free(self):
        """A product whose 100 bags were typed in, at a landed cost of 40, sells at 40."""
        conn, cursor = self.db()
        cursor.execute("UPDATE products SET last_landed_cost = 40 WHERE product_id = %s",
                       (self.typed_product_id,))
        conn.commit()
        cursor.close()
        conn.close()
        self.post_invoice(self.typed_product_id, 10, rate="60")
        out = [row for row in self.movements(self.typed_product_id) if row[3] == "SalesInvoice"]
        self.assertEqual(len(out), 1)
        quantity, unit_cost, value, _, _ = out[0]
        self.assertEqual(quantity, -10)
        self.assertEqual(unit_cost, 40)              # not zero, and not the selling price
        self.assertEqual(value, -400)
        self.assertEqual(self.on_hand(self.typed_product_id), 90)

    # -- perpetual inventory: 1200 and the movements agree ----------------

    def balance_of(self, code):
        """What the posted books say is in an account (debit - credit)."""
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

    def stock_value(self):
        conn, cursor = self.db()
        cursor.execute("SELECT COALESCE(SUM(value), 0) FROM stock_movements WHERE organization_id = %s",
                       (self.org_id,))
        value = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return value

    def test_a_product_bought_on_a_bill_goes_into_stock_not_into_an_expense(self):
        bill_id = self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        self.assertEqual(self.balance_of("1200"), D("25000.00"))     # it is stock
        self.assertEqual(self.balance_of("5000"), D("0.00"))         # not an expense - yet

    def test_selling_turns_the_cost_of_the_goods_into_an_expense(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 4)
        self.assertEqual(self.balance_of("5000"), D("10000.00"))     # 4 x 2,500
        self.assertEqual(self.balance_of("1200"), D("15000.00"))     # the 6 left
        self.assertEqual(self.stock_value(), D("15000.00"))          # the shelf agrees

    def test_the_invoice_s_own_entry_carries_the_cost(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 4)
        conn, cursor = self.db()
        cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s",
                       (invoice_id,))
        header = cursor.fetchone()[0]
        cursor.execute("SELECT label FROM journal_lines WHERE journal_header_id = %s", (header,))
        labels = [row[0] or "" for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        self.assertTrue(any(label.startswith("Cost of goods sold") for label in labels))
        page = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        # the entry is balanced with the cost in it
        conn, cursor = self.db()
        cursor.execute("SELECT SUM(debit), SUM(credit) FROM journal_lines WHERE journal_header_id = %s",
                       (header,))
        debit, credit = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(debit, credit)

    def test_resetting_the_invoice_takes_the_cost_back_out(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 4)
        self.assertEqual(self.balance_of("5000"), D("10000.00"))
        self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.balance_of("5000"), D("0.00"))
        self.assertEqual(self.balance_of("1200"), D("25000.00"))
        self.assertEqual(self.stock_value(), D("25000.00"))

    def test_reposting_an_invoice_does_not_double_its_cost(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 4)
        self.client.post(f"/sales/invoices/{invoice_id}/reset-to-draft", data={"csrf_token": "test-csrf"})
        self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.balance_of("5000"), D("10000.00"))     # once, not twice
        self.assertEqual(self.on_hand(), 6)

    def test_the_stock_account_always_equals_the_stock_value(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000"),
                        (None, 1, 20000, "Landed Cost", "5200")])
        self.post_invoice(self.product_id, 3)
        self.post_bill([(self.product_id, 5, 2600, "Goods", "5000")])
        self.assertEqual(self.balance_of("1200"), self.stock_value())

    def test_the_stock_page_shows_the_movements_and_the_balance(self):
        self.post_bill([(self.typed_product_id, 10, 40, "Goods", "5000")])
        page = self.client.get(f"/sales/products/{self.typed_product_id}/stock").get_data(as_text=True)
        self.assertEqual(page.count("Opening stock"), 2)      # the pill and the reference
        self.assertIn("Movements", page)
        self.assertIn("110.00", page)                   # the balance after the purchase
        self.assertIn("Stock value", page)
        self.assertIn("Average cost a unit", page)
        self.assertIn("Purchase bill", page)            # a link back to the paper

    def test_selling_more_than_we_have_posts_but_says_so(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        invoice_id = self.post_invoice(self.product_id, 25)
        conn, cursor = self.db()
        cursor.execute("SELECT status FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        self.assertEqual(cursor.fetchone()[0], "Posted")     # it still goes through
        cursor.close()
        conn.close()
        self.assertEqual(self.on_hand(), -15)
        page = self.client.get(f"/sales/invoices/{invoice_id}", follow_redirects=True).get_data(as_text=True)
        self.assertIn("stock is now short", page)
        self.assertIn("Urea 50kg", page)

    def test_an_asset_line_moves_no_stock(self):
        self.post_bill([(self.product_id, 1, 2500000, "Asset", "1530")])
        self.assertEqual(self.movements(), [])
        self.assertEqual(self.on_hand(), 0)

    def test_a_line_with_no_product_moves_no_stock(self):
        self.post_bill([(None, 1, 40000, "Goods", "5000")])     # rent, not goods
        self.assertEqual(self.movements(), [])
        self.assertEqual(self.on_hand(), 0)

    def test_on_hand_is_always_the_sum_of_the_movements(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000"),
                        (None, 1, 20000, "Landed Cost", "5200")])
        self.post_invoice(self.product_id, 3)
        self.post_bill([(self.product_id, 5, 2600, "Goods", "5000")])
        conn, cursor = self.db()
        cursor.execute("SELECT COALESCE(on_hand, 0), "
                       "(SELECT COALESCE(SUM(quantity), 0) FROM stock_movements m "
                       " WHERE m.product_id = p.product_id) FROM products p WHERE product_id = %s",
                       (self.product_id,))
        on_hand, moved = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(on_hand, moved)
        self.assertEqual(on_hand, 12)                   # 10 in, 3 out, 5 in

    def test_the_stock_page_keeps_a_running_balance(self):
        self.post_bill([(self.product_id, 10, 2500, "Goods", "5000")])
        self.post_invoice(self.product_id, 4)
        page = self.client.get(f"/sales/products/{self.product_id}/stock").get_data(as_text=True)
        self.assertIn(">10.00<", page)                   # what came in
        self.assertIn(">4.00<", page)                    # what went out
        self.assertIn(">6.00<", page)                    # and what is left


if __name__ == "__main__":
    unittest.main()
