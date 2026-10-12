"""
customers/schema.py

Customer master data and Customer Receipts (with WHT Receivable). This is
the customer-side mirror of the vendors segment: same WHT mechanics, same
single-window voucher pattern, just flipped direction (money coming IN,
and any tax the CUSTOMER withholds from what they owe us becomes a
recoverable asset - WHT Receivable - not a liability).

Receipts here are "on account" only for now (not applied against a
specific invoice) since Sales Invoices don't exist on the new
architecture yet - that link gets added when Sales is rebuilt.
"""


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            name VARCHAR(150) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Defensive ADD COLUMN for every column beyond the bare minimum above -
    # this protects against exactly what just happened: a customers table
    # left over from an earlier, less complete version of this schema.
    # ALTER TABLE ADD COLUMN IF NOT EXISTS is a no-op on a table that
    # already has the column, so this is always safe to re-run.
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS nature_of_supply VARCHAR(20) NOT NULL DEFAULT 'Supplies'")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ntn VARCHAR(30)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS strn VARCHAR(30)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS cnic VARCHAR(20)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS address TEXT")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS credit_limit NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_receipts (
            customer_receipt_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            amount NUMERIC(14, 2) NOT NULL,
            receipt_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS memo VARCHAR(255)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS deposit_to_account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS has_wht BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS wht_rate NUMERIC(5, 2)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS wht_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")

    # WHT Receivable accounts - the asset-side mirror of the WHT Payable
    # accounts seeded in vendors/schema.py.
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        for code, name in [
            ("1150", "WHT Receivable - Supplies"),
            ("1160", "WHT Receivable - Services"),
        ]:
            cursor.execute(
                "SELECT 1 FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
                (org_id, code)
            )
            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO chart_of_accounts
                        (organization_id, code, name, account_type, allow_reconciliation)
                    VALUES (%s, %s, %s, 'Asset', FALSE)
                """, (org_id, code, name))
