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


def seed_organization(cursor, organization_id):
    """
    Make sure ONE organization has the two WHT Receivable accounts - the
    asset-side mirror of the WHT Payable accounts seeded in
    vendors/schema.py. Adds only what is missing. Called at startup for
    every organization, and again the moment a new organization is created
    (core.db.seed_new_organization).
    """
    for code, name in [
        ("1150", "WHT Receivable - Supplies"),
        ("1160", "WHT Receivable - Services"),
    ]:
        cursor.execute(
            "SELECT 1 FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
            (organization_id, code)
        )
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO chart_of_accounts
                    (organization_id, code, name, account_type, allow_reconciliation)
                VALUES (%s, %s, %s, 'Asset', FALSE)
            """, (organization_id, code, name))


def _give_every_customer_an_id(cursor):
    """
    A customer's ID is a running number inside its organization: 1, 2, 3...
    New customers get theirs when they are saved (customers/services.py).

    This gives one to every customer that has none yet - the customers that
    existed before the ID did - continuing after the organization's highest
    ID, oldest customer first. A customer that already has an ID keeps it,
    so running this again changes nothing.
    """
    cursor.execute("""
        UPDATE customers
        SET customer_no = numbered.new_no
        FROM (
            SELECT waiting.customer_id,
                   COALESCE((SELECT MAX(taken.customer_no) FROM customers taken
                             WHERE taken.organization_id = waiting.organization_id), 0)
                   + ROW_NUMBER() OVER (PARTITION BY waiting.organization_id ORDER BY waiting.customer_id) AS new_no
            FROM customers waiting
            WHERE waiting.customer_no IS NULL
        ) AS numbered
        WHERE customers.customer_id = numbered.customer_id
    """)


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

    # The customer card: ID, Person / Company, contact details, the address
    # in parts (the existing "address" column is the Street line), Region,
    # Territory, the two categories and the licence. Every statement only
    # ADDS a column - nothing existing is changed, renamed or removed - and
    # does nothing at all on a database that already has the column.
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_no INTEGER")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_company BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS email VARCHAR(150)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone VARCHAR(50)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS street2 VARCHAR(200)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS city VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS country VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS region VARCHAR(50)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS territory VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS category_parent VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS category VARCHAR(100)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS lic_number VARCHAR(50)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS lic_expiry_date DATE")
    _give_every_customer_an_id(cursor)
    # One ID is never held by two customers of the same organization.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS customers_id_per_organization
        ON customers (organization_id, customer_no)
    """)

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
        seed_organization(cursor, org_id)
