"""
vendors/schema.py

Vendor master data, direct (non-PO) Vendor Bills, Vendor Credit Notes,
Vendor Payments (with WHT & allocation), and Batch Vendor Payments.
"""


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            vendor_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            name VARCHAR(150) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS nature_of_purchase VARCHAR(20) NOT NULL DEFAULT 'Supplies'")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS ntn VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS strn VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS address TEXT")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS credit_limit NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS is_company BOOLEAN DEFAULT TRUE")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS email VARCHAR(150)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS phone VARCHAR(50)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS city VARCHAR(100)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS postal_code VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS country VARCHAR(100) DEFAULT 'Pakistan'")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS cnic VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS website VARCHAR(150)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS category VARCHAR(100)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS tags VARCHAR(255)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS region VARCHAR(100)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS territory VARCHAR(100)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS is_customer BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(50)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS default_payment_method VARCHAR(50) DEFAULT 'Manual'")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS notes TEXT")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS contact_person VARCHAR(150)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS logo_path VARCHAR(255)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS account_type VARCHAR(50) DEFAULT 'Trade Creditors'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bills (
            vendor_bill_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id),
            bill_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS bill_number VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS due_date DATE")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS receipt_date DATE")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS accounting_date DATE")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS recipient_bank VARCHAR(100)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS lc_reference VARCHAR(100)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100) DEFAULT 'Expenses'")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS amount_due NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS notes TEXT")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS purchase_journal VARCHAR(50) DEFAULT 'D-PUR'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bill_lines (
            vendor_bill_line_id SERIAL PRIMARY KEY,
            vendor_bill_id INTEGER NOT NULL REFERENCES vendor_bills(vendor_bill_id),
            account_id INTEGER NOT NULL REFERENCES chart_of_accounts(account_id),
            total NUMERIC(14, 2) NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE vendor_bill_lines ADD COLUMN IF NOT EXISTS label VARCHAR(255)")
    cursor.execute("ALTER TABLE vendor_bill_lines ADD COLUMN IF NOT EXISTS quantity NUMERIC(14, 2) DEFAULT 1")
    cursor.execute("ALTER TABLE vendor_bill_lines ADD COLUMN IF NOT EXISTS rate NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_bill_lines ADD COLUMN IF NOT EXISTS line_order INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_bill_lines ADD COLUMN IF NOT EXISTS discount_pct NUMERIC(5, 2) DEFAULT 0")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_voucher_counters (
            organization_id INTEGER REFERENCES organizations(organization_id),
            series VARCHAR(20),
            next_number INTEGER DEFAULT 1,
            PRIMARY KEY (organization_id, series)
        )
    """)

    # Vendor Credit Notes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_credit_notes (
            vendor_credit_note_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id),
            original_bill_id INTEGER REFERENCES vendor_bills(vendor_bill_id),
            credit_note_number VARCHAR(50),
            credit_date DATE NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Draft',
            journal_header_id INTEGER REFERENCES journal_headers(journal_header_id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_credit_note_lines (
            vendor_credit_note_line_id SERIAL PRIMARY KEY,
            vendor_credit_note_id INTEGER NOT NULL REFERENCES vendor_credit_notes(vendor_credit_note_id),
            account_id INTEGER NOT NULL REFERENCES chart_of_accounts(account_id),
            label VARCHAR(255),
            quantity NUMERIC(14, 2) DEFAULT 1,
            rate NUMERIC(14, 2) DEFAULT 0,
            total NUMERIC(14, 2) NOT NULL DEFAULT 0,
            line_order INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Vendor Payments
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_payments (
            vendor_payment_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id),
            amount NUMERIC(14, 2) NOT NULL,
            payment_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS voucher_number VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS memo VARCHAR(255)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS paid_from_account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20) DEFAULT 'Manual'")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS cheque_number VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS cheque_date DATE")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS lc_reference VARCHAR(100)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS vendor_bank_account VARCHAR(100)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS has_wht BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS wht_rate NUMERIC(5, 2)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS wht_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_payment_cheque
        ON vendor_payments (paid_from_account_id, cheque_number)
        WHERE cheque_number IS NOT NULL AND cheque_number != ''
    """)

    # Payment Allocations against Bills
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_payment_allocations (
            allocation_id SERIAL PRIMARY KEY,
            vendor_payment_id INTEGER NOT NULL REFERENCES vendor_payments(vendor_payment_id) ON DELETE CASCADE,
            vendor_bill_id INTEGER NOT NULL REFERENCES vendor_bills(vendor_bill_id) ON DELETE CASCADE,
            allocated_amount NUMERIC(14, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Batch Vendor Payments
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS batch_vendor_payments (
            batch_payment_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            batch_number VARCHAR(50),
            payment_date DATE NOT NULL,
            paid_from_account_id INTEGER REFERENCES chart_of_accounts(account_id),
            payment_method VARCHAR(20) DEFAULT 'Manual',
            total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
            status VARCHAR(20) DEFAULT 'Draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS batch_vendor_payment_lines (
            batch_line_id SERIAL PRIMARY KEY,
            batch_payment_id INTEGER NOT NULL REFERENCES batch_vendor_payments(batch_payment_id) ON DELETE CASCADE,
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id),
            amount NUMERIC(14, 2) NOT NULL,
            memo VARCHAR(255),
            vendor_payment_id INTEGER REFERENCES vendor_payments(vendor_payment_id)
        )
    """)

    # Ensure WHT Payable Accounts
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        for code, name in [
            ("2100", "WHT Payable - Supplies"),
            ("2110", "WHT Payable - Services"),
        ]:
            cursor.execute(
                "SELECT 1 FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
                (org_id, code)
            )
            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO chart_of_accounts
                        (organization_id, code, name, account_type, allow_reconciliation)
                    VALUES (%s, %s, %s, 'Liability', FALSE)
                """, (org_id, code, name))
