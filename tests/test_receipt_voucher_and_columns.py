"""
tests/test_receipt_voucher_and_columns.py

Checks for the changes of 15 Nov 2026:

  * the printable voucher for a customer receipt - it exists, it carries the
    voucher number, the amount, the amount in words, the method and, only when
    they apply, the cheque details and the withholding tax;
  * the receipt's own page and the receipts list both lead to that voucher;
  * an invoice has no LC Reference any more (a letter of credit is a purchase
    reference), and the invoice lines' optional columns are tick boxes;
  * the Customer Partner Ledger: a running balance per customer, its own
    screen, and another company's customer getting nothing;
  * the menu: the two tax screens under Business Settings, both partner
    ledgers under Reports.

Needs DATABASE_URL pointing at a TEST database; each test makes its own
company and removes it again.
"""
import unittest
import uuid
from datetime import date
from decimal import Decimal

from _people import member

from customers.services import amount_in_words


class _OneCompany(unittest.TestCase):
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
        conn, cursor = self.raw()
        cursor.execute(
            "INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
            ("TEST_VOUCHER_" + uuid.uuid4().hex[:8],))
        self.org_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) "
            "VALUES ('Voucher Tester', %s, 'hash', TRUE) RETURNING user_id",
            (f"voucher_{uuid.uuid4().hex[:8]}@x.local",))
        self.user_id = cursor.fetchone()[0]
        member(cursor, self.user_id, self.org_id)
        for code, name, kind in (("1000", "Cash in Hand", "Asset"),
                                 ("1100", "Trade Receivables", "Asset"),
                                 ("1150", "WHT Receivable - Supplies", "Asset"),
                                 ("4000", "Sales Revenue", "Revenue")):
            cursor.execute("""
                INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (self.org_id, code, name, kind))
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1000'",
                       (self.org_id,))
        self.cash_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO customers (organization_id, name, nature_of_supply, address, city, ntn)
            VALUES (%s, 'Gulf Agro Chemicals', 'Supplies', 'Band road Pindi bhattian', 'Lahore', '4900884-2')
            RETURNING customer_id
        """, (self.org_id,))
        self.customer_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Voucher Tester"
            flask_session["csrf_token"] = "test-csrf"

    def tearDown(self):
        conn, cursor = self.raw()
        for statement in (
                "DELETE FROM customer_receipts WHERE organization_id = %s",
                "DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)",
                "DELETE FROM sales_invoices WHERE organization_id = %s",
                "DELETE FROM journal_lines WHERE journal_header_id IN "
                "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
                "DELETE FROM journal_headers WHERE organization_id = %s",
                "DELETE FROM customers WHERE organization_id = %s",
                "DELETE FROM products WHERE organization_id = %s",
                "DELETE FROM chart_of_accounts WHERE organization_id = %s",
                "DELETE FROM organization_memberships WHERE organization_id = %s",
                "DELETE FROM organizations WHERE organization_id = %s",
                "DELETE FROM users WHERE user_id = %s"):
            cursor.execute(statement, (self.user_id,) if statement.endswith("user_id = %s") else (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()

    # -- helpers ---------------------------------------------------------
    def raw(self):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    def make_receipt(self, amount="592277.68", method="Cash", voucher_no="CPV/2026/00001",
                     cheque_no=None, cheque_bank=None, cheque_date=None, wht=None, wht_amount=None):
        conn, cursor = self.raw()
        cursor.execute("""
            INSERT INTO customer_receipts
                (organization_id, customer_id, amount, receipt_date, memo, deposit_to_account_id,
                 has_wht, wht_rate, wht_amount, voucher_no, payment_type, journal_name, payment_method,
                 cheque_no, cheque_bank, cheque_date)
            VALUES (%s, %s, %s, %s, 'Against invoice', %s, %s, %s, %s, %s,
                    'Customer Payment', 'Customer Receipts', %s, %s, %s, %s)
            RETURNING customer_receipt_id
        """, (self.org_id, self.customer_id, Decimal(amount), date(2026, 11, 4), self.cash_id,
              wht is not None, wht, wht_amount, voucher_no, method,
              cheque_no, cheque_bank, cheque_date))
        receipt_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return receipt_id

    def make_invoice(self, amount="56550.00", status="Posted", invoice_date=date(2026, 6, 15),
                     number="INV/2026/00001"):
        conn, cursor = self.raw()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, invoice_no, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING sales_invoice_id
        """, (self.org_id, self.customer_id, invoice_date, number, status))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines (sales_invoice_id, line_order, label, quantity, rate, total)
            VALUES (%s, 0, 'Goods', 1, %s, %s)
        """, (invoice_id, Decimal(amount), Decimal(amount)))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id


class AmountInWordsTests(unittest.TestCase):
    """The words written on the voucher."""

    def test_whole_rupees(self):
        self.assertEqual(amount_in_words(5), "Rupees Five Only")
        self.assertEqual(amount_in_words(21), "Rupees Twenty-One Only")
        self.assertEqual(amount_in_words(105), "Rupees One Hundred Five Only")
        self.assertEqual(amount_in_words(100000), "Rupees One Hundred Thousand Only")

    def test_rupees_and_paisa(self):
        self.assertEqual(amount_in_words(Decimal("3500.50")),
                         "Rupees Three Thousand Five Hundred and Paisa Fifty Only")
        self.assertEqual(amount_in_words(Decimal("1500000")), "Rupees One Million Five Hundred Thousand Only")

    def test_zero_and_big_numbers(self):
        self.assertEqual(amount_in_words(0), "Rupees Zero Only")
        self.assertTrue(amount_in_words(Decimal("35067502.70")).startswith(
            "Rupees Thirty-Five Million Sixty-Seven Thousand Five Hundred Two"))


class ReceiptVoucherTests(_OneCompany):
    """Reports and printing: the customer receipt voucher."""

    def test_the_voucher_exists_and_carries_the_receipt(self):
        receipt_id = self.make_receipt()
        page = self.client.get(f"/customers/receipts/{receipt_id}/print")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("CPV/2026/00001", body)
        self.assertIn("CASH RECEIPT VOUCHER", body)
        self.assertIn("Gulf Agro Chemicals", body)
        self.assertIn("Band road Pindi bhattian", body)
        self.assertIn("4900884-2", body)                                  # NTN
        self.assertIn("592,277.68 Rs", body)                              # the amount
        self.assertIn("Rupees Five Hundred Ninety-Two Thousand Two Hundred Seventy-Seven", body)
        self.assertIn("Prepared By", body)                                # the shared frame

    def test_a_cheque_voucher_shows_the_cheque(self):
        receipt_id = self.make_receipt(method="Cheque", cheque_no="8841203",
                                       cheque_bank="Meezan Bank, Multan Branch",
                                       cheque_date=date(2026, 11, 4))
        body = self.client.get(f"/customers/receipts/{receipt_id}/print").get_data(as_text=True)
        self.assertIn("CHEQUE RECEIPT VOUCHER", body)
        self.assertIn("8841203", body)
        self.assertIn("Meezan Bank, Multan Branch", body)
        self.assertIn("04/11/2026", body)

    def test_a_cash_voucher_has_no_cheque_block(self):
        receipt_id = self.make_receipt(method="Cash")
        body = self.client.get(f"/customers/receipts/{receipt_id}/print").get_data(as_text=True)
        self.assertNotIn("Cheque Details", body)

    def test_withholding_tax_is_shown_only_when_it_happened(self):
        plain = self.make_receipt(amount="1000.00", voucher_no="CPV/2026/00002")
        plain_body = self.client.get(f"/customers/receipts/{plain}/print").get_data(as_text=True)
        self.assertNotIn("Withholding Tax Deducted", plain_body)

        taxed = self.make_receipt(amount="1000.00", voucher_no="CPV/2026/00003",
                                  wht=Decimal("4.5"), wht_amount=Decimal("45.00"))
        taxed_body = self.client.get(f"/customers/receipts/{taxed}/print").get_data(as_text=True)
        self.assertIn("Withholding Tax Deducted", taxed_body)
        self.assertIn("WHT @ 4.5%", taxed_body)
        self.assertIn("45.00 Rs", taxed_body)
        self.assertIn("955.00 Rs", taxed_body)                 # net received

    def test_another_company_cannot_print_it(self):
        receipt_id = self.make_receipt()
        conn, cursor = self.raw()
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       ("TEST_OTHER_" + uuid.uuid4().hex[:8],))
        other_org = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Other', %s, 'hash', TRUE) RETURNING user_id",
                       (f"other_{uuid.uuid4().hex[:8]}@x.local",))
        other_user = cursor.fetchone()[0]
        # a member of the other company: this test is about its receipt being
        # unprintable here, not about being turned away at the door (a 403)
        member(cursor, other_user, other_org)
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = other_org
            flask_session["user_id"] = other_user
            flask_session["csrf_token"] = "test-csrf"
        response = self.client.get(f"/customers/receipts/{receipt_id}/print")
        self.assertEqual(response.status_code, 404)
        conn, cursor = self.raw()
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (other_org,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (other_user,))
        conn.commit()
        cursor.close()
        conn.close()

    def test_the_receipt_page_and_the_list_lead_to_the_voucher(self):
        receipt_id = self.make_receipt()
        detail = self.client.get(f"/customers/receipts/{receipt_id}").get_data(as_text=True)
        # the voucher is printed from the page's gear menu now, not a button
        self.assertIn("Print / PDF voucher", detail)
        self.assertIn(f"/customers/receipts/{receipt_id}/print", detail)
        listing = self.client.get("/customers/receipts").get_data(as_text=True)
        self.assertIn(f"/customers/receipts/{receipt_id}/print", listing)


class CustomerPartnerLedgerTests(_OneCompany):
    """Reports > Customer Partner Ledger."""

    def test_the_running_balance_adds_invoices_and_takes_away_receipts(self):
        self.make_invoice(amount="1000.00", invoice_date=date(2026, 6, 15), number="INV/2026/00001")
        self.make_invoice(amount="500.00", invoice_date=date(2026, 7, 15), number="INV/2026/00002")
        self.make_receipt(amount="300.00", voucher_no="CPV/2026/00001")
        page = self.client.get(
            f"/customers/reports/partner-ledger?customer_id={self.customer_id}")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Customer Partner Ledger", body)
        self.assertIn("INV/2026/00001", body)
        self.assertIn("CPV/2026/00001", body)
        self.assertIn("1,000.00", body)
        self.assertIn("300.00", body)
        # 1,000 + 500 - 300 is what he still owes
        self.assertIn("1,200.00", body)

    def test_a_draft_invoice_is_not_a_debt_yet(self):
        self.make_invoice(amount="900.00", status="Draft", number="INV/2026/00009")
        body = self.client.get(
            f"/customers/reports/partner-ledger?customer_id={self.customer_id}").get_data(as_text=True)
        self.assertNotIn("INV/2026/00009", body)
        self.assertIn("Nothing has been invoiced or received", body)

    def test_the_dates_narrow_the_period(self):
        self.make_invoice(amount="1000.00", invoice_date=date(2026, 6, 15), number="INV/2026/00001")
        self.make_invoice(amount="500.00", invoice_date=date(2026, 9, 15), number="INV/2026/00002")
        body = self.client.get(
            f"/customers/reports/partner-ledger?customer_id={self.customer_id}"
            f"&start_date=2026-09-01&end_date=2026-09-30").get_data(as_text=True)
        self.assertNotIn("INV/2026/00001", body)
        self.assertIn("INV/2026/00002", body)
        self.assertIn("1,000.00", body)            # brought forward from June

    def test_another_companys_customer_gets_nothing(self):
        conn, cursor = self.raw()
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       ("TEST_STRANGER_" + uuid.uuid4().hex[:8],))
        other_org = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, name, nature_of_supply) "
                       "VALUES (%s, 'Someone Else', 'Supplies') RETURNING customer_id", (other_org,))
        stranger = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        response = self.client.get(f"/customers/reports/partner-ledger?customer_id={stranger}")
        self.assertEqual(response.status_code, 404)
        conn, cursor = self.raw()
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (other_org,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (other_org,))
        conn.commit()
        cursor.close()
        conn.close()

    def test_the_screen_opens_without_a_customer(self):
        page = self.client.get("/customers/reports/partner-ledger")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Choose a customer above", page.get_data(as_text=True))


class InvoiceColumnsAndLcTests(_OneCompany):
    """The sale invoice: no LC Reference, and columns you can tick."""

    def test_the_entry_screen_has_no_lc_box_and_has_the_column_boxes(self):
        form = self.client.get("/sales/invoices/new").get_data(as_text=True)
        self.assertNotIn('name="lc_reference"', form)
        self.assertNotIn("LC Reference", form)
        self.assertIn('id="column-picker"', form)
        for column in ("desc", "account", "unit", "disc", "tax"):
            self.assertIn(f'data-col="{column}"', form)

    def test_the_line_columns_are_not_fixed_percentages(self):
        form = self.client.get("/sales/invoices/new").get_data(as_text=True)
        head = form.split('<table class="lp-table lines-table"', 1)[1].split("</thead>", 1)[0]
        self.assertNotIn("width:", head.replace("width: 46px", ""))
        self.assertIn("col-product", head)
        self.assertIn("col-disc", head)

    def test_an_invoice_still_saves_without_the_lc_field(self):
        conn, cursor = self.raw()
        cursor.execute("INSERT INTO products (organization_id, name, unit, is_active) "
                       "VALUES (%s, 'Carton Tape', 'Units', TRUE) RETURNING product_id", (self.org_id,))
        product_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '4000'",
                       (self.org_id,))
        revenue_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        response = self.client.post("/sales/invoices/new", data={
            "csrf_token": "test-csrf",
            "customer_id": self.customer_id,
            "invoice_date": "2026-11-15",
            "due_date": "2026-12-15",
            "payment_terms": "30 days",
            "delivery_date": "2026-11-15",
            "product_id[]": str(product_id),
            "label[]": "Carton Tape",
            "account_id[]": str(revenue_id),
            "unit[]": "Units",
            "quantity[]": "2",
            "rate[]": "500",
            "discount_percent[]": "0",
            "sales_tax_rate_id[]": "",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        conn, cursor = self.raw()
        cursor.execute("SELECT invoice_no, lc_reference FROM sales_invoices "
                       "WHERE organization_id = %s ORDER BY sales_invoice_id DESC LIMIT 1", (self.org_id,))
        number, lc_reference = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertTrue(number.startswith("INV/"))
        self.assertIsNone(lc_reference)


class MenuTests(_OneCompany):
    """Where the screens sit in the menu."""

    def test_tax_rates_are_under_business_settings(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Business Settings", page)
        settings = page.split("Business Settings", 1)[1].split("</div>", 1)[0]
        self.assertIn("Sales Tax Rates", settings)
        self.assertIn("WHT Rates", settings)

    def test_both_partner_ledgers_are_under_reports(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Reports", page)
        reports = page.split(">Reports<", 1)[1].split("</div>", 1)[0]
        self.assertIn("Customer Partner Ledger", reports)
        self.assertIn("Vendor Partner Ledger", reports)
        self.assertIn("Trial Balance", reports)


if __name__ == "__main__":
    unittest.main()
