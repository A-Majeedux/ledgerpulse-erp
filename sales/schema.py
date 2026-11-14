"""
sales/schema.py

Sales module master data, direct Sales Invoices, and line items.
Includes:
  - products: Lightweight product catalog (Name, Unit, default Revenue Account)
  - sales_tax_rates: Separate Sales Tax list (independent of WHT rates per Abdul's rule)
  - sales_invoices: Header for customer sales invoices
  - sales_invoice_lines: Line items capturing quantity, rate, discount %, sales tax, and line total
  - Account 2200: "Output Tax Payable" auto-seeded per organization
"""

UOM_CHOICES = ["Units", "Kg", "Gm", "Ltr", "ML", "Bag", "Carton", "Box"]


def ensure_schema(cursor):
    # -----------------------------------------------------------------
    # 1. Product Master
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            product_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            name VARCHAR(150) NOT NULL,
            unit VARCHAR(20) NOT NULL DEFAULT 'Units',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS default_account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    # -----------------------------------------------------------------
    # 2. Sales Tax Rates Master (separate from WHT tax_rates)
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_tax_rates (
            sales_tax_rate_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            label VARCHAR(100) NOT NULL,
            rate_percent NUMERIC(5, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE sales_tax_rates ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    # Seed common Pakistan Sales Tax rates if empty
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        cursor.execute("SELECT COUNT(*) FROM sales_tax_rates WHERE organization_id = %s", (org_id,))
        if cursor.fetchone()[0] == 0:
            for label, rate in [
                ("Sales Tax 18%", 18.0),
                ("Sales Tax 15%", 15.0),
                ("Reduced Rate 10%", 10.0),
                ("Zero Rated 0%", 0.0),
                ("Exempt", 0.0),
            ]:
                cursor.execute("""
                    INSERT INTO sales_tax_rates (organization_id, label, rate_percent)
                    VALUES (%s, %s, %s)
                """, (org_id, label, rate))

    # -----------------------------------------------------------------
    # 3. Sales Invoices
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_invoices (
            sales_invoice_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            invoice_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS due_date DATE")
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(50)")
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100) DEFAULT 'Customer Invoices'")
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS journal_header_id INTEGER REFERENCES journal_headers(journal_header_id)")

    # -----------------------------------------------------------------
    # 4. Sales Invoice Lines
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_invoice_lines (
            sales_invoice_line_id SERIAL PRIMARY KEY,
            sales_invoice_id INTEGER NOT NULL REFERENCES sales_invoices(sales_invoice_id),
            line_order INTEGER NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS product_id INTEGER REFERENCES products(product_id)")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS label VARCHAR(255)")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS unit VARCHAR(20) DEFAULT 'Units'")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS quantity NUMERIC(14, 2) DEFAULT 1")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS rate NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS discount_percent NUMERIC(5, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS sales_tax_rate_id INTEGER REFERENCES sales_tax_rates(sales_tax_rate_id)")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(14, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE sales_invoice_lines ADD COLUMN IF NOT EXISTS total NUMERIC(14, 2) NOT NULL DEFAULT 0")

    # -----------------------------------------------------------------
    # 5. Output Tax Payable Account
    # -----------------------------------------------------------------
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        cursor.execute(
            "SELECT 1 FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
            (org_id, "2200")
        )
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO chart_of_accounts
                    (organization_id, code, name, account_type, allow_reconciliation)
                VALUES (%s, '2200', 'Output Tax Payable', 'Liability', FALSE)
            """, (org_id,))