"""
wht/schema.py

The Tax Rates master list - a small, shared table both Vendors and
Customers pick from when applying WHT to a transaction. Per Abdul's
explicit design: the SOFTWARE never decides a rate, an exemption, or a
threshold - it only maintains the list of currently-applicable rates so
the bookkeeper can pick the right one, since Pakistan's WHT rates change
with every Finance Act.
"""

# Common Pakistan WHT rates used as a starting list - ordinary starting
# points, NOT authoritative.
STARTER_WHT_RATES = [
    ("WHT 1%", 1.0), ("WHT 1.5%", 1.5), ("WHT 4%", 4.0),
    ("WHT 4.5%", 4.5), ("WHT 5%", 5.0), ("WHT 6%", 6.0),
    ("WHT 8%", 8.0), ("WHT 10%", 10.0),
]


def seed_organization(cursor, organization_id):
    """
    Give ONE organization the starter WHT rate list - only if its list is
    completely empty. Called at startup for every organization, and again
    the moment a new organization is created (core.db.seed_new_organization).
    """
    cursor.execute("SELECT COUNT(*) FROM tax_rates WHERE organization_id = %s", (organization_id,))
    if cursor.fetchone()[0] == 0:
        for label, rate in STARTER_WHT_RATES:
            cursor.execute("""
                INSERT INTO tax_rates (organization_id, label, rate_percent)
                VALUES (%s, %s, %s)
            """, (organization_id, label, rate))


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tax_rates (
            tax_rate_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            label VARCHAR(100) NOT NULL,
            rate_percent NUMERIC(5, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE tax_rates ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    # Seed a few common Pakistan WHT rates so the list isn't empty on day
    # one - ordinary starting points, NOT authoritative; the bookkeeper is
    # expected to edit/add/retire rates as the Finance Act changes.
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        seed_organization(cursor, org_id)
