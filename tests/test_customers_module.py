"""
tests/test_customers_module.py

Checks for the Round 10 work on the Customers module. One class, and it needs
DATABASE_URL pointed at a TEST database:

    python -m pytest tests/test_customers_module.py -q

What it proves, slice by slice:

  the duplicate guard   a second customer with the same NTN, CNIC or phone is
                        refused, inside one company only, and blank numbers are
                        never compared with each other
  one definition of due the customer card's "still due", the ageing report, the
                        partner ledger closing figure and the dashboard all
                        count the same thing: invoiced less what receipts have
                        been MATCHED to invoices
  credit limit          the room left is invoiced less received; an invoice that
                        would go over it is refused at posting unless someone
                        ticks "save anyway", and a draft is always allowed
  tax and terms         province, registration, filer, withholding tax and the
                        payment terms are saved, and the terms fill in an
                        invoice's due date when nobody types one
  statement             opening + debits - credits = closing, and its ageing
                        summary is the same figure the card shows
  the read-only page    /customers/<id> shows the customer without the edit form
"""
import unittest
import uuid

from _people import member
from datetime import date, timedelta
from decimal import Decimal as D

from customers.services import (
    REGISTRATION_OPTIONS, account_statement, bucket_totals, credit_position, credit_status,
    customer_ageing, customer_open_invoices, customer_totals,
)


class CustomersModuleTests(unittest.TestCase):

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
        cls.org_name = f"TEST_CUSTOMERS_MODULE_{uuid.uuid4().hex[:8]}"
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (cls.org_name,))
        cls.org_id = cursor.fetchone()[0]
        cls.user_email = f"test_{uuid.uuid4().hex[:8]}@customers-module.local"
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Customers Module Tester', %s, 'hash', TRUE) RETURNING user_id", (cls.user_email,))
        cls.user_id = cursor.fetchone()[0]
        member(cursor, cls.user_id, cls.org_id)
        # the accounts the invoice and receipt screens insist on
        cls.revenue_account_id = None
        for code, name, account_type in (("1100", "Trade Receivables", "Asset"),
                                         ("4000", "Sales Revenue", "Revenue"),
                                         ("2200", "Output Tax Payable", "Liability")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, account_type))
            if code == "4000":
                cls.revenue_account_id = cursor.fetchone()[0]
        # a product, because the invoice screen only writes a line for one
        cursor.execute("INSERT INTO products (organization_id, name, sales_price) VALUES (%s, 'Freight', 0) "
                       "RETURNING product_id", (cls.org_id,))
        cls.product_id = cursor.fetchone()[0]
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
        for table in ("customer_receipts", "customer_contacts"):
            cursor.execute(f"DELETE FROM {table} WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (cls.org_id,))
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
        for table in ("customer_receipts", "customer_contacts"):
            cursor.execute(f"DELETE FROM {table} WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Customers Module Tester"
            flask_session["csrf_token"] = "test-csrf"

    # -- helpers ---------------------------------------------------------
    def db(self):
        from core.db import get_conn
        return get_conn()

    def save(self, url="/customers/new", **typed):
        form = {"csrf_token": "test-csrf", "name": "Acme Traders", "partner_type": "company",
                "nature_of_supply": "Supplies", "credit_limit": "0", "status": "active"}
        form.update(typed)
        return self.client.post(url, data=form, follow_redirects=False)

    def customer_id(self, name="Acme Traders"):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT customer_id FROM customers WHERE organization_id = %s AND name = %s",
                       (self.org_id, name))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row[0] if row else None

    def add_customer(self, name="Acme Traders", **extra):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, %s, %s) "
                       "RETURNING customer_id",
                       (self.org_id, (self.customer_count() + 1), name))
        customer_id = cursor.fetchone()[0]
        for field, value in extra.items():
            cursor.execute(f"UPDATE customers SET {field} = %s WHERE customer_id = %s", (value, customer_id))
        conn.commit()
        cursor.close()
        conn.close()
        return customer_id

    def customer_count(self):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customers WHERE organization_id = %s", (self.org_id,))
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count

    def invoice(self, customer_id, total, status="Posted", day="2026-09-01", due=None):
        from sales.services import give_invoice_a_number
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING sales_invoice_id
        """, (self.org_id, customer_id, day, due, status))
        invoice_id = cursor.fetchone()[0]
        # the product and the account, because the invoice page joins on them
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, product_id, account_id, line_order, "
                       "label, unit, quantity, rate, total) "
                       "VALUES (%s, %s, %s, 0, 'Line', 'Units', 1, %s, %s)",
                       (invoice_id, self.product_id, self.revenue_account_id, total, total))
        give_invoice_a_number(cursor, self.org_id, invoice_id, day)
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def receipt(self, customer_id, amount, allocations=(), day="2026-10-01"):
        from customers.services import apply_receipt_to_invoices
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date) "
                       "VALUES (%s, %s, %s, %s) RETURNING customer_receipt_id",
                       (self.org_id, customer_id, amount, day))
        receipt_id = cursor.fetchone()[0]
        if allocations:
            apply_receipt_to_invoices(cursor, self.org_id, receipt_id, customer_id, D(str(amount)),
                                      [(invoice_id, D(str(amount_))) for invoice_id, amount_ in allocations])
        conn.commit()
        cursor.close()
        conn.close()
        return receipt_id

    # -- the duplicate guard ---------------------------------------------
    def test_a_second_customer_with_the_same_ntn_is_refused(self):
        self.assertEqual(self.save(name="Gulf Agro", ntn="1234567-8").status_code, 302)
        response = self.save(name="Gulf Agro Chemicals (Pvt) Ltd", ntn="1234567-8")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("NTN 1234567-8 is already on customer", page)
        self.assertIn("Gulf Agro", page)
        self.assertEqual(self.customer_count(), 1)                          # the second one was not saved

    def test_a_second_customer_with_the_same_cnic_is_refused(self):
        self.save(name="Ravi Traders", cnic="3520112345678")
        page = self.save(name="Ravi Traders Lahore", cnic="3520112345678").get_data(as_text=True)
        self.assertIn("CNIC 3520112345678 is already on customer", page)
        self.assertEqual(self.customer_count(), 1)

    def test_a_second_customer_with_the_same_phone_is_refused(self):
        self.save(name="Kasur Traders", phone="0300-1234567")
        # the same number written differently is still the same number
        page = self.save(name="Kasur Traders Two", phone="(0300) 123 45 67").get_data(as_text=True)
        self.assertIn("Phone (0300) 123 45 67 is already on customer", page)
        self.assertEqual(self.customer_count(), 1)

    def test_a_blank_tax_number_is_never_a_duplicate(self):
        # most walk-in customers have no NTN, and many share an empty box
        self.assertEqual(self.save(name="Walk In One", phone="").status_code, 302)
        self.assertEqual(self.save(name="Walk In Two", phone="").status_code, 302)
        self.assertEqual(self.save(name="Walk In Three", ntn="", cnic="").status_code, 302)
        self.assertEqual(self.customer_count(), 3)

    def test_a_customer_may_keep_his_own_tax_number(self):
        self.save(name="Keep Traders", ntn="7654321-0", phone="03211234567")
        customer_id = self.customer_id("Keep Traders")
        # saving the same card again is not a duplicate of itself
        response = self.save(url=f"/customers/{customer_id}/edit", name="Keep Traders",
                             ntn="7654321-0", phone="03211234567", status="active")
        self.assertEqual(response.status_code, 302)
        # nor may he move his number onto somebody else's card
        self.save(name="Other Traders")
        other_id = self.customer_id("Other Traders")
        page = self.save(url=f"/customers/{other_id}/edit", name="Other Traders", ntn="7654321-0",
                         status="active").get_data(as_text=True)
        self.assertIn("already on customer", page)

    # -- tax and terms ---------------------------------------------------
    def test_the_terms_are_saved_and_read_back(self):
        response = self.save(name="Taxed Traders", province="Sindh", registration_type="Retail",
                             is_filer="1", withholds_wht="1", default_wht_rate="4.5",
                             payment_terms_days="30")
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT province, registration_type, is_filer, withholds_wht, default_wht_rate, "
                       "payment_terms_days FROM customers WHERE organization_id = %s", (self.org_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(row, ("Sindh", "Retail", True, True, D("4.50"), 30))

    def test_the_terms_are_refused_when_they_do_not_make_sense(self):
        page = self.save(name="Bad Terms", registration_type="Registered-ish").get_data(as_text=True)
        self.assertIn("Registration must be Registered, Unregistered or Retail.", page)
        page = self.save(name="Bad Terms", default_wht_rate="101").get_data(as_text=True)
        self.assertIn("Default WHT rate must be between 0 and 100.", page)
        page = self.save(name="Bad Terms", payment_terms_days="400").get_data(as_text=True)
        self.assertIn("Payment terms cannot be longer than 365 days.", page)
        page = self.save(name="Bad Terms", payment_terms_days="thirty").get_data(as_text=True)
        self.assertIn("Payment terms must be a whole number of days", page)
        self.assertEqual(self.customer_count(), 0)

    def test_the_registration_types_are_the_three_pakistani_ones(self):
        self.assertEqual(REGISTRATION_OPTIONS, ["Registered", "Unregistered", "Retail"])

    def test_the_terms_are_offered_as_json(self):
        customer_id = self.add_customer("Json Traders", payment_terms_days=45, withholds_wht=True,
                                        default_wht_rate=D("4.50"), credit_limit=D("100000"))
        data = self.client.get(f"/customers/{customer_id}/terms").get_json()
        self.assertEqual((data["payment_terms_days"], data["withholds_wht"], data["default_wht_rate"]),
                         (45, True, "4.50"))
        self.assertTrue(data["credit_has_limit"])
        self.assertEqual(data["credit_room"], "100000.00")
        self.assertEqual(self.client.get("/customers/999999/terms").status_code, 404)

    def test_an_invoice_with_no_due_date_takes_it_from_the_terms(self):
        customer_id = self.add_customer("Terms Traders", payment_terms_days=30)
        product_free_line = {"csrf_token": "test-csrf", "customer_id": customer_id,
                             "invoice_date": "2026-09-01", "due_date": "", "payment_terms": "",
                             "product_id[]": str(self.product_id),
                             "account_id[]": str(self.revenue_account_id), "label[]": "Freight", "unit[]": "Units",
                             "quantity[]": "1", "rate[]": "1000", "discount_percent[]": "0",
                             "sales_tax_rate_id[]": ""}
        response = self.client.post("/sales/invoices/new", data=product_free_line, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT due_date, payment_terms FROM sales_invoices WHERE organization_id = %s",
                       (self.org_id,))
        due_date, payment_terms = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(due_date, date(2026, 9, 1) + timedelta(days=30))
        self.assertEqual(payment_terms, "30 days")

    # -- one definition of "due" -----------------------------------------
    def test_due_is_invoiced_less_what_receipts_are_matched_to(self):
        customer_id = self.add_customer("Due Traders")
        first = self.invoice(customer_id, "1000.00")
        self.invoice(customer_id, "500.00")
        # a receipt of 900: 600 matched to the first invoice, 300 left unapplied
        self.receipt(customer_id, "900.00", allocations=[(first, "600.00")])
        conn = self.db()
        cursor = conn.cursor()
        totals = customer_totals(cursor, self.org_id, customer_id)
        cursor.close()
        conn.close()
        self.assertEqual(totals["invoiced"], D("1500.00"))
        self.assertEqual(totals["received"], D("900.00"))
        self.assertEqual(totals["applied"], D("600.00"))
        self.assertEqual(totals["due"], D("900.00"))                        # 1500 - 600 matched
        self.assertEqual(totals["unapplied"], D("300.00"))                  # money in hand, not matched
        self.assertEqual(totals["net"], D("600.00"))                        # 1500 - 900 received

    def test_the_card_the_ageing_and_the_ledger_agree(self):
        customer_id = self.add_customer("Agree Traders")
        first = self.invoice(customer_id, "1000.00", day="2026-09-01")
        self.invoice(customer_id, "500.00", day="2026-10-01")
        self.receipt(customer_id, "400.00", allocations=[(first, "400.00")], day="2026-10-05")

        conn = self.db()
        cursor = conn.cursor()
        totals = customer_totals(cursor, self.org_id, customer_id)
        rows, buckets = customer_ageing(cursor, self.org_id, as_of=date(2026, 11, 1))
        statement = account_statement(cursor, self.org_id, customer_id, None, date(2026, 11, 1))
        open_rows = customer_open_invoices(cursor, self.org_id, customer_id, as_of=date(2026, 11, 1))
        cursor.close()
        conn.close()

        self.assertEqual(totals["due"], D("1100.00"))                       # 1500 invoiced - 400 matched
        self.assertEqual(buckets["total"], D("1100.00"))                    # the ageing report
        self.assertEqual(bucket_totals(open_rows)["total"], D("1100.00"))   # the open-invoice list
        self.assertEqual(statement["closing"], totals["net"])               # 1500 invoiced - 900?? no: 1500 - 400
        self.assertEqual([row["name"] for row in rows], ["Agree Traders"])

    def test_the_statement_adds_up_over_a_period(self):
        customer_id = self.add_customer("Statement Traders")
        self.invoice(customer_id, "1000.00", day="2026-08-01")
        self.invoice(customer_id, "700.00", day="2026-09-01")
        self.receipt(customer_id, "250.00", day="2026-09-10")

        conn = self.db()
        cursor = conn.cursor()
        whole = account_statement(cursor, self.org_id, customer_id)
        period = account_statement(cursor, self.org_id, customer_id, date(2026, 9, 1), date(2026, 9, 30))
        cursor.close()
        conn.close()

        self.assertEqual(whole["closing"], D("1450.00"))                    # 1700 invoiced - 250 received
        self.assertEqual(period["opening"], D("1000.00"))                   # what was owed on 1 September
        self.assertEqual((period["debit_total"], period["credit_total"]), (D("700.00"), D("250.00")))
        self.assertEqual(period["opening"] + period["debit_total"] - period["credit_total"],
                         period["closing"])
        self.assertEqual(period["closing"], whole["closing"])               # stopping at 30 Sep loses nothing

    # -- the credit limit ------------------------------------------------
    def test_the_room_left_counts_what_is_invoiced_after_receipts(self):
        customer_id = self.add_customer("Limit Traders", credit_limit=D("1000"))
        first = self.invoice(customer_id, "800.00")
        self.receipt(customer_id, "500.00", allocations=[(first, "500.00")])
        conn = self.db()
        cursor = conn.cursor()
        position = credit_status(cursor, self.org_id, customer_id)
        cursor.close()
        conn.close()
        self.assertEqual(position["used"], D("300.00"))                     # 800 invoiced - 500 received
        self.assertEqual(position["room"], D("700.00"))
        self.assertFalse(position["over"])

    def test_a_draft_that_would_go_over_the_limit_is_still_saved(self):
        customer_id = self.add_customer("Draft Traders", credit_limit=D("1000"))
        self.invoice(customer_id, "900.00")
        page = self.save_invoice(customer_id, "500.00")
        self.assertEqual(page.status_code, 302)                            # a draft changes no balance
        self.assertEqual(self.invoice_status(), ["Draft", "Posted"])       # the 900 is posted, the 500 is a draft

    def test_a_posted_invoice_that_would_go_over_the_limit_is_refused(self):
        customer_id = self.add_customer("Blocked Traders", credit_limit=D("1000"))
        self.invoice(customer_id, "900.00")
        invoice_id = self.invoice(customer_id, "500.00", status="Draft")
        response = self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("over_limit=1", response.headers["Location"])
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        status = cursor.fetchone()[0]
        # the page then says why, and offers the tick
        # the page it was sent back to, which says the post was refused
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        cursor.close()
        conn.close()
        self.assertEqual(status, "Draft")                                   # nothing was posted
        self.assertIn("goes over", page)
        self.assertIn("not posted", page)
        self.assertIn("override_credit_limit", page)
        self.assertIn("Save anyway", page)

    def test_save_anyway_posts_it(self):
        customer_id = self.add_customer("Anyway Traders", credit_limit=D("1000"))
        self.invoice(customer_id, "900.00")
        invoice_id = self.invoice(customer_id, "500.00", status="Draft")
        response = self.client.post(f"/sales/invoices/{invoice_id}/post",
                                    data={"csrf_token": "test-csrf", "override_credit_limit": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("over_limit", response.headers["Location"])
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM sales_invoices WHERE sales_invoice_id = %s", (invoice_id,))
        status = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(status, "Posted")

    def test_a_customer_with_no_limit_is_never_stopped(self):
        customer_id = self.add_customer("No Limit Traders", credit_limit=D("0"))
        self.invoice(customer_id, "9000.00")
        invoice_id = self.invoice(customer_id, "5000.00", status="Draft")
        response = self.client.post(f"/sales/invoices/{invoice_id}/post", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("over_limit", response.headers["Location"])

    def test_the_credit_position_with_an_invoice_about_to_be_added(self):
        position = credit_position({"net": D("900.00")}, D("1000"), extra=D("200"))
        self.assertEqual(position["room"], D("-100.00"))
        self.assertEqual(position["over_by_text"], "100.00")
        self.assertTrue(position["over"])

    # -- the screens ------------------------------------------------------
    def test_the_read_only_page_shows_the_customer(self):
        customer_id = self.add_customer("Viewed Traders", province="Sindh", registration_type="Retail",
                                        payment_terms_days=21, credit_limit=D("50000"))
        self.invoice(customer_id, "1200.00")
        response = self.client.get(f"/customers/{customer_id}")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Viewed Traders", page)
        self.assertIn("Retail", page)
        self.assertIn("21 days", page)
        self.assertIn("1,200.00", page)
        # the page is not the edit form: no card to fill in and save
        self.assertNotIn('name="credit_limit"', page)
        self.assertNotIn("Save Changes", page)

    def test_the_statement_screen_prints(self):
        customer_id = self.add_customer("Printed Traders")
        self.invoice(customer_id, "1500.00")
        response = self.client.get(f"/customers/{customer_id}/statement")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Statement of Account", page)
        self.assertIn("Printed Traders", page)
        self.assertIn("1,500.00", page)
        self.assertIn("How old the balance is", page)
        # a start and an end date are accepted too
        response = self.client.get(f"/customers/{customer_id}/statement?start_date=2026-01-01&end_date=2026-12-31")
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_customer_is_a_404(self):
        for path in ("/customers/999999", "/customers/999999/statement"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    # -- helpers used by the last few tests -------------------------------
    def save_invoice(self, customer_id, amount):
        form = {"csrf_token": "test-csrf", "customer_id": customer_id, "invoice_date": "2026-09-15",
                "due_date": "", "payment_terms": "", "product_id[]": str(self.product_id), "account_id[]": str(self.revenue_account_id),
                "label[]": "Freight", "unit[]": "Units", "quantity[]": "1", "rate[]": amount,
                "discount_percent[]": "0", "sales_tax_rate_id[]": ""}
        return self.client.post("/sales/invoices/new", data=form, follow_redirects=False)

    def invoice_status(self):
        conn = self.db()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM sales_invoices WHERE organization_id = %s ORDER BY sales_invoice_id DESC",
                       (self.org_id,))
        rows = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows


if __name__ == "__main__":
    unittest.main()
