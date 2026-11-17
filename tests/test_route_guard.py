"""
tests/test_route_guard.py

Every page and every button has to answer one question before it does anything:
*is this person allowed?* A menu that hides an item is a courtesy; the route is
the lock. This file checks the lock, from two directions:

  * **the route table itself** - every rule in a module that has been finished
    must carry a permission, so a new route cannot slip in ungated. The guard is
    written on the view function by `require_permission`, which is how the test
    can read it back.
  * **a person who may only look** - a Viewer, whose role holds the reading
    permissions and nothing else: he sees the list and the invoice, gets **403**
    on every way of changing one, and is not shown a button that would refuse him.

Modules are added to `GUARDED_MODULES` as they are finished, one per round.
"""
import unittest
import uuid
from decimal import Decimal as D

from _people import member, viewer

# The modules whose every route is behind a permission. Grows a module per round.
GUARDED_MODULES = {"sales", "accounting"}

# Routes that are not a company's business: signing in, setting up the very first
# account, and the platform's own administration.
PUBLIC_PREFIXES = ("auth.",)


class TheRouteTableTests(unittest.TestCase):
    """Read the map the app publishes and look for a way through without a permission."""

    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app = flask_app.app

    def routes_of(self, module):
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint.startswith(module + "."):
                yield rule

    def test_every_route_in_a_finished_module_asks_for_a_permission(self):
        unguarded = []
        checked = 0
        for module in sorted(GUARDED_MODULES):
            for rule in self.routes_of(module):
                checked += 1
                view = self.app.view_functions[rule.endpoint]
                if not getattr(view, "required_permission", None):
                    unguarded.append(f"{sorted(rule.methods - {'HEAD', 'OPTIONS'})} {rule} "
                                     f"({rule.endpoint})")
        self.assertEqual(unguarded, [],
                         "these routes can be reached without a permission:\n  " + "\n  ".join(unguarded))
        self.assertGreater(checked, 5, "the walk found hardly any routes - is the endpoint prefix right?")

    def test_the_writes_of_a_finished_module_ask_for_a_writing_permission(self):
        """A route that changes something must not be satisfied by a reading right."""
        reading_only = []
        for module in sorted(GUARDED_MODULES):
            for rule in self.routes_of(module):
                writes = rule.methods - {"GET", "HEAD", "OPTIONS"}
                if not writes:
                    continue
                view = self.app.view_functions[rule.endpoint]
                code = getattr(view, "required_permission", "")
                if code and code.endswith(".view"):
                    reading_only.append(f"{sorted(writes)} {rule} -> {code}")
        self.assertEqual(reading_only, [], "a write route asks only for a .view right:\n  "
                                           + "\n  ".join(reading_only))


class _OneCompany(unittest.TestCase):
    """A company, an invoice, and two people: an Admin and a Viewer."""

    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app = flask_app.app
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_GUARD_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cls.people = {}
        for who in ("admin", "viewer"):
            cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                           "VALUES (%s, %s, 'hash', TRUE) RETURNING user_id",
                           (who.title(), f"{who}_{uuid.uuid4().hex[:8]}@guard.local"))
            cls.people[who] = cursor.fetchone()[0]
        member(cursor, cls.people["admin"], cls.org_id)
        viewer(cursor, cls.people["viewer"], cls.org_id)
        # the Viewer may look at sales, and at nothing else here
        cursor.execute("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.role_id, p.permission_id FROM roles r, permissions p
            WHERE r.name = 'Viewer' AND r.organization_id IS NULL AND p.code = 'sales.view'
            ON CONFLICT DO NOTHING
        """)
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s LIMIT 1", (cls.org_id,))
        cls.accounts = {}
        for code, name, kind in (("1100", "Trade Receivables", "Asset"), ("4000", "Sales", "Revenue")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id", (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, 1, 'Guarded Customer') "
                       "RETURNING customer_id", (cls.org_id,))
        cls.customer_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (cls.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM customers WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (cls.org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(cls.people.values()),))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                       "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (self.org_id,))
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                       "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (self.org_id,))
        cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()

    def sign_in(self, who):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.people[who]
            flask_session["user_name"] = who.title()
            flask_session["csrf_token"] = "test-csrf"

    def make_invoice(self, status="Draft"):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, due_date,
                                        status, invoice_no)
            VALUES (%s, %s, '2026-11-01', '2026-12-01', %s, 'INV/2026/00001')
            RETURNING sales_invoice_id
        """, (self.org_id, self.customer_id, status))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_invoice_lines (sales_invoice_id, account_id, label, quantity, rate, total, line_order)
            VALUES (%s, %s, 'Something sold', 1, 100, 100, 0)
        """, (invoice_id, self.accounts["4000"]))
        cursor.execute("""
            INSERT INTO journal_headers (organization_id, reference, accounting_date, journal_name,
                                         status, source_type, source_id)
            VALUES (%s, 'Invoice', '2026-11-01', 'Customer Invoices', %s, 'SalesInvoice', %s)
            RETURNING journal_header_id
        """, (self.org_id, status, invoice_id))
        header = cursor.fetchone()[0]
        for account, debit, credit in ((self.accounts["1100"], 100, 0), (self.accounts["4000"], 0, 100)):
            cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, label, debit, credit, "
                           "line_order) VALUES (%s, %s, 'x', %s, %s, 0)", (header, account, debit, credit))
        cursor.execute("UPDATE sales_invoices SET journal_header_id = %s WHERE sales_invoice_id = %s",
                       (header, invoice_id))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id


class AViewerUnTests(_OneCompany):
    """What a person with the reading right and nothing else can and cannot do."""

    def test_he_can_open_the_invoices_and_read_one(self):
        invoice_id = self.make_invoice()
        self.sign_in("viewer")
        listing = self.client.get("/sales/invoices")
        self.assertEqual(listing.status_code, 200)
        detail = self.client.get(f"/sales/invoices/{invoice_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("INV/2026/00001", detail.get_data(as_text=True))

    def test_he_is_not_shown_a_button_that_would_refuse_him(self):
        invoice_id = self.make_invoice()
        self.sign_in("viewer")
        listing = self.client.get("/sales/invoices").get_data(as_text=True)
        self.assertNotIn("/sales/invoices/new", listing)
        detail = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertNotIn("Post Invoice", detail)
        for url, forbidden in (("/sales/products", "/sales/products/new"),
                               ("/sales/tax-rates", "/sales/tax-rates/new")):
            body = self.client.get(url).get_data(as_text=True)
            self.assertNotIn(forbidden, body, url)
            self.assertNotIn("/sales/invoices/new", body, url)

    def test_every_way_of_changing_an_invoice_is_closed_to_him(self):
        invoice_id = self.make_invoice()
        self.sign_in("viewer")
        attempts = [
            ("GET", "/sales/invoices/new"),
            ("POST", "/sales/invoices/new"),
            ("GET", f"/sales/invoices/{invoice_id}/edit"),
            ("POST", f"/sales/invoices/{invoice_id}/edit"),
            ("POST", f"/sales/invoices/{invoice_id}/post"),
            ("POST", f"/sales/invoices/{invoice_id}/apply-receipt"),
            ("POST", f"/sales/invoices/{invoice_id}/duplicate"),
            ("POST", f"/sales/invoices/{invoice_id}/delete"),
            ("POST", "/sales/products/new"),
            ("GET", "/sales/products/new"),
            ("POST", "/sales/tax-rates/new"),
            ("POST", "/sales/tax-rates/1/toggle"),
        ]
        for method, url in attempts:
            response = self.client.open(url, method=method, data={"csrf_token": "test-csrf"})
            self.assertEqual(response.status_code, 403, f"{method} {url} let a viewer through")

    def test_the_books_are_closed_to_him_too(self):
        self.sign_in("viewer")
        for method, url in (("GET", "/accounting/journal-entries/new"),
                            ("POST", "/accounting/journal-entries/new"),
                            ("GET", "/accounting/chart-of-accounts/new"),
                            ("GET", "/accounting/bank-transfers/new")):
            response = self.client.open(url, method=method, data={"csrf_token": "test-csrf"})
            self.assertEqual(response.status_code, 403, f"{method} {url} let a viewer through")
        # but he may read them
        self.assertEqual(self.client.get("/accounting/journal-entries").status_code, 200)
        body = self.client.get("/accounting/journal-entries").get_data(as_text=True)
        self.assertNotIn("/accounting/journal-entries/new", body)

    def test_the_admin_is_let_through_the_same_doors(self):
        invoice_id = self.make_invoice()
        self.sign_in("admin")
        self.assertEqual(self.client.get("/sales/invoices/new").status_code, 200)
        self.assertEqual(self.client.get(f"/sales/invoices/{invoice_id}/edit").status_code, 200)
        body = self.client.get(f"/sales/invoices/{invoice_id}").get_data(as_text=True)
        self.assertIn("Post Invoice", body)

    def test_a_person_who_does_not_belong_to_the_company_is_turned_away(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Outsider', %s, 'hash', TRUE) RETURNING user_id",
                       (f"outsider_{uuid.uuid4().hex[:8]}@x.local",))
        outsider = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        try:
            with self.client.session_transaction() as flask_session:
                flask_session["organization_id"] = self.org_id
                flask_session["user_id"] = outsider
                flask_session["csrf_token"] = "test-csrf"
            self.assertEqual(self.client.get("/sales/invoices").status_code, 403)
        finally:
            conn = get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE user_id = %s", (outsider,))
            conn.commit()
            cursor.close()
            conn.close()


if __name__ == "__main__":
    unittest.main()
