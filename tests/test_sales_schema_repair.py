"""
tests/test_sales_schema_repair.py

The bug his own database showed: a products table made by an earlier version
has no company on it - not even the column - so every sales screen that asks
for "the products of this company" crashed:

    psycopg2.errors.UndefinedColumn: column p.organization_id does not exist

CREATE TABLE IF NOT EXISTS never adds a column to a table that already exists,
so this test builds a throwaway database shaped the old way (a products table
with no company column, a head office plus his real company, products on no
company at all), opens the Sales schema on it, and checks that:

  * the invoice screen and the products screen open again;
  * the old products were given to HIS company, not to the ZYRO head office;
  * the tax rates were given a company too;
  * a product that already had a company was left exactly as it was.

The throwaway database is created and dropped by the test itself; DATABASE_URL
must point at the usual test database, whose server is asked to make another.
Needs DATABASE_URL set to a TEST database:

    python -m pytest tests/test_sales_schema_repair.py -q
"""
import os
import unittest
import uuid

import psycopg2
from psycopg2.extensions import make_dsn

from sales.schema import attach_master_data_to_a_company, _target_company


class OldProductsTableTests(unittest.TestCase):
    """A database shaped the way his was, built and dropped by this test."""

    def setUp(self):
        self.name = f"zyro_repair_{uuid.uuid4().hex[:8]}"
        self.dsn = make_dsn(os.environ["DATABASE_URL"], dbname=self.name)
        admin = psycopg2.connect(make_dsn(os.environ["DATABASE_URL"], dbname="postgres"))
        admin.autocommit = True
        cursor = admin.cursor()
        cursor.execute(f'CREATE DATABASE "{self.name}"')
        cursor.close()
        admin.close()

        conn = psycopg2.connect(self.dsn)
        cursor = conn.cursor()
        # The tables Sales leans on, as the rest of the app leaves them.
        cursor.execute("""
            CREATE TABLE organizations (
                organization_id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                currency VARCHAR(10) DEFAULT 'PKR',
                is_zyro BOOLEAN DEFAULT FALSE
            )
        """)
        cursor.execute("""
            CREATE TABLE chart_of_accounts (
                account_id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
                code VARCHAR(20) NOT NULL,
                name VARCHAR(150) NOT NULL,
                account_type VARCHAR(20) NOT NULL,
                allow_reconciliation BOOLEAN DEFAULT FALSE
            )
        """)
        cursor.execute("""
            CREATE TABLE journal_headers (
                journal_header_id SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations(organization_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE customers (
                customer_id SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations(organization_id),
                name VARCHAR(150) NOT NULL
            )
        """)
        # AN OLD products table: no company column at all.
        cursor.execute("""
            CREATE TABLE products (
                product_id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                unit VARCHAR(20) NOT NULL DEFAULT 'Units',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # AN OLD sales tax table: no company column either.
        cursor.execute("""
            CREATE TABLE sales_tax_rates (
                sales_tax_rate_id SERIAL PRIMARY KEY,
                label VARCHAR(100) NOT NULL,
                rate_percent NUMERIC(5, 2) NOT NULL
            )
        """)
        cursor.execute("INSERT INTO organizations (name, is_zyro) VALUES ('ZYRO Head Office', TRUE)")
        cursor.execute("INSERT INTO organizations (name, currency) VALUES ('HEXTAR CHEMICALS ENTERPRISES', 'PKR') "
                       "RETURNING organization_id")
        self.real_company = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, name) VALUES (%s, 'Punjab Zari Corporation')",
                       (self.real_company,))
        cursor.execute("INSERT INTO products (name, unit) VALUES "
                       "('10-Ltr Cane White Bulk', 'Units'), ('250-ML White Bottle', 'Units')")
        cursor.execute("INSERT INTO sales_tax_rates (label, rate_percent) VALUES ('Sales Tax 18%', 18.0)")
        conn.commit()
        cursor.close()
        conn.close()
        self.open_connection()

    def tearDown(self):
        self.conn.rollback()
        self.cursor.close()
        self.conn.close()
        admin = psycopg2.connect(make_dsn(os.environ["DATABASE_URL"], dbname="postgres"))
        admin.autocommit = True
        cursor = admin.cursor()
        cursor.execute(f'DROP DATABASE IF EXISTS "{self.name}"')
        cursor.close()
        admin.close()

    def open_connection(self):
        self.conn = psycopg2.connect(self.dsn)
        self.cursor = self.conn.cursor()

    def test_the_sales_schema_repairs_the_old_tables(self):
        from sales.schema import ensure_schema
        ensure_schema(self.cursor)          # the line the app runs at every start
        self.conn.commit()

        self.cursor.execute("SELECT COUNT(*) FROM information_schema.columns "
                            "WHERE table_name = 'products' AND column_name = 'organization_id'")
        self.assertEqual(self.cursor.fetchone()[0], 1)      # the column was added

        self.cursor.execute("SELECT name, organization_id, is_active, unit FROM products ORDER BY product_id")
        products = self.cursor.fetchall()
        self.assertEqual(len(products), 2)
        for name, organization_id, is_active, unit in products:
            self.assertEqual(organization_id, self.real_company, name)   # his company, not the head office
            self.assertTrue(is_active)
            self.assertEqual(unit, "Units")

        # no tax rate is left without a company, and the one that was in the old
        # table belongs to his company (ZYRO seeds its own rates for each company,
        # so the label alone does not identify it)
        self.cursor.execute("SELECT COUNT(*) FROM sales_tax_rates WHERE organization_id IS NULL")
        self.assertEqual(self.cursor.fetchone()[0], 0)
        self.cursor.execute("SELECT organization_id FROM sales_tax_rates WHERE label = 'Sales Tax 18%'")
        self.assertIn(self.real_company, [row[0] for row in self.cursor.fetchall()])

        # the screens' own queries now work
        self.cursor.execute("""
            SELECT p.product_id, p.name FROM products p
            WHERE p.organization_id = %s AND p.is_active = TRUE ORDER BY p.name
        """, (self.real_company,))
        self.assertEqual([row[1] for row in self.cursor.fetchall()],
                         ["10-Ltr Cane White Bulk", "250-ML White Bottle"])

    def test_a_product_that_already_has_a_company_is_left_alone(self):
        """A product carrying a company keeps it - the repair only fills the gaps."""
        from sales.schema import ensure_schema
        cursor = self.cursor
        cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS organization_id INTEGER")
        cursor.execute("SELECT organization_id FROM organizations WHERE is_zyro = TRUE")
        head_office = cursor.fetchone()[0]
        cursor.execute("UPDATE products SET organization_id = %s WHERE name = '10-Ltr Cane White Bulk'",
                       (head_office,))
        ensure_schema(cursor)
        self.conn.commit()
        cursor.execute("SELECT organization_id FROM products WHERE name = '10-Ltr Cane White Bulk'")
        self.assertEqual(cursor.fetchone()[0], head_office)              # exactly as it was
        cursor.execute("SELECT organization_id FROM products WHERE name = '250-ML White Bottle'")
        self.assertEqual(cursor.fetchone()[0], self.real_company)       # the gap was filled
        cursor.execute("SELECT COUNT(*) FROM products WHERE organization_id IS NULL")
        self.assertEqual(cursor.fetchone()[0], 0)

    def test_running_the_repair_again_changes_nothing(self):
        from sales.schema import ensure_schema
        ensure_schema(self.cursor)
        self.conn.commit()
        self.assertEqual(attach_master_data_to_a_company(self.cursor), (0, 0))

    def test_the_head_office_is_never_chosen_while_a_real_company_exists(self):
        from sales.schema import ensure_schema
        ensure_schema(self.cursor)
        self.conn.commit()
        organization_id, name = _target_company(self.cursor)
        self.assertEqual(organization_id, self.real_company)
        self.assertTrue(name.startswith("HEXTAR"))


class TargetCompanyTests(unittest.TestCase):
    """The chooser, on the ordinary test database."""

    def test_the_head_office_is_never_chosen_while_a_real_company_exists(self):
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        app = flask_app.app
        with app.app_context():
            from core.db import get_conn
            conn = get_conn()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT organization_id FROM organizations WHERE is_zyro = TRUE LIMIT 1")
                row = cursor.fetchone()
                if row is None:          # this database has no head office at all
                    self.skipTest("this database has no ZYRO head office")
                head_office = row[0]
                # a real company with one customer, which is where trading happens
                real_name = f"TEST_TARGET_COMPANY_{uuid.uuid4().hex[:8]}"
                cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') "
                               "RETURNING organization_id", (real_name,))
                real_company = cursor.fetchone()[0]
                cursor.execute("INSERT INTO customers (organization_id, name) VALUES (%s, 'Target Customer')",
                               (real_company,))
                conn.commit()

                target = _target_company(cursor)
                self.assertIsNotNone(target)
                self.assertNotEqual(target[0], head_office)      # never the head office
                self.assertEqual(target[0], real_company)
            finally:
                conn.rollback()
                cursor.execute("DELETE FROM customers WHERE name = 'Target Customer' AND organization_id IN "
                               "(SELECT organization_id FROM organizations WHERE name LIKE 'TEST_TARGET_COMPANY_%')")
                cursor.execute("DELETE FROM organizations WHERE name LIKE 'TEST_TARGET_COMPANY_%'")
                conn.commit()
                cursor.close()
                conn.close()


if __name__ == "__main__":
    unittest.main()
