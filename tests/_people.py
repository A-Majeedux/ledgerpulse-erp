"""
tests/_people.py

Who the tests sign in as.

The fixtures used to set `user_id` in the session and stop there - a person who
belongs to no company and holds no role. That was invisible while a page only
asked *"is somebody signed in to an organization"*, and it stopped being
invisible once the modules started asking *"is this person allowed to write an
invoice"*: the honest answer for a person with no membership is **no**.

So the tests now sign in as what the app actually expects: a member of the
company, holding the Admin role. `member()` makes one; `admin_role()` finds the
company-wide Admin role and, if the test database has no permission rows for it,
gives it every permission - which is what the real bootstrap does.
"""


def admin_role(cursor):
    """The company-wide Admin role, with every permission it should have."""
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND organization_id IS NULL")
    row = cursor.fetchone()
    if row:
        role_id = row[0]
    else:
        cursor.execute("INSERT INTO roles (name, description) VALUES ('Admin', 'Full access') "
                       "RETURNING role_id")
        role_id = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM role_permissions WHERE role_id = %s", (role_id,))
    if not cursor.fetchone()[0]:
        cursor.execute("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT %s, permission_id FROM permissions
            ON CONFLICT DO NOTHING
        """, (role_id,))
    return role_id


def member(cursor, user_id, organization_id, role_name="Admin"):
    """Make this person a member of this company (Admin unless told otherwise)."""
    if role_name == "Admin":
        role_id = admin_role(cursor)
    else:
        cursor.execute("SELECT role_id FROM roles WHERE name = %s AND organization_id IS NULL",
                       (role_name,))
        found = cursor.fetchone()
        if not found:
            raise AssertionError(f"there is no {role_name} role to give")
        role_id = found[0]
    cursor.execute("""
        INSERT INTO organization_memberships (user_id, organization_id, role_id)
        VALUES (%s, %s, %s)
    """, (user_id, organization_id, role_id))
    return role_id


def reader_role(cursor):
    """A company-wide role that may look and nothing else."""
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Viewer' AND organization_id IS NULL")
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("INSERT INTO roles (name, description) VALUES ('Viewer', 'Read only') RETURNING role_id")
    return cursor.fetchone()[0]


def viewer(cursor, user_id, organization_id):
    """A member who may see the pages and may not change anything."""
    return member(cursor, user_id, organization_id, role_name="Viewer")
