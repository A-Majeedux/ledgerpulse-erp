"""
vendors/schema.py

Vendor master data, direct (non-PO) Vendor Bills, and Vendor Payments
(with WHT). The full RFQ->PO->Goods Receipt chain is deferred until
Inventory exists underneath it (see build brief) - this covers the
Vendors-module spend that doesn't need a Purchase Order at all:
recurring bills, subscriptions, maintenance, services.
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
    # Defensive ADD COLUMN for everything beyond the bare minimum - safe
    # to re-run even if this table already existed from an earlier,
    # less complete version of this schema.
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS nature_of_purchase VARCHAR(20) NOT NULL DEFAULT 'Supplies'")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS ntn VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS strn VARCHAR(30)")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS address TEXT")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS credit_limit NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bills (
            vendor_bill_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id),
            bill_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS due_date DATE")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100) DEFAULT 'Expenses'")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")

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
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS memo VARCHAR(255)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS paid_from_account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20) DEFAULT 'Manual'")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS cheque_number VARCHAR(50)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS cheque_date DATE")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS has_wht BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS wht_rate NUMERIC(5, 2)")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS wht_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE vendor_payments ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_payment_cheque
        ON vendor_payments (paid_from_account_id, cheque_number)
        WHERE cheque_number IS NOT NULL AND cheque_number != ''
    """)

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
