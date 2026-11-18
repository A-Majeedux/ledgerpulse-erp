"""
tests/test_role_editor.py

Who may hand out what.

A role is not a label, it is a set of rights, and the screen that builds one is
therefore the most dangerous screen in the system: whoever can write a role can
write *any* role. Until now `users.manage` was quietly god-mode - a manager could
give his own membership the right to post journals, delete invoices and change
the company's books.

The rule now is simple and testable: **a person cannot grant a right he does not
hold himself.** The platform administrator is the one exception.

The rest of the file checks the three routes that were still open (reports, WHT
rates, the company's own settings) and the company picker, which used to list
every company on the installation to everyone.
"""
import unittest
import uuid

from _people import member, viewer


class _TwoManagers(unittest.TestCase):
    """A company, a manager who may manage users, and people around him."""

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
        tag = uuid.uuid4().hex[:8]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_ROLES_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_SOMEONE_ELSE_{tag}",))
        cls.other_org_id = cursor.fetchone()[0]

        cls.people = {}
        for who in ("boss", "manager", "viewer"):
            cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                           "VALUES (%s, %s, 'hash', TRUE) RETURNING user_id",
                           (who.title(), f"{who}_{tag}@roles.local"))
            cls.people[who] = cursor.fetchone()[0]
        member(cursor, cls.people["boss"], cls.org_id)
        viewer(cursor, cls.people["viewer"], cls.org_id)

        # The manager: may manage users, and nothing else worth anything.
        cursor.execute("INSERT INTO roles (organization_id, name, description) "
                       "VALUES (%s, %s, 'People, not books') RETURNING role_id",
                       (cls.org_id, f"Office Manager {tag}"))
        cls.manager_role_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT %s, permission_id FROM permissions WHERE code IN ('users.manage', 'users.view')
        """, (cls.manager_role_id,))
        cursor.execute("INSERT INTO organization_memberships (user_id, organization_id, role_id) "
                       "VALUES (%s, %s, %s)",
                       (cls.people["manager"], cls.org_id, cls.manager_role_id))

        cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND organization_id IS NULL")
        cls.admin_role_id = cursor.fetchone()[0]
        cursor.execute("SELECT role_id FROM roles WHERE name = 'Viewer' AND organization_id IS NULL")
        cls.viewer_role_id = cursor.fetchone()[0]

        cursor.execute("SELECT permission_id FROM permissions WHERE code = 'accounting.post'")
        cls.accounting_post_id = cursor.fetchone()[0]
        cursor.execute("SELECT permission_id FROM permissions WHERE code = 'users.manage'")
        cls.users_manage_id = cursor.fetchone()[0]

        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in (cls.org_id, cls.other_org_id):
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM role_permissions WHERE role_id IN "
                           "(SELECT role_id FROM roles WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM roles WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(cls.people.values()),))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        # each test starts with the manager's role exactly as it was made
        cursor.execute("DELETE FROM role_permissions WHERE role_id IN "
                       "(SELECT role_id FROM roles WHERE organization_id = %s AND role_id <> %s)",
                       (self.org_id, self.manager_role_id))
        cursor.execute("DELETE FROM roles WHERE organization_id = %s AND role_id <> %s",
                       (self.org_id, self.manager_role_id))
        cursor.execute("UPDATE organization_memberships SET role_id = %s "
                       "WHERE user_id = %s AND organization_id = %s",
                       (self.manager_role_id, self.people["manager"], self.org_id))
        conn.commit()
        cursor.close()
        conn.close()

    def sign_in(self, who, organization_id=None):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = organization_id or self.org_id
            flask_session["user_id"] = self.people[who]
            flask_session["user_name"] = who.title()
            flask_session["csrf_token"] = "test-csrf"

    def roles_named(self, name):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT role_id FROM roles WHERE organization_id = %s AND name = %s",
                       (self.org_id, name))
        found = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return found

    # -- nobody builds a role bigger than himself --------------------------

    def test_a_manager_cannot_grant_a_right_he_does_not_hold(self):
        self.sign_in("manager")
        response = self.client.post(
            f"/organizations/{self.org_id}/roles/new",
            data={"csrf_token": "test-csrf", "name": "Sneaky Poster", "description": "",
                      "permission_ids": [str(self.accounting_post_id)]},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("cannot grant a right you do not hold yourself", body)
        self.assertIn("accounting.post", body)
        self.assertEqual(self.roles_named("Sneaky Poster"), [],
                         "the role was created anyway - the guard did not hold")

    def test_a_manager_can_grant_what_he_holds(self):
        self.sign_in("manager")
        self.client.post(
            f"/organizations/{self.org_id}/roles/new",
            data={"csrf_token": "test-csrf", "name": "Assistant", "description": "helps with people",
                  "permission_ids": [str(self.users_manage_id)]},
            follow_redirects=True,
        )
        created = self.roles_named("Assistant")
        self.assertEqual(len(created), 1)
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT permission_id FROM role_permissions WHERE role_id = %s", (created[0],))
        self.assertEqual([row[0] for row in cursor.fetchall()], [self.users_manage_id])
        cursor.close()
        conn.close()

    def test_handing_somebody_a_bigger_role_is_refused(self):
        """The other way to get a right you lack: give it to a friend, log in as him."""
        self.sign_in("manager")
        response = self.client.post(
            f"/organizations/{self.org_id}/users/{self.people['viewer']}/role",
            data={"csrf_token": "test-csrf", "role_id": self.admin_role_id}, follow_redirects=True,
        )
        self.assertIn("cannot grant a right you do not hold yourself", response.get_data(as_text=True))
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT role_id FROM organization_memberships WHERE user_id = %s AND organization_id = %s",
                       (self.people["viewer"], self.org_id))
        self.assertEqual(cursor.fetchone()[0], self.viewer_role_id, "the viewer was promoted to Admin")
        cursor.close()
        conn.close()

    def test_a_built_in_role_can_be_copied_into_one_of_the_company_s_own(self):
        self.sign_in("boss")
        response = self.client.post(
            f"/organizations/{self.org_id}/roles/{self.viewer_role_id}/copy",
            data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        created = self.roles_named("Viewer (copy)")
        self.assertEqual(len(created), 1, "the copy was not made")
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT organization_id FROM roles WHERE role_id = %s", (created[0],))
        self.assertEqual(cursor.fetchone()[0], self.org_id, "a copy must belong to the company, so it can be edited")
        cursor.execute("""SELECT COUNT(*) FROM role_permissions rp JOIN role_permissions rp2
                          ON rp.permission_id = rp2.permission_id AND rp2.role_id = %s
                          WHERE rp.role_id = %s""", (self.viewer_role_id, created[0]))
        copied = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM role_permissions WHERE role_id = %s", (self.viewer_role_id,))
        self.assertEqual(copied, cursor.fetchone()[0], "the copy lost some of the original's rights")
        cursor.close()
        conn.close()

    def test_the_roles_screen_offers_a_copy_of_every_role(self):
        self.sign_in("boss")
        body = self.client.get(f"/organizations/{self.org_id}/roles").get_data(as_text=True)
        self.assertIn(f"/roles/{self.viewer_role_id}/copy", body)
        self.assertIn(f"/roles/{self.admin_role_id}/copy", body)
        self.assertGreaterEqual(body.count("/copy"), 3)

    def test_a_copy_never_holds_more_than_the_person_making_it(self):
        """The manager copies Admin: he gets what he holds, and is told the rest."""
        self.sign_in("manager")
        response = self.client.post(
            f"/organizations/{self.org_id}/roles/{self.admin_role_id}/copy",
            data={"csrf_token": "test-csrf"}, follow_redirects=True)
        body = response.get_data(as_text=True)
        created = self.roles_named("Admin (copy)")
        self.assertEqual(len(created), 1)
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""SELECT p.code FROM role_permissions rp JOIN permissions p
                          ON p.permission_id = rp.permission_id WHERE rp.role_id = %s""", (created[0],))
        codes = {row[0] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        self.assertEqual(codes, {"users.manage"})
        self.assertIn("Not granted", body)
        self.assertIn("accounting.post", body)


class _RoutesThatWereOpen(unittest.TestCase):
    """The last ungated pages: reports, WHT rates, the company's own settings."""

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
        tag = uuid.uuid4().hex[:8]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_OPEN_{tag}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (f"TEST_NOT_MINE_{tag}",))
        cls.other_org_id = cursor.fetchone()[0]
        cls.other_name = f"TEST_NOT_MINE_{tag}"

        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Bookkeeper', %s, 'hash', TRUE) RETURNING user_id",
                       (f"keeper_{tag}@open.local",))
        cls.user_id = cursor.fetchone()[0]
        # a role that may sell and nothing else
        cursor.execute("INSERT INTO roles (organization_id, name) VALUES (%s, %s) RETURNING role_id",
                       (cls.org_id, f"Sales Only {tag}"))
        cls.role_id = cursor.fetchone()[0]
        cursor.execute("""INSERT INTO role_permissions (role_id, permission_id)
                          SELECT %s, permission_id FROM permissions WHERE code = 'sales.view'""",
                       (cls.role_id,))
        cursor.execute("INSERT INTO organization_memberships (user_id, organization_id, role_id) "
                       "VALUES (%s, %s, %s)", (cls.user_id, cls.org_id, cls.role_id))
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in (cls.org_id, cls.other_org_id):
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM role_permissions WHERE role_id IN "
                           "(SELECT role_id FROM roles WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM roles WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def sign_in(self):
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Bookkeeper"
            flask_session["csrf_token"] = "test-csrf"

    def test_the_reports_need_the_right_to_read_reports(self):
        self.sign_in()
        for url in ("/reports/trial-balance", "/reports/general-ledger"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_the_tax_rate_screens_need_an_accounting_right(self):
        self.sign_in()
        self.assertEqual(self.client.get("/tax-rates/").status_code, 403)
        self.assertEqual(self.client.post("/tax-rates/new",
                                          data={"csrf_token": "test-csrf", "name": "x", "rate": "1"}).status_code, 403)

    def test_the_company_s_own_settings_need_users_manage(self):
        self.sign_in()
        self.assertEqual(self.client.get(f"/organizations/{self.org_id}/edit").status_code, 403)
        self.assertEqual(self.client.get("/organizations/manage").status_code, 403)

    def test_renewing_the_subscription_needs_the_subscription_right(self):
        self.sign_in()
        self.assertEqual(self.client.get("/organizations/subscription/renew").status_code, 403)

    def test_the_company_picker_lists_only_my_companies(self):
        """A list of every company on the installation is not a customer's to read."""
        self.sign_in()
        body = self.client.get("/organizations/pick").get_data(as_text=True)
        self.assertIn("TEST_OPEN_", body)
        self.assertNotIn(self.other_name, body,
                         "the picker showed a company this person does not belong to")


if __name__ == "__main__":
    unittest.main()
