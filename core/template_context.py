"""
core/template_context.py

Registers values that should be available in EVERY template automatically,
without each route having to remember to pass them in. Right now this is
just the organization list + active organization (for the switcher in the
top bar), but any other truly-global value belongs here too.

Wire this up once in app.py:
    from core.template_context import register_context_processors
    register_context_processors(app)
"""
from flask import session
from core.db import get_conn
from core.context import current_organization_id


def register_context_processors(app):
    @app.context_processor
    def inject_organizations():
        conn = get_conn()
        cursor = conn.cursor()
        # Only the organizations the logged-in user is a member of - the
        # switcher must never list another tenant's company names. With
        # nobody logged in (login / setup screens) the list is empty.
        cursor.execute("""
            SELECT organization_id, name FROM organizations
            WHERE organization_id IN (
                SELECT organization_id FROM organization_memberships WHERE user_id = %s
            )
            ORDER BY name
        """, (session.get("user_id"),))
        organizations = cursor.fetchall()
        cursor.close()
        conn.close()
        return {
            "organizations": organizations,
            "active_organization_id": current_organization_id(),
        }
