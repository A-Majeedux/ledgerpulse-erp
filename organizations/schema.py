"""
organizations/schema.py

An "organization" is one company's separate set of books. Every table in
every other segment (accounting, sales, vendors, etc.) should carry an
organization_id column pointing here, so one login can hold many
completely separate companies with no data crossing between them.
"""


def ensure_schema(cursor):
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

    # If no organization exists yet (very first run), create one default
    # one so the app is usable immediately instead of showing an empty
    # picker screen.
    cursor.execute("SELECT COUNT(*) FROM organizations")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO organizations (name) VALUES (%s)",
            ("My Organization",)
        )
