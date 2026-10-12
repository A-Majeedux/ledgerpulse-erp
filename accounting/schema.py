"""
accounting/schema.py

The foundation every other segment posts into: the Chart of Accounts, and
Journal Entries (headers + lines) as the single source of truth for every
financial transaction in the system - whether created directly here or
generated behind the scenes by Sales, Vendors, Assets, etc. once those
segments exist.
"""

ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Revenue", "Expense"]


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chart_of_accounts (
            account_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            code VARCHAR(20) NOT NULL,
            name VARCHAR(150) NOT NULL,
            account_type VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (organization_id, code)
        )
    """)
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS allow_reconciliation BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS tags VARCHAR(255)")
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_headers (
            journal_header_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            accounting_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS reference VARCHAR(100)")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100) DEFAULT 'Miscellaneous Operations'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'Manual'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS source_id INTEGER")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS posted_at TIMESTAMP")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            journal_line_id SERIAL PRIMARY KEY,
            journal_header_id INTEGER NOT NULL REFERENCES journal_headers(journal_header_id),
            account_id INTEGER NOT NULL REFERENCES chart_of_accounts(account_id),
            debit NUMERIC(14, 2) NOT NULL DEFAULT 0,
            credit NUMERIC(14, 2) NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS partner_name VARCHAR(150)")
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS label VARCHAR(255)")
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS line_order INTEGER NOT NULL DEFAULT 0")

    # A blank Chart of Accounts is useless, so seed a minimal starter set
    # per organization the first time - just enough to record something.
    # This never overwrites accounts a user already created/edited.
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        cursor.execute(
            "SELECT COUNT(*) FROM chart_of_accounts WHERE organization_id = %s",
            (org_id,)
        )
        if cursor.fetchone()[0] == 0:
            starter_accounts = [
                ("1000", "Cash in Hand", "Asset"),
                ("1010", "Bank Account", "Asset"),
                ("1100", "Trade Receivables", "Asset"),
                ("2000", "Trade Creditors", "Liability"),
                ("3000", "Owner's Equity", "Equity"),
                ("3100", "Retained Earnings", "Equity"),
                ("4000", "Sales Revenue", "Revenue"),
                ("5000", "Cost of Goods Sold", "Expense"),
                ("6000", "General Expenses", "Expense"),
            ]
            for code, name, account_type in starter_accounts:
                reconcilable = name in ("Trade Receivables", "Trade Creditors")
                cursor.execute("""
                    INSERT INTO chart_of_accounts
                        (organization_id, code, name, account_type, allow_reconciliation)
                    VALUES (%s, %s, %s, %s, %s)
                """, (org_id, code, name, account_type, reconcilable))
