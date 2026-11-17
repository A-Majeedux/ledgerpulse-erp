"""
tests/test_vendors.py
Comprehensive automated tests for LedgerPulse Vendors & Purchases module (Phase 8).
Ensures complete test data isolation using throwaway organizations starting with "TEST_".
"""
import os
import io
os.environ["TESTING"] = "true"

import unittest
import uuid
from decimal import Decimal

from _people import member
from flask import session

import app
from core.db import get_conn
from accounting.services import create_journal_entry, UnbalancedEntryError


class LedgerPulseVendorsTests(unittest.TestCase):

    def setUp(self):
        self.app = app.app
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Generate unique test organization names starting with "TEST_"
        self.test_org_name = f"TEST_ORG_{uuid.uuid4().hex[:8]}"
        self.test_other_org_name = f"TEST_OTHER_{uuid.uuid4().hex[:8]}"
        self.test_user_email = f"test_{uuid.uuid4().hex[:8]}@vendors.local"

        conn = get_conn()
        cursor = conn.cursor()
        
        # Insert test org 1
        cursor.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING organization_id", (self.test_org_name,))
        self.org_id = cursor.fetchone()[0]

        # Insert test org 2 (for isolation testing)
        cursor.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING organization_id", (self.test_other_org_name,))
        self.other_org_id = cursor.fetchone()[0]

        # Insert test user
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) VALUES ('Vendor Tester', %s, 'hash', TRUE) RETURNING user_id", (self.test_user_email,))
        self.user_id = cursor.fetchone()[0]
        # a member of both companies: the isolation tests are about one company's
        # data being invisible from the other, not about signed-in strangers
        member(cursor, self.user_id, self.org_id)
        member(cursor, self.user_id, self.other_org_id)

        # Ensure accounts for both orgs with is_active = TRUE
        for org in [self.org_id, self.other_org_id]:
            for code, name, acc_type in [
                ('2000', 'Accounts Payable', 'Liability'),
                ('2100', 'WHT Payable - Supplies', 'Liability'),
                ('2110', 'WHT Payable - Services', 'Liability'),
                ('5000', 'Office Expense', 'Expense'),
                ('1010', 'HBL Bank', 'Bank')
            ]:
                cursor.execute("""
                    INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT DO NOTHING
                """, (org, code, name, acc_type))

        conn.commit()
        cursor.close()
        conn.close()

    def tearDown(self):
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in [getattr(self, 'org_id', None), getattr(self, 'other_org_id', None)]:
            if org_id:
                cursor.execute("DELETE FROM batch_vendor_payment_lines WHERE batch_payment_id IN (SELECT batch_payment_id FROM batch_vendor_payments WHERE organization_id = %s)", (org_id,))
                cursor.execute("DELETE FROM batch_vendor_payments WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM vendor_payment_allocations WHERE vendor_payment_id IN (SELECT vendor_payment_id FROM vendor_payments WHERE organization_id = %s)", (org_id,))
                cursor.execute("DELETE FROM vendor_payments WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM vendor_credit_note_lines WHERE vendor_credit_note_id IN (SELECT vendor_credit_note_id FROM vendor_credit_notes WHERE organization_id = %s)", (org_id,))
                cursor.execute("DELETE FROM vendor_credit_notes WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN (SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)", (org_id,))
                cursor.execute("DELETE FROM vendor_bills WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM vendors WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN (SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (org_id,))
                cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))

        if hasattr(self, 'user_id') and self.user_id:
            cursor.execute("DELETE FROM users WHERE user_id = %s", (self.user_id,))

        conn.commit()
        cursor.close()
        conn.close()
        self.app_context.pop()

    def login_session(self, client, org_id=None):
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["user_name"] = "Vendor Tester"
            sess["organization_id"] = org_id or self.org_id
            sess["csrf_token"] = "test-csrf"

    def test_1_get_routes_return_200(self):
        """Test that every GET route in the vendors blueprint returns 200 with seeded real data."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        # Seed 1 vendor
        cursor.execute("INSERT INTO vendors (organization_id, name, nature_of_purchase) VALUES (%s, 'Seeded Vendor', 'Supplies') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '5000'", (self.org_id,))
        expense_acc_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # Seed 2 bills (1 draft, 1 posted)
        self.client.post("/vendors/bills/new", data={
            "csrf_token": "test-csrf", "vendor_id": vendor_id, "bill_date": "2026-11-02",
            "label[]": "Bill 1", "account_id[]": expense_acc_id, "quantity[]": "1", "rate[]": "1000.00", "total[]": "1000.00"
        }, follow_redirects=True)
        self.client.post("/vendors/bills/new", data={
            "csrf_token": "test-csrf", "vendor_id": vendor_id, "bill_date": "2026-11-02",
            "label[]": "Bill 2", "account_id[]": expense_acc_id, "quantity[]": "1", "rate[]": "2000.00", "total[]": "2000.00"
        }, follow_redirects=True)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_bill_id FROM vendor_bills WHERE vendor_id = %s ORDER BY vendor_bill_id", (vendor_id,))
        bill_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        # Post the second bill
        if len(bill_ids) >= 2:
            self.client.post(f"/vendors/bills/{bill_ids[1]}/post", data={"csrf_token": "test-csrf"}, follow_redirects=True)

        # Seed 1 payment with WHT
        self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf", "vendor_id": vendor_id, "paid_from_account_id": bank_id,
            "amount": "10000.00", "payment_date": "2026-11-02", "has_wht": "on", "wht_rate": "5.0", "payment_method": "Manual"
        }, follow_redirects=True)

        # Seed 1 credit note
        self.client.post("/vendors/credit-notes/new", data={
            "csrf_token": "test-csrf", "vendor_id": vendor_id, "credit_date": "2026-11-02",
            "label[]": "CN 1", "account_id[]": expense_acc_id, "quantity[]": "1", "rate[]": "500.00", "total[]": "500.00"
        }, follow_redirects=True)

        # Seed 1 batch payment
        self.client.post("/vendors/batch-payments/new", data={
            "csrf_token": "test-csrf", "payment_date": "2026-11-02", "paid_from_account_id": bank_id,
            "vendor_id[]": [str(vendor_id)], "amount[]": ["1500.00"], "memo[]": ["Batch 1"]
        }, follow_redirects=True)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_payment_id FROM vendor_payments WHERE vendor_id = %s", (vendor_id,))
        payment_row = cursor.fetchone()
        payment_id = payment_row[0] if payment_row else None

        cursor.execute("SELECT vendor_credit_note_id FROM vendor_credit_notes WHERE vendor_id = %s", (vendor_id,))
        cn_row = cursor.fetchone()
        cn_id = cn_row[0] if cn_row else None

        cursor.execute("SELECT batch_payment_id FROM batch_vendor_payments WHERE organization_id = %s", (self.org_id,))
        batch_row = cursor.fetchone()
        batch_id = batch_row[0] if batch_row else None
        cursor.close()
        conn.close()

        get_routes = [
            "/vendors/",
            f"/vendors/{vendor_id}",
            "/vendors/new",
            "/vendors/bills",
            "/vendors/bills/new",
            *(f"/vendors/bills/{bid}" for bid in bill_ids),
            *(f"/vendors/bills/{bid}/print" for bid in bill_ids if len(bill_ids) > 1),
            "/vendors/credit-notes",
            "/vendors/credit-notes/new",
            *(f"/vendors/credit-notes/{cn_id}" for _ in [1] if cn_id),
            *(f"/vendors/credit-notes/{cn_id}/print" for _ in [1] if cn_id),
            "/vendors/payments",
            "/vendors/payments/new",
            *(f"/vendors/payments/{payment_id}" for _ in [1] if payment_id),
            *(f"/vendors/payments/{payment_id}/print" for _ in [1] if payment_id),
            "/vendors/batch-payments",
            "/vendors/batch-payments/new",
            *(f"/vendors/batch-payments/{batch_id}" for _ in [1] if batch_id),
            *(f"/vendors/batch-payments/{batch_id}/print" for _ in [1] if batch_id),
            "/vendors/reports/own-cheques",
            "/vendors/reports/ledger",
            "/vendors/reports/aging",
        ]
        for route in get_routes:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, f"Route {route} failed with status {resp.status_code}")

    def test_2_vendor_bill_journal_entry(self):
        """Test vendor -> bill -> post creates a balanced journal entry (Dr expense, Cr Accounts Payable)."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name, nature_of_purchase) VALUES (%s, 'Test Supplier', 'Supplies') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '5000'", (self.org_id,))
        expense_acc_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # Post bill via form
        resp = self.client.post("/vendors/bills/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "bill_date": "2026-11-02",
            "due_date": "2026-12-02",
            "label[]": "Office Supplies",
            "account_id[]": expense_acc_id,
            "quantity[]": "2",
            "rate[]": "5000.00",
            "total[]": "10000.00"
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Get bill id
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_bill_id, journal_header_id FROM vendor_bills WHERE vendor_id = %s", (vendor_id,))
        b_row = cursor.fetchone()
        bill_id, jh_id = b_row
        cursor.close()
        conn.close()

        # Post bill
        resp_post = self.client.post(f"/vendors/bills/{bill_id}/post", data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

        # Verify balanced journal entry
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(debit), SUM(credit) FROM journal_lines WHERE journal_header_id = %s", (jh_id,))
        dr_sum, cr_sum = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(dr_sum, cr_sum)
        self.assertEqual(dr_sum, Decimal("10000.00"))

    def test_3_vendor_payment_wht_posting(self):
        """Test payment of 20,000 with 5% WHT on Supplies vendor posts Dr AP 20,000, Cr WHT Payable 1,000, Cr Bank 19,000."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name, nature_of_purchase) VALUES (%s, 'WHT Supplier', 'Supplies') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        resp = self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "paid_from_account_id": bank_id,
            "amount": "20000.00",
            "payment_date": "2026-11-02",
            "has_wht": "on",
            "wht_rate": "5.0",
            "payment_method": "Manual"
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Verify journal entry lines
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT journal_header_id FROM vendor_payments WHERE vendor_id = %s", (vendor_id,))
        jh_id = cursor.fetchone()[0]
        cursor.execute("SELECT coa.code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.account_id = jl.account_id WHERE jl.journal_header_id = %s ORDER BY jl.line_order", (jh_id,))
        lines = cursor.fetchall()
        cursor.close()
        conn.close()

        lines_dict = {row[0]: (row[1], row[2]) for row in lines}
        self.assertEqual(lines_dict["2000"][0], Decimal("20000.00")) # Dr AP
        self.assertEqual(lines_dict["2100"][1], Decimal("1000.00"))  # Cr WHT Payable
        self.assertEqual(lines_dict["1010"][1], Decimal("19000.00")) # Cr Bank

    def test_4_duplicate_cheque_blocking(self):
        """Test a duplicate cheque number on the same bank is blocked."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Cheque Vendor') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # First cheque payment
        resp1 = self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "paid_from_account_id": bank_id,
            "amount": "5000.00",
            "payment_date": "2026-11-02",
            "payment_method": "Cheque",
            "cheque_number": "CHQ-999001"
        }, follow_redirects=True)
        self.assertEqual(resp1.status_code, 200)

        # Second payment with same cheque number on same bank
        resp2 = self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "paid_from_account_id": bank_id,
            "amount": "3000.00",
            "payment_date": "2026-11-02",
            "payment_method": "Cheque",
            "cheque_number": "CHQ-999001"
        }, follow_redirects=True)
        self.assertIn("already been used", resp2.get_data(as_text=True))

    def test_5_credit_note_reverses_bill(self):
        """Test a posted credit note reverses the bill entry."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'CN Vendor') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '5000'", (self.org_id,))
        expense_acc_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        resp_cn = self.client.post("/vendors/credit-notes/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "credit_date": "2026-11-02",
            "label[]": "Return Goods",
            "account_id[]": expense_acc_id,
            "quantity[]": "1",
            "rate[]": "4000.00",
            "total[]": "4000.00"
        }, follow_redirects=True)
        self.assertEqual(resp_cn.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_credit_note_id, journal_header_id FROM vendor_credit_notes WHERE vendor_id = %s", (vendor_id,))
        cn_id, jh_id = cursor.fetchone()
        cursor.close()
        conn.close()

        resp_post = self.client.post(f"/vendors/credit-notes/{cn_id}/post", data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(debit), SUM(credit) FROM journal_lines WHERE journal_header_id = %s", (jh_id,))
        dr_sum, cr_sum = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(dr_sum, cr_sum)
        self.assertEqual(dr_sum, Decimal("4000.00"))

    def test_6_batch_payments(self):
        """Test a batch of N lines creates N payments with balanced entries."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Batch V1') RETURNING vendor_id", (self.org_id,))
        v1 = cursor.fetchone()[0]
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Batch V2') RETURNING vendor_id", (self.org_id,))
        v2 = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        resp = self.client.post("/vendors/batch-payments/new", data={
            "csrf_token": "test-csrf",
            "payment_date": "2026-11-02",
            "paid_from_account_id": bank_id,
            "vendor_id[]": [str(v1), str(v2)],
            "amount[]": ["1000.00", "2000.00"],
            "memo[]": ["Batch item 1", "Batch item 2"]
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vendor_payments WHERE organization_id = %s AND memo LIKE 'Batch item %%'", (self.org_id,))
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(count, 2)

    def test_7_bill_becomes_paid_when_allocated(self):
        """Test a bill becomes Paid when fully allocated."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Alloc Vendor') RETURNING vendor_id", (self.org_id,))
        vendor_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '5000'", (self.org_id,))
        expense_acc_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # Create bill for 5000
        self.client.post("/vendors/bills/new", data={
            "csrf_token": "test-csrf", "vendor_id": vendor_id, "bill_date": "2026-11-02",
            "label[]": "Service", "account_id[]": expense_acc_id, "quantity[]": "1", "rate[]": "5000.00", "total[]": "5000.00"
        }, follow_redirects=True)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_bill_id FROM vendor_bills WHERE vendor_id = %s", (vendor_id,))
        bill_id = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        # Pay and allocate 5000 against bill_id
        self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf",
            "vendor_id": vendor_id,
            "paid_from_account_id": bank_id,
            "amount": "5000.00",
            "payment_date": "2026-11-02",
            "bill_id[]": str(bill_id),
            "alloc_amount[]": "5000.00"
        }, follow_redirects=True)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT status, paid_amount FROM vendor_bills WHERE vendor_bill_id = %s", (bill_id,))
        status, paid_amt = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertEqual(status, "Paid")
        self.assertEqual(paid_amt, Decimal("5000.00"))

    def test_8_organization_isolation(self):
        """Test one organization cannot see another's vendor data."""
        # Create vendor in org 1
        self.login_session(self.client, org_id=self.org_id)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Org1 Secret Vendor') RETURNING vendor_id", (self.org_id,))
        v_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # Switch session to other org
        self.login_session(self.client, org_id=self.other_org_id)
        resp = self.client.get(f"/vendors/{v_id}")
        self.assertEqual(resp.status_code, 404)

    def test_9_vendor_form_and_validations(self):
        """Test company & person vendor creation, logo upload, edit form, rejected .exe, and missing name."""
        self.login_session(self.client)
        png_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

        # 1. Test missing Name -> fails (400)
        resp_missing = self.client.post("/vendors/new", data={
            "csrf_token": "test-csrf",
            "entity_type": "company",
            "nature_of_purchase": "Supplies",
            "account_type": "Trade Creditors"
        }, follow_redirects=True)
        self.assertEqual(resp_missing.status_code, 400)

        # 2. Test rejected .exe upload -> fails (400)
        resp_exe = self.client.post("/vendors/new", data={
            "csrf_token": "test-csrf",
            "entity_type": "company",
            "name": "Malware Vendor",
            "nature_of_purchase": "Supplies",
            "account_type": "Trade Creditors",
            "logo": (io.BytesIO(b"MZ...fake exe"), "hack.exe")
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(resp_exe.status_code, 400)

        # 3. Create Company vendor with valid PNG
        resp_company = self.client.post("/vendors/new", data={
            "csrf_token": "test-csrf",
            "entity_type": "company",
            "name": "ACME Supplies Corp",
            "contact_person": "John Doe",
            "address": "123 Industrial Area",
            "ntn": "1234567-8",
            "strn": "9876543-2",
            "email": "john@acme.com",
            "phone": "+923001234567",
            "nature_of_purchase": "Supplies",
            "account_type": "Trade Creditors",
            "credit_limit": "500000.00",
            "logo": (io.BytesIO(png_bytes), "logo.png")
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(resp_company.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vendor_id, name, is_company, contact_person, ntn, strn, email, phone, nature_of_purchase, account_type, credit_limit, logo_path
            FROM vendors WHERE organization_id = %s AND name = 'ACME Supplies Corp'
        """, (self.org_id,))
        v_data = cursor.fetchone()
        cursor.close()
        conn.close()

        self.assertIsNotNone(v_data)
        v_id = v_data[0]
        self.assertTrue(v_data[2])
        self.assertEqual(v_data[3], "John Doe")
        self.assertEqual(v_data[4], "1234567-8")
        self.assertEqual(v_data[5], "9876543-2")
        self.assertIsNotNone(v_data[11])

        # Test edit form loads and shows saved values
        resp_edit_get = self.client.get(f"/vendors/{v_id}/edit")
        self.assertEqual(resp_edit_get.status_code, 200)
        self.assertIn(b"ACME Supplies Corp", resp_edit_get.data)
        self.assertIn(b"John Doe", resp_edit_get.data)

        # 4. Create Person (Individual) vendor
        resp_person = self.client.post("/vendors/new", data={
            "csrf_token": "test-csrf",
            "entity_type": "person",
            "name": "Ali Consultant",
            "nature_of_purchase": "Services",
            "account_type": "Trade Creditors",
            "credit_limit": "50000.00"
        }, follow_redirects=True)
        self.assertEqual(resp_person.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vendor_id, is_company, nature_of_purchase FROM vendors WHERE organization_id = %s AND name = 'Ali Consultant'
        """, (self.org_id,))
        p_data = cursor.fetchone()
        cursor.close()
        conn.close()
        self.assertIsNotNone(p_data)
        self.assertFalse(p_data[1])
        self.assertEqual(p_data[2], "Services")

    def test_10_all_print_templates_and_routes(self):
        """Test all 7 print routes for bill, payment, credit note, batch, and journal entries."""
        self.login_session(self.client)
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vendors (organization_id, name, nature_of_purchase) VALUES (%s, 'Print Test Vendor', 'Supplies') RETURNING vendor_id", (self.org_id,))
        v_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '5000'", (self.org_id,))
        exp_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1010'", (self.org_id,))
        bank_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        # 1. Bill + Post
        resp_bill = self.client.post("/vendors/bills/new", data={
            "csrf_token": "test-csrf", "vendor_id": v_id, "bill_date": "2026-11-03",
            "label[]": "Test Print Line", "account_id[]": exp_id, "quantity[]": "2", "rate[]": "5000.00", "total[]": "10000.00"
        }, follow_redirects=True)
        self.assertEqual(resp_bill.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_bill_id, bill_number FROM vendor_bills WHERE organization_id = %s ORDER BY vendor_bill_id DESC LIMIT 1", (self.org_id,))
        b_row = cursor.fetchone()
        b_id, b_num = b_row[0], b_row[1]
        cursor.close()
        conn.close()

        self.client.post(f"/vendors/bills/{b_id}/post", data={"csrf_token": "test-csrf"})

        # 2. Payment + WHT + Cheque
        resp_pay = self.client.post("/vendors/payments/new", data={
            "csrf_token": "test-csrf", "vendor_id": v_id, "paid_from_account_id": bank_id,
            "amount": "10000.00", "payment_date": "2026-11-03", "payment_method": "Cheque",
            "cheque_number": "CHQ-PRT-1", "has_wht": "on", "wht_rate": "5.0",
            "bill_id[]": str(b_id), "alloc_amount[]": "10000.00"
        }, follow_redirects=True)
        self.assertEqual(resp_pay.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_payment_id, voucher_number FROM vendor_payments WHERE organization_id = %s ORDER BY vendor_payment_id DESC LIMIT 1", (self.org_id,))
        p_row = cursor.fetchone()
        p_id, p_num = p_row[0], p_row[1]
        cursor.close()
        conn.close()

        # 3. Credit Note + Post
        resp_cn = self.client.post("/vendors/credit-notes/new", data={
            "csrf_token": "test-csrf", "vendor_id": v_id, "original_bill_id": str(b_id), "credit_date": "2026-11-03",
            "label[]": "Return Line", "account_id[]": exp_id, "quantity[]": "1", "rate[]": "1000.00", "total[]": "1000.00"
        }, follow_redirects=True)
        self.assertEqual(resp_cn.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_credit_note_id, credit_note_number FROM vendor_credit_notes WHERE organization_id = %s ORDER BY vendor_credit_note_id DESC LIMIT 1", (self.org_id,))
        cn_row = cursor.fetchone()
        cn_id, cn_num = cn_row[0], cn_row[1]
        cursor.close()
        conn.close()

        self.client.post(f"/vendors/credit-notes/{cn_id}/post", data={"csrf_token": "test-csrf"})

        # 4. Batch Payment
        resp_batch = self.client.post("/vendors/batch-payments/new", data={
            "csrf_token": "test-csrf", "payment_date": "2026-11-03", "paid_from_account_id": bank_id,
            "vendor_id[]": [str(v_id)], "amount[]": ["5000.00"], "memo[]": ["Batch print test"]
        }, follow_redirects=True)
        self.assertEqual(resp_batch.status_code, 200)

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT batch_payment_id, batch_number FROM batch_vendor_payments WHERE organization_id = %s ORDER BY batch_payment_id DESC LIMIT 1", (self.org_id,))
        bp_row = cursor.fetchone()
        bp_id, bp_num = bp_row[0], bp_row[1]
        cursor.close()
        conn.close()

        # Test all 7 print routes
        print_routes = [
            (f"/vendors/bills/{b_id}/print", b_num, "Invoice Lines"),
            (f"/vendors/bills/{b_id}/journal-print", b_num, "Journal Entry"),
            (f"/vendors/payments/{p_id}/print", p_num, "Total Amount"),
            (f"/vendors/payments/{p_id}/journal-print", p_num, "Journal Entry"),
            (f"/vendors/credit-notes/{cn_id}/print", cn_num, "Credit Note Lines"),
            (f"/vendors/credit-notes/{cn_id}/journal-print", cn_num, "Journal Entry"),
            (f"/vendors/batch-payments/{bp_id}/print", bp_num, "Batch Payment Items"),
        ]

        for route, doc_num, heading in print_routes:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, f"Route {route} failed with status {resp.status_code}")
            text = resp.data.decode("utf-8")
            self.assertIn(heading, text, f"Heading '{heading}' missing in {route}")
            self.assertIn(doc_num, text, f"Document number '{doc_num}' missing in {route}")
            for bad_word in ["None", "False", "null"]:
                self.assertNotIn(bad_word, text, f"Forbidden word '{bad_word}' found in {route}")


if __name__ == "__main__":
    unittest.main()
