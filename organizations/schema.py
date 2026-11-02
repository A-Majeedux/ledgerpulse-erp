"""
organizations/schema.py

An "organization" is one company's separate set of books. Every table in
every other segment (accounting, sales, vendors, etc.) should carry an
organization_id column pointing here, so one login can hold many
completely separate companies with no data crossing between them.
"""


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            email VARCHAR(255),
            phone VARCHAR(50),
            address TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            organization_id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            currency VARCHAR(10) DEFAULT 'PKR',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Additive columns - safe to run even on a database that already has
    # the organizations table from before this business-details expansion.
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS nature_of_business VARCHAR(50)")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS ntn VARCHAR(30)")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS strn VARCHAR(30)")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS head_office_address TEXT")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS warehouse_address TEXT")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS logo_path VARCHAR(255)")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS fiscal_year_start_month INTEGER DEFAULT 7")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS client_id INTEGER REFERENCES clients(client_id) ON DELETE SET NULL")
    cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS is_zyro BOOLEAN DEFAULT FALSE")

    # Ensure ZYRO organization exists
    cursor.execute("SELECT organization_id FROM organizations WHERE is_zyro = TRUE LIMIT 1")
    if not cursor.fetchone():
        cursor.execute("SELECT organization_id FROM organizations ORDER BY organization_id LIMIT 1")
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE organizations SET is_zyro = TRUE WHERE organization_id = %s", (row[0],))
        else:
            cursor.execute(
                "INSERT INTO organizations (name, is_zyro) VALUES (%s, TRUE)",
                ("ZYRO Head Office",)
            )
