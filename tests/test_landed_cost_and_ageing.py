"""
tests/test_landed_cost_and_ageing.py

Checks for the ninth round - the three things built together:

  * LC + landed cost on purchases: a vendor bill can carry its LC charges
    (freight, duty, clearing) as their own lines, and those charges are spread
    over the goods lines - by value or by quantity - so each product gets a
    landed unit cost. The arithmetic must be exact: the shares must add up to
    the charges with no paisa lost, whatever the numbers.
  * Matching receipts to invoices: a customer receipt can be applied to the
    invoices it settles, the way a vendor payment applies to vendor bills, and
    each invoice then has a real "still due" figure.
  * Customer ageing: 0-30 / 31-60 / 61-90 / older, per customer, under Reports.

The first group needs no database. The rest, like the other database tests,
need DATABASE_URL pointing at a TEST database; each test makes its own company
and removes it again.

    python -m pytest tests/test_landed_cost_and_ageing.py -q
"""
import datetime
import unittest
import uuid
from decimal import Decimal

from _people import member

from vendors.landed_cost import (
    BASIS_LABELS, landed_unit_cost_by_product, normalise_basis, split_landed_cost,
)


class LandedCostArithmeticTests(unittest.TestCase):
    """The split itself - no database, no web page."""

    def goods(self):
        return [
            {"label": "Urea 50kg", "product_id": 1, "quantity": Decimal("100"), "total": Decimal("250000")},
            {"label": "DAP 50kg", "product_id": 2, "quantity": Decimal("60"), "total": Decimal("180000")},
            {"label": "Spray pump", "product_id": 3, "quantity": Decimal("10"), "total": Decimal("60000")},
        ]

    def test_the_shares_add_up_exactly_to_the_charges(self):
        # 33,333.33 over three lines cannot divide evenly - the last line takes
        # the remainder, so nothing is lost and nothing is invented.
        rows, summary = split_landed_cost(self.goods(), Decimal("33333.33"), "value")
        self.assertEqual(sum((row["allocated"] for row in rows), Decimal("0")), Decimal("33333.33"))
        self.assertEqual(summary["cost_total"], Decimal("33333.33"))
        self.assertEqual(summary["grand_total"], Decimal("490000") + Decimal("33333.33"))

    def test_by_value_gives_the_big_line_the_big_share(self):
        rows, _summary = split_landed_cost(self.goods(), Decimal("49000"), "value")
        # Goods: 250,000 / 180,000 / 60,000 of 490,000 - so 25,000 / 18,000 / 6,000.
        self.assertEqual([row["allocated"] for row in rows],
                         [Decimal("25000.00"), Decimal("18000.00"), Decimal("6000.00")])

    def test_by_quantity_ignores_the_value_of_the_goods(self):
        rows, summary = split_landed_cost(self.goods(), Decimal("17000"), "qty")
        # 170 units, 17,000 of charges: 100.00 a unit.
        self.assertEqual([row["allocated"] for row in rows],
                         [Decimal("10000.00"), Decimal("6000.00"), Decimal("1000.00")])
        self.assertEqual(summary["basis"], "qty")
        self.assertEqual(summary["units"], Decimal("170"))

    def test_a_cost_per_unit_is_given_for_every_line(self):
        rows, _summary = split_landed_cost(self.goods(), Decimal("17000"), "qty")
        # Urea: (250,000 + 10,000) / 100 = 2,600 a bag.
        self.assertEqual(rows[0]["unit_cost"], Decimal("2600.00"))
        self.assertEqual(rows[0]["landed_total"], Decimal("260000.00"))

    def test_odder_numbers_still_add_up(self):
        goods = [
            {"quantity": Decimal("7"), "total": Decimal("1234.56")},
            {"quantity": Decimal("3"), "total": Decimal("998.99")},
            {"quantity": Decimal("11"), "total": Decimal("0.01")},
        ]
        for basis in ("value", "qty"):
            for charges in ("0.01", "999.99", "12345.67"):
                rows, _summary = split_landed_cost(goods, Decimal(charges), basis)
                self.assertEqual(sum((row["allocated"] for row in rows), Decimal("0")),
                                 Decimal(charges), f"{basis} / {charges}")

    def test_no_charges_means_no_extra_cost(self):
        rows, summary = split_landed_cost(self.goods(), Decimal("0"), "value")
        self.assertEqual([row["allocated"] for row in rows], [Decimal("0.00")] * 3)
        self.assertEqual(summary["cost_total"], Decimal("0"))
        self.assertEqual(summary["grand_total"], Decimal("490000"))

    def test_a_line_with_no_quantity_does_not_divide_by_zero(self):
        rows, _summary = split_landed_cost(
            [{"quantity": Decimal("0"), "total": Decimal("500")}], Decimal("100"), "qty")
        self.assertEqual(rows[0]["unit_cost"], Decimal("0.00"))

    def test_the_two_bases_are_the_two_he_is_offered(self):
        self.assertEqual(normalise_basis("value"), "value")
        self.assertEqual(normalise_basis("qty"), "qty")
        self.assertEqual(normalise_basis(""), "value")        # an old bill with no answer
        self.assertEqual(normalise_basis(None), "value")
        self.assertEqual(normalise_basis("by tonnage"), "value")   # anything else: by value
        self.assertEqual(set(BASIS_LABELS), {"value", "qty"})

    def test_the_same_product_on_two_lines_is_one_cost(self):
        goods = [
            {"product_id": 1, "quantity": Decimal("10"), "total": Decimal("1000")},
            {"product_id": 1, "quantity": Decimal("10"), "total": Decimal("1000")},
            {"product_id": 2, "quantity": Decimal("5"), "total": Decimal("500")},
        ]
        costs = landed_unit_cost_by_product(goods, Decimal("500"), "qty")
        # 500 charges over 25 units = 20 a unit. Product 1 got 20 units and
        # 2,000 of goods, so its landed cost a unit is (2000 + 400) / 20 = 120.
        self.assertEqual(costs[1], Decimal("120.00"))
        self.assertEqual(costs[2], Decimal("120.00"))

    def test_the_remainder_goes_on_the_last_line_not_a_new_one(self):
        rows, _summary = split_landed_cost(self.goods(), Decimal("100"), "value")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["allocated"],
                         Decimal("100") - rows[0]["allocated"] - rows[1]["allocated"])


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
            ("TEST_ROUND9_" + uuid.uuid4().hex[:8],))
        self.org_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) "
            "VALUES ('Round Nine', %s, 'hash', TRUE) RETURNING user_id",
            (f"r9_{uuid.uuid4().hex[:8]}@x.local",))
        self.user_id = cursor.fetchone()[0]
        member(cursor, self.user_id, self.org_id)
        for code, name, kind in (
                ("1000", "Cash in Hand", "Asset"),
                ("1100", "Trade Receivables", "Asset"),
                ("2000", "Trade Payables", "Liability"),
                ("5000", "Office Expense", "Expense"),
                ("5100", "Goods Purchased", "Expense"),
                ("4000", "Sales Revenue", "Revenue")):
            cursor.execute("""
                INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (self.org_id, code, name, kind))
        cursor.execute("""
            INSERT INTO customers (organization_id, name, nature_of_supply)
            VALUES (%s, 'Gulf Agro Chemicals', 'Supplies') RETURNING customer_id
        """, (self.org_id,))
        self.customer_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO vendors (organization_id, name, nature_of_purchase)
            VALUES (%s, 'Fatima Fertilizer', 'Goods') RETURNING vendor_id
        """, (self.org_id,))
        self.vendor_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, sales_price, on_hand)
            VALUES (%s, 'Urea 50kg', 'Bags', 3000, 0) RETURNING product_id
        """, (self.org_id,))
        self.urea_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, sales_price, on_hand)
            VALUES (%s, 'DAP 50kg', 'Bags', 9000, 0) RETURNING product_id
        """, (self.org_id,))
        self.dap_id = cursor.fetchone()[0]
        cursor.execute("SELECT code, account_id FROM chart_of_accounts WHERE organization_id = %s",
                       (self.org_id,))
        self.accounts = dict(cursor.fetchall())
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Round Nine"
            flask_session["csrf_token"] = "test-csrf"

    def tearDown(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_receipt_allocations WHERE customer_receipt_id IN "
                       "(SELECT customer_receipt_id FROM customer_receipts WHERE organization_id = %s)",
                       (self.org_id,))
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
                       "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM products WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (self.org_id,))
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

    def account(self, code):
        return self.accounts[code]


class LandedCostOnABillTests(_OneCompanyPerTest):
    """A bill with LC charges: stored, spread, shown, and remembered."""

    def bill_form(self, **changes):
        """Two goods lines and one LC charge: 10 Urea + 5 DAP, 20,000 of freight."""
        form = {
            "vendor_id": self.vendor_id,
            "bill_date": "2026-11-01",
            "lc_reference": "LC 44/2026",
            "landed_cost_basis": "value",
            "journal_name": "Expenses",
            "csrf_token": "test-csrf",
            "label[]": ["Urea 50kg", "DAP 50kg", "Freight and clearing"],
            "account_id[]": [str(self.account("5100")), str(self.account("5100")), str(self.account("5000"))],
            "quantity[]": ["10", "5", "1"],
            "rate[]": ["2500", "8000", "20000"],
            "total[]": ["25000", "40000", "20000"],
            "product_id[]": [str(self.urea_id), str(self.dap_id), ""],
            "line_kind[]": ["Goods", "Goods", "Landed Cost"],
        }
        form.update(changes)
        return form

    def save_bill(self, **changes):
        return self.client.post("/vendors/bills/new", data=self.bill_form(**changes))

    def the_bill_id(self):
        conn, cursor = self.raw()
        cursor.execute("SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s "
                       "ORDER BY vendor_bill_id DESC", (self.org_id,))
        bill_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return bill_id

    def test_the_lines_remember_the_product_and_which_one_is_a_charge(self):
        self.assertEqual(self.save_bill().status_code, 302)
        conn, cursor = self.raw()
        cursor.execute("""
            SELECT COALESCE(line_kind, 'Goods'), product_id, total
            FROM vendor_bill_lines WHERE vendor_bill_id = %s ORDER BY line_order
        """, (self.the_bill_id(),))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        self.assertEqual([row[0] for row in rows], ["Goods", "Goods", "Landed Cost"])
        self.assertEqual(rows[0][1], self.urea_id)
        self.assertEqual(rows[1][1], self.dap_id)
        self.assertIsNone(rows[2][1])          # a freight charge names no product

    def test_the_basis_is_kept_with_the_bill(self):
        self.save_bill(landed_cost_basis="qty")
        conn, cursor = self.raw()
        cursor.execute("SELECT landed_cost_basis FROM vendor_bills WHERE organization_id = %s",
                       (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], "qty")
        cursor.close()
        conn.close()

    def test_the_bill_page_shows_what_each_unit_really_cost(self):
        self.save_bill()
        page = self.client.get(f"/vendors/bills/{self.the_bill_id()}").get_data(as_text=True)
        self.assertIn("What these goods really cost", page)
        self.assertIn("LC charges", page)
        self.assertIn("Cost per unit", page)
        self.assertIn("LC 44/2026", page)
        # 65,000 of goods + 20,000 of freight, by value:
        #   Urea 25,000/65,000 -> 7,692.31 of the charges, landed 32,692.31, /10 = 3,269.23 a bag
        self.assertIn("7,692.31", page)
        self.assertIn("3,269.23", page)
        # The bill itself is still 85,000 - the charges are on it once, not twice.
        self.assertIn("85,000.00", page)

    def test_posting_remembers_each_products_landed_cost(self):
        self.save_bill()
        bill_id = self.the_bill_id()
        self.assertEqual(self.client.post(f"/vendors/bills/{bill_id}/post",
                                          data={"csrf_token": "test-csrf"}).status_code, 302)
        conn, cursor = self.raw()
        cursor.execute("SELECT product_id, last_landed_cost FROM products WHERE organization_id = %s "
                       "ORDER BY product_id", (self.org_id,))
        landed = dict(cursor.fetchall())
        cursor.close()
        conn.close()
        self.assertEqual(landed[self.urea_id], Decimal("3269.23"))
        self.assertEqual(landed[self.dap_id], Decimal("10461.54"))   # (40,000 + 12,307.69) / 5

    def test_the_products_card_shows_the_landed_cost_after_posting(self):
        self.save_bill()
        self.client.post(f"/vendors/bills/{self.the_bill_id()}/post", data={"csrf_token": "test-csrf"})
        page = self.client.get("/sales/products").get_data(as_text=True)
        self.assertIn("Last landed cost", page)
        self.assertIn("3269.23", page)
        self.assertIn("10461.54", page)

    def test_a_bill_with_no_lc_lines_is_unaffected(self):
        response = self.save_bill(**{
            "label[]": ["Office rent"],
            "account_id[]": [str(self.account("5000"))],
            "quantity[]": ["1"],
            "rate[]": ["40000"],
            "total[]": ["40000"],
            "product_id[]": [""],
            "line_kind[]": ["Goods"],
            "lc_reference": "",
        })
        self.assertEqual(response.status_code, 302)
        bill_id = self.the_bill_id()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertNotIn("What these goods really cost", page)   # no charges, no panel
        self.assertIn("40000.00", page)
        self.client.post(f"/vendors/bills/{bill_id}/post", data={"csrf_token": "test-csrf"})
        conn, cursor = self.raw()
        cursor.execute("SELECT COUNT(*) FROM products WHERE organization_id = %s AND last_landed_cost IS NOT NULL",
                       (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.close()
        conn.close()

    def test_a_chart_numbered_2100_still_saves_the_bill(self):
        # The owner's own books number Trade Payables 2100, not the 2000 this
        # program's default chart uses. A bill must save against either.
        conn, cursor = self.raw()
        cursor.execute("UPDATE chart_of_accounts SET code = '2100', name = 'Trade Payables' "
                       "WHERE organization_id = %s AND code = '2000'", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.save_bill().status_code, 302)

    def test_a_company_with_no_payables_account_is_told_so(self):
        conn, cursor = self.raw()
        cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s AND code = '2000'",
                       (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        response = self.save_bill()
        self.assertEqual(response.status_code, 200)     # the form again, not a crash
        self.assertIn("no Trade Payables account", response.get_data(as_text=True))

    def test_the_form_offers_the_product_and_the_landed_cost_tick(self):
        page = self.client.get("/vendors/bills/new").get_data(as_text=True)
        self.assertIn("LC Reference", page)
        self.assertIn("Spread LC charges", page)
        self.assertIn("By quantity", page)
        self.assertIn("Urea 50kg", page)          # the product picker
        self.assertIn("line_kind[]", page)        # the tick writes this, always sent


class ReceiptAllocationTests(_OneCompanyPerTest):
    """A receipt knocks off the invoices it says it settles."""

    def make_invoice(self, amount="60000", due="2026-11-30", post=True, invoice_date="2026-11-01"):
        form = {
            "customer_id": self.customer_id,
            "invoice_date": invoice_date,
            "due_date": due,
            "journal_name": "Customer Invoices",
            "csrf_token": "test-csrf",
            "product_id[]": [str(self.urea_id)],
            "label[]": ["Urea 50kg"],
            "account_id[]": [str(self.account("4000"))],
            "unit[]": ["Bags"],
            "quantity[]": ["1"],
            "rate[]": [amount],
            "discount_percent[]": ["0"],
            "sales_tax_rate_id[]": [""],
        }
        response = self.client.post("/sales/invoices/new", data=form)
        self.assertEqual(response.status_code, 302)
        conn, cursor = self.raw()
        cursor.execute("SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s "
                       "ORDER BY sales_invoice_id DESC", (self.org_id,))
        invoice_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        if post:
            self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        return invoice_id

    def receipt_form(self, **changes):
        form = {
            "customer_id": self.customer_id,
            "amount": "60000",
            "receipt_date": "2026-11-14",
            "deposit_to_account_id": self.account("1000"),
            "payment_method": "Cash",
            "csrf_token": "test-csrf",
        }
        form.update(changes)
        return form

    def save_receipt(self, **changes):
        return self.client.post("/customers/receipts/new", data=self.receipt_form(**changes))

    def invoice_figures(self, invoice_id):
        """What the receipt tests care about: (applied, still due)."""
        conn, cursor = self.raw()
        cursor.execute("SELECT COALESCE(paid_amount, 0), amount_due FROM sales_invoices "
                       "WHERE sales_invoice_id = %s", (invoice_id,))
        paid, due = cursor.fetchone()
        cursor.close()
        conn.close()
        return paid, due

    def nothing_applied(self, invoice_id):
        """True when no money has been applied to the invoice at all."""
        paid, due = self.invoice_figures(invoice_id)
        return paid == Decimal("0") and due is None

    def test_a_receipt_can_settle_an_invoice_and_the_balance_is_real(self):
        invoice_id = self.make_invoice("60000")
        response = self.save_receipt(**{"invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["25000"]})
        self.assertEqual(response.status_code, 302)

        paid, due = self.invoice_figures(invoice_id)
        self.assertEqual(paid, Decimal("25000.00"))
        self.assertEqual(due, Decimal("35000.00"))

        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Invoices this receipt settles", page)
        self.assertIn("25,000.00", page)
        self.assertIn("35,000.00", page)        # 60,000 receipt less 25,000 applied

    def test_one_receipt_can_settle_several_invoices(self):
        first = self.make_invoice("60000")
        second = self.make_invoice("40000")
        self.save_receipt(amount="100000", **{
            "invoice_id[]": [str(first), str(second)],
            "alloc_amount[]": ["60000", "40000"],
        })
        self.assertEqual(self.invoice_figures(first), (Decimal("60000.00"), Decimal("0.00")))
        self.assertEqual(self.invoice_figures(second), (Decimal("40000.00"), Decimal("0.00")))

    def test_one_invoice_can_be_settled_by_two_receipts(self):
        invoice_id = self.make_invoice("60000")
        self.save_receipt(amount="30000", **{"invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["30000"]})
        self.save_receipt(amount="30000", **{"invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["30000"]})
        paid, due = self.invoice_figures(invoice_id)
        self.assertEqual(paid, Decimal("60000.00"))
        self.assertEqual(due, Decimal("0.00"))

    def test_more_than_the_invoice_owes_is_refused(self):
        invoice_id = self.make_invoice("60000")
        response = self.save_receipt(amount="90000", **{
            "invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["90000"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("cannot be applied to it", response.get_data(as_text=True))
        # and nothing at all was saved
        self.assertTrue(self.nothing_applied(invoice_id))
        conn, cursor = self.raw()
        cursor.execute("SELECT COUNT(*) FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.close()
        conn.close()

    def test_more_than_the_receipt_is_for_is_refused(self):
        invoice_id = self.make_invoice("60000")
        response = self.save_receipt(amount="10000", **{
            "invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["20000"]})
        self.assertIn("has been applied to invoices", response.get_data(as_text=True))
        self.assertTrue(self.nothing_applied(invoice_id))

    def test_a_draft_invoice_cannot_be_paid(self):
        invoice_id = self.make_invoice("60000", post=False)
        response = self.save_receipt(**{"invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["60000"]})
        self.assertIn("still a draft", response.get_data(as_text=True))
        self.assertTrue(self.nothing_applied(invoice_id))

    def test_another_customers_invoice_is_refused(self):
        conn, cursor = self.raw()
        cursor.execute("""
            INSERT INTO customers (organization_id, name, nature_of_supply)
            VALUES (%s, 'Someone Else Ltd', 'Supplies') RETURNING customer_id
        """, (self.org_id,))
        other_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        invoice_id = self.make_invoice("60000")
        response = self.save_receipt(customer_id=other_id, **{
            "invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["60000"]})
        self.assertIn("belongs to another customer", response.get_data(as_text=True))

    def test_a_receipt_that_matches_nothing_still_saves(self):
        response = self.save_receipt()
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("not matched to any invoice", page)

    def test_the_picker_lists_only_posted_invoices_with_something_owed(self):
        posted = self.make_invoice("60000")
        self.make_invoice("40000", post=False)
        data = self.client.get(f"/customers/{self.customer_id}/open-invoices").get_json()
        self.assertEqual([invoice["id"] for invoice in data["invoices"]], [posted])
        self.assertEqual(data["invoices"][0]["remaining"], "60000.00")

    def test_a_settled_invoice_leaves_the_picker(self):
        invoice_id = self.make_invoice("60000")
        self.save_receipt(**{"invoice_id[]": [str(invoice_id)], "alloc_amount[]": ["60000"]})
        data = self.client.get(f"/customers/{self.customer_id}/open-invoices").get_json()
        self.assertEqual(data["invoices"], [])


class CustomerAgeingTests(_OneCompanyPerTest):
    """0-30 / 31-60 / 61-90 / older, the three buckets and the fourth."""

    def make_invoice(self, amount, invoice_date, due):
        form = {
            "customer_id": self.customer_id,
            "invoice_date": invoice_date,
            "due_date": due,
            "journal_name": "Customer Invoices",
            "csrf_token": "test-csrf",
            "product_id[]": [str(self.urea_id)],
            "label[]": ["Urea 50kg"],
            "account_id[]": [str(self.account("4000"))],
            "unit[]": ["Bags"],
            "quantity[]": ["1"],
            "rate[]": [amount],
            "discount_percent[]": ["0"],
            "sales_tax_rate_id[]": [""],
        }
        self.client.post("/sales/invoices/new", data=form)
        conn, cursor = self.raw()
        cursor.execute("SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s "
                       "ORDER BY sales_invoice_id DESC", (self.org_id,))
        invoice_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        return invoice_id

    def test_the_four_buckets_are_the_four_promised(self):
        self.make_invoice("1000", "2026-10-20", "2026-10-25")   # 20 days before 14 Nov
        self.make_invoice("2000", "2026-09-20", "2026-09-25")   # 50 days
        self.make_invoice("4000", "2026-08-20", "2026-08-25")   # 81 days
        self.make_invoice("8000", "2026-06-20", "2026-06-25")   # 142 days

        page = self.client.get("/customers/reports/ageing?as_of=2026-11-14").get_data(as_text=True)
        for heading in ("0-30 days", "31-60 days", "61-90 days", "Over 90 days"):
            self.assertIn(heading, page)
        self.assertIn("Gulf Agro Chemicals", page)
        self.assertIn("1,000.00", page)
        self.assertIn("2,000.00", page)
        self.assertIn("4,000.00", page)
        self.assertIn("8,000.00", page)
        self.assertIn("15,000.00", page)      # the total

    def test_an_invoice_without_a_due_date_ages_from_its_own_date(self):
        # 1 Sep to 14 Nov is 74 days, which is the 61-90 bucket.
        self.make_invoice("5000", "2026-09-01", "")
        page = self.client.get("/customers/reports/ageing?as_of=2026-11-14").get_data(as_text=True)
        self.assertIn("61-90 days", page)
        self.assertIn("5,000.00", page)
        self.assertIn("74", page)

    def test_a_settled_invoice_drops_out_of_the_ageing(self):
        invoice_id = self.make_invoice("60000", "2026-10-20", "2026-10-25")
        self.client.post("/customers/receipts/new", data={
            "customer_id": self.customer_id,
            "amount": "60000",
            "receipt_date": "2026-11-14",
            "deposit_to_account_id": self.account("1000"),
            "payment_method": "Cash",
            "csrf_token": "test-csrf",
            "invoice_id[]": [str(invoice_id)],
            "alloc_amount[]": ["60000"],
        })
        page = self.client.get("/customers/reports/ageing?as_of=2026-11-14").get_data(as_text=True)
        self.assertIn("Nothing is owed by any customer", page)

    def test_a_draft_invoice_is_not_aged(self):
        form = {
            "customer_id": self.customer_id,
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-05",
            "journal_name": "Customer Invoices",
            "csrf_token": "test-csrf",
            "product_id[]": [str(self.urea_id)],
            "label[]": ["Urea 50kg"],
            "account_id[]": [str(self.account("4000"))],
            "unit[]": ["Bags"],
            "quantity[]": ["1"],
            "rate[]": ["7000"],
            "discount_percent[]": ["0"],
            "sales_tax_rate_id[]": [""],
        }
        self.client.post("/sales/invoices/new", data=form)     # saved, never posted
        page = self.client.get("/customers/reports/ageing?as_of=2026-11-14").get_data(as_text=True)
        self.assertIn("Nothing is owed by any customer", page)

    def test_the_report_is_under_reports(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("Customer Aging Report", home)
        self.assertEqual(self.client.get("/customers/reports/ageing").status_code, 200)


if __name__ == "__main__":
    unittest.main()
