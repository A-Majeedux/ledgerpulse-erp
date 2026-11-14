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

    # The customer card (14 Nov 2026): ID, Person / Company, contact details,
    # the address in parts (the existing "address" column is the Street line),
    # Region, Territory, the two categories and the licence.
    #
    # Every statement only ADDS a column - nothing existing is changed,
    # renamed or removed - and does nothing at all on a database that already
    # has the column. The ID is then given to the customers that were saved
    # before it existed, and a unique index makes sure one ID is never held by
    # two customers of the same organization.
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

    # The rest of the card (14 Nov 2026): the tag words, the "also a vendor"
    # mark, the currency he deals in, his website, and the people/addresses
    # kept under him (the contacts table below). Additions only, as above.
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS tags VARCHAR(255)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_vendor BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS currency VARCHAR(10)")
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS website VARCHAR(255)")
    # The Notes tab of the card. TEXT, so the length limit is the app's
    # (NOTES_MAX_LENGTH), not the column's.
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT")

    _give_every_customer_an_id(cursor)
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
    # The boxes his own receipt screen has above the amount: which kind of
    # payment this is, which journal it was written in, the voucher number the
    # screen shows, how the money came in, and - for a cheque - the cheque's
    # own details (the "Cheques" tab). All of them optional, so a receipt
    # saved before this version still opens exactly as it did.
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS payment_type VARCHAR(50) DEFAULT 'Customer Payment'")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS voucher_no VARCHAR(30)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS payment_method VARCHAR(30) DEFAULT 'Cash'")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS cheque_no VARCHAR(50)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS cheque_bank VARCHAR(150)")
    cursor.execute("ALTER TABLE customer_receipts ADD COLUMN IF NOT EXISTS cheque_date DATE")
    # One voucher number per company, never twice (customers/services.py).
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS customer_receipts_voucher_no_key
        ON customer_receipts (organization_id, voucher_no)
        WHERE voucher_no IS NOT NULL
    """)
    # A voucher number for the receipts made before numbering existed - once,
    # oldest first (customers/services.py).
    from customers.services import number_missing_receipts
    number_missing_receipts(cursor)

    # The people and addresses kept under a customer - the "Contacts &
    # Addresses" tab of his own customer form. One customer may have many;
    # at most one of them is the primary one (customers/services.py keeps
    # that rule). The table only ever grows: nothing here is dropped.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_contacts (
            contact_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS name VARCHAR(150)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS job_position VARCHAR(100)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS email VARCHAR(150)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS phone VARCHAR(50)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS street TEXT")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS street2 VARCHAR(200)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS city VARCHAR(100)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS country VARCHAR(100)")
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS is_primary BOOLEAN DEFAULT FALSE")
    # What a contact IS: the address an invoice goes to, the address goods are
    # delivered to, or just another person/place kept on file. His own contact
    # card has the same three answers.
    cursor.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS address_type VARCHAR(30) DEFAULT 'Other'")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS customer_contacts_of_customer
        ON customer_contacts (organization_id, customer_id)
    """)

    # The vendor behind this customer, when he is both: a customer bought
    # from, and a vendor we buy from. Only the Purchases figure of the card
    # reads it, and it is empty on every customer that is not linked, so
    # nothing about an unlinked customer changes. No foreign key: the vendors
    # table is made by another part of the program and may not exist yet when
    # this runs.
    cursor.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS vendor_id INTEGER")

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
