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


def _target_company(cursor):
    """The company that products and tax rates without one should belong to.

    ZYRO's own workspace ("ZYRO Head Office") is never chosen while a real
    company exists: business data does not belong there. Among real companies,
    the one with the most customers wins (that is where the trading happens),
    and the oldest company breaks a tie.
    """
    cursor.execute("""
        SELECT company.organization_id, company.name
        FROM organizations company
        ORDER BY (SELECT COUNT(*) FROM customers customer
                  WHERE customer.organization_id = company.organization_id) DESC,
                 COALESCE(company.is_zyro, FALSE) ASC,
                 company.organization_id ASC
        LIMIT 1
    """)
    return cursor.fetchone()


def attach_master_data_to_a_company(cursor):
    """Give every product and every tax rate a company, where the company was lost.

    A products table from an earlier version has no company on its rows at all
    (his own database did not even have the column). Without this, his products
    exist but no screen shows them, because every screen asks for the products
    of the company you are working in.

    How the company is found, in order:
      1. the company of the product's own default revenue account, when it has one;
      2. otherwise the company described in _target_company(), and a line is
         printed saying how many rows were attached and where, so that it is
         never done silently.

    A row that already has a company is never touched. Returns
    (products_attached, tax_rates_attached).
    """
    cursor.execute("""
        UPDATE products product
        SET organization_id = account.organization_id
        FROM chart_of_accounts account
        WHERE account.account_id = product.default_account_id
          AND product.organization_id IS NULL
    """)
    attached_products = cursor.rowcount or 0

    target = _target_company(cursor)
    if target is None:                       # a database with no company at all yet
        return attached_products, 0
    target_id, target_name = target

    cursor.execute("UPDATE products SET organization_id = %s WHERE organization_id IS NULL", (target_id,))
    guessed_products = cursor.rowcount or 0
    attached_products += guessed_products

    cursor.execute("UPDATE sales_tax_rates SET organization_id = %s WHERE organization_id IS NULL", (target_id,))
    attached_rates = cursor.rowcount or 0

    if guessed_products or attached_rates:
        print(f"[sales schema] {guessed_products} product(s) and {attached_rates} tax rate(s) had no "
              f"company; they were given to {target_name} (ID {target_id}). Nothing else about them was changed.")
    return attached_products, attached_rates


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
            sales_price NUMERIC(15, 2) DEFAULT 0,
            on_hand NUMERIC(15, 3) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS default_account_id INTEGER REFERENCES chart_of_accounts(account_id)")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    # A products table left over from an earlier version may be missing even
    # the columns written above - CREATE TABLE IF NOT EXISTS never touches a
    # table that already exists, which is how his own products table ended up
    # with no company on it and the invoice screen refused to open. Every line
    # below is a no-op on a table that already has the column.
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(organization_id)")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS name VARCHAR(150) NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS unit VARCHAR(20) NOT NULL DEFAULT 'Units'")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    # The two figures his own products screen shows on every card: the price
    # the product sells at, and how much of it is on hand. They are his to
    # keep on the product itself until a stock ledger exists; nothing else
    # reads them, so an untouched product behaves exactly as before.
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_price NUMERIC(15, 2) DEFAULT 0")
    cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS on_hand NUMERIC(15, 3) DEFAULT 0")

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
    # The same defensive columns as products, for the same reason.
    cursor.execute("ALTER TABLE sales_tax_rates ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(organization_id)")
    cursor.execute("ALTER TABLE sales_tax_rates ADD COLUMN IF NOT EXISTS label VARCHAR(100) NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE sales_tax_rates ADD COLUMN IF NOT EXISTS rate_percent NUMERIC(5, 2) NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE sales_tax_rates ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    attach_master_data_to_a_company(cursor)

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
    # --- the printed number of the invoice (sales/services.py) -------------
    # His own screen numbers invoices INV/2026/01749; ZYRO does the same. A
    # number is given when the invoice is saved and never changes after that.
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS invoice_no VARCHAR(30)")
    # The two boxes his invoice screen has and ZYRO had no place for.
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS lc_reference VARCHAR(100)")
    cursor.execute("ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS delivery_date DATE")
    # Two invoices of one company can never carry the same number. Invoices
    # made before numbering existed have none yet, and many "none"s are fine.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS sales_invoices_number_per_organization
        ON sales_invoices (organization_id, invoice_no)
        WHERE invoice_no IS NOT NULL
    """)

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

    # A number for the invoices made before numbering existed - once, oldest
    # first, per company per year (sales/services.py).
    from sales.services import number_missing_invoices
    number_missing_invoices(cursor)

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