"""
tests/test_customer_receipts_vouchers.py

Checks for the three customer-side screens finished in the sixth round:

  * the receipt screen with the boxes of his own screen - Payment Type,
    Journal, Voucher Number, Payment Method, and the Cheques tab;
  * the voucher number (CPV/2026/00001) - its shape, that it goes up, that it
    starts again each year, and that it is never handed out twice;
  * what a contact card IS (Invoice Address / Delivery Address / Other), and
    the Purchases figure that comes from the vendor a customer is linked to.

Like the other database tests, this needs DATABASE_URL pointing at a TEST
database; every test makes its own company inside it and removes it again.

    python -m pytest tests/test_customer_receipts_vouchers.py -q
"""
import unittest
import uuid

from customers.services import (
    ADDRESS_TYPE_OPTIONS, PAYMENT_METHOD_OPTIONS, PAYMENT_TYPE_OPTIONS,
    clean_contact_form, receipt_voucher_number,
)


class ReceiptVoucherRuleTests(unittest.TestCase):
    """The parts that need no database."""

    def test_the_shape_of_a_voucher_number(self):
        self.assertEqual(receipt_voucher_number(2026, 1), "CPV/2026/00001")
        self.assertEqual(receipt_voucher_number(2026, 42), "CPV/2026/00042")
        self.assertEqual(receipt_voucher_number(2027, 1), "CPV/2027/00001")   # a new year starts again

    def test_the_answers_his_screen_offers(self):
        self.assertEqual(ADDRESS_TYPE_OPTIONS, ["Invoice Address", "Delivery Address", "Other"])
        self.assertIn("Customer Payment", PAYMENT_TYPE_OPTIONS)
        self.assertEqual(PAYMENT_METHOD_OPTIONS[0], "Cash")
        self.assertIn("Cheque", PAYMENT_METHOD_OPTIONS)

    def test_a_contact_without_a_type_is_other(self):
        data, problems = clean_contact_form({"name": "Hafizabad godown"})
        self.assertEqual(problems, [])
        self.assertEqual(data["address_type"], "Other")

    def test_a_contact_type_that_is_not_offered_is_refused(self):
        _data, problems = clean_contact_form({"name": "Godown", "address_type": "Warehouse"})
        self.assertTrue(any("Contact type" in problem for problem in problems))


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
            ("TEST_RECEIPTS_" + uuid.uuid4().hex[:8],))
        self.org_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) "
            "VALUES ('Receipt Tester', %s, 'hash', TRUE) RETURNING user_id",
            (f"receipt_{uuid.uuid4().hex[:8]}@x.local",))
        self.user_id = cursor.fetchone()[0]
        for code, name, kind in (("1100", "Trade Receivables", "Asset"),
                                 ("1150", "WHT Receivable - Supplies", "Asset"),
                                 ("1160", "WHT Receivable - Services", "Asset"),
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
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1100'",
                       (self.org_id,))
        self.deposit_account_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Receipt Tester"
            flask_session["csrf_token"] = "test-csrf"

    def tearDown(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM customer_contacts WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
                       "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM products WHERE organization_id = %s", (self.org_id,))
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

    def receipt_form(self, **changes):
        form = {
            "customer_id": self.customer_id,
            "amount": "50000",
            "receipt_date": "2026-11-14",
            "deposit_to_account_id": self.deposit_account_id,
            "csrf_token": "test-csrf",
        }
        form.update(changes)
        return form

    def save_receipt(self, **changes):
        return self.client.post("/customers/receipts/new", data=self.receipt_form(**changes))

    def voucher_number_of(self, location):
        return self.client.get(location).get_data(as_text=True)


class ReceiptScreenTests(_OneCompanyPerTest):

    def test_the_form_has_the_boxes_of_his_screen(self):
        page = self.client.get("/customers/receipts/new").get_data(as_text=True)
        for box in ("Payment Type", "Journal", "Voucher Number", "Payment Method",
                    "Cheque Number", "Cheque Date"):
            self.assertIn(box, page)
        for answer in ("Customer Payment", "Cash", "Cheque", "Bank Transfer"):
            self.assertIn(answer, page)

    def test_a_cheque_receipt_keeps_everything_and_gets_a_number(self):
        response = self.save_receipt(payment_type="Customer Payment", journal_name="Bank Receipts",
                                     payment_method="Cheque", cheque_no="80123456",
                                     cheque_bank="Meezan Bank", cheque_date="2026-11-15")
        self.assertEqual(response.status_code, 302)
        page = self.voucher_number_of(response.headers["Location"])
        self.assertIn("CPV/2026/00001", page)
        self.assertIn("Cheque", page)
        self.assertIn("80123456", page)
        self.assertIn("Meezan Bank", page)
        self.assertIn("Bank Receipts", page)

    def test_the_numbers_go_up_and_start_again_next_year(self):
        self.save_receipt(payment_method="Cash")
        self.save_receipt(payment_method="Cash")
        second_year = self.save_receipt(payment_method="Cash", receipt_date="2027-01-05")
        page = self.voucher_number_of(second_year.headers["Location"])
        self.assertIn("CPV/2027/00001", page)

        listed = self.client.get("/customers/receipts").get_data(as_text=True)
        self.assertIn("CPV/2026/00002", listed)
        self.assertIn("CPV/2026/00001", listed)

    def test_a_cheque_needs_its_number_and_date(self):
        response = self.save_receipt(payment_method="Cheque")
        self.assertEqual(response.status_code, 200)
        self.assertIn("needs its number", response.get_data(as_text=True))
        # and nothing was saved
        conn, cursor = self.raw()
        cursor.execute("SELECT COUNT(*) FROM customer_receipts WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.close()
        conn.close()

    def test_a_receipt_saved_by_the_old_screen_still_works(self):
        response = self.save_receipt()          # no Payment Type, no method, no journal
        self.assertEqual(response.status_code, 302)
        page = self.voucher_number_of(response.headers["Location"])
        self.assertIn("CPV/2026/00001", page)
        self.assertIn("Customer Payment", page)
        self.assertIn("Cash", page)

    def test_the_cheques_tab_shows_only_cheques(self):
        self.save_receipt(payment_method="Cheque", cheque_no="1", cheque_date="2026-11-15")
        self.save_receipt(payment_method="Cash")
        cheques = self.client.get("/customers/receipts?tab=cheques").get_data(as_text=True)
        self.assertIn("CPV/2026/00001", cheques)
        self.assertNotIn("CPV/2026/00002", cheques)
        self.assertIn("Cheques", cheques)

    def test_another_company_cannot_open_this_receipt(self):
        response = self.save_receipt(payment_method="Cash")
        receipt_url = response.headers["Location"]

        conn, cursor = self.raw()
        cursor.execute(
            "INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
            ("TEST_RECEIPTS_OTHER_" + uuid.uuid4().hex[:8],))
        other_org_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        try:
            with self.client.session_transaction() as flask_session:
                flask_session["organization_id"] = other_org_id
            self.assertEqual(self.client.get(receipt_url).status_code, 404)
        finally:
            conn, cursor = self.raw()
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (other_org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (other_org_id,))
            conn.commit()
            cursor.close()
            conn.close()


class ContactTypeTests(_OneCompanyPerTest):

    def test_a_contact_keeps_its_type(self):
        response = self.client.post(f"/customers/{self.customer_id}/contacts", data={
            "name": "Hafizabad godown", "address_type": "Delivery Address",
            "street": "Band road", "city": "Hafizabad", "csrf_token": "test-csrf",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Delivery Address", page)
        self.assertIn("Hafizabad godown", page)

        conn, cursor = self.raw()
        cursor.execute("SELECT address_type FROM customer_contacts WHERE customer_id = %s", (self.customer_id,))
        self.assertEqual(cursor.fetchone()[0], "Delivery Address")
        cursor.close()
        conn.close()


class PurchasesFigureTests(_OneCompanyPerTest):

    def make_vendor_with_a_bill(self, total=150000):
        conn, cursor = self.raw()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Gulf Agro (Vendor)') "
                       "RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO vendor_bills (organization_id, vendor_id, bill_date, status)
            VALUES (%s, %s, '2026-11-01', 'Posted') RETURNING vendor_bill_id
        """, (self.org_id, vendor_id))
        bill_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendor_bill_lines (vendor_bill_id, account_id, total) VALUES (%s, %s, %s)",
                       (bill_id, self.deposit_account_id, total))
        conn.commit()
        cursor.close()
        conn.close()
        return vendor_id

    def test_the_card_counts_what_we_buy_from_a_linked_vendor(self):
        vendor_id = self.make_vendor_with_a_bill()

        before = self.client.get(f"/customers/{self.customer_id}/edit").get_data(as_text=True)
        self.assertIn("Purchases", before)
        self.assertIn("Link a vendor on the Sales", before)     # nothing linked yet

        linked = self.client.post(f"/customers/{self.customer_id}/vendor",
                                  data={"vendor_id": vendor_id, "csrf_token": "test-csrf"},
                                  follow_redirects=True)
        page = linked.get_data(as_text=True)
        self.assertIn("150,000.00", page)
        self.assertIn("1 bill from Gulf Agro (Vendor)", page)

        unlinked = self.client.post(f"/customers/{self.customer_id}/vendor",
                                    data={"vendor_id": "", "csrf_token": "test-csrf"},
                                    follow_redirects=True)
        self.assertIn("Link a vendor on the Sales", unlinked.get_data(as_text=True))

    def test_a_vendor_of_another_company_cannot_be_linked(self):
        conn, cursor = self.raw()
        cursor.execute(
            "INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
            ("TEST_RECEIPTS_OTHER_" + uuid.uuid4().hex[:8],))
        other_org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Their Vendor') RETURNING vendor_id",
                       (other_org_id,))
        their_vendor = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        try:
            response = self.client.post(f"/customers/{self.customer_id}/vendor",
                                        data={"vendor_id": their_vendor, "csrf_token": "test-csrf"})
            self.assertEqual(response.status_code, 400)
        finally:
            conn, cursor = self.raw()
            cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (other_org_id,))
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (other_org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (other_org_id,))
            conn.commit()
            cursor.close()
            conn.close()


if __name__ == "__main__":
    unittest.main()
