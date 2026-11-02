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
from core.db import get_conn
from core.context import current_organization_id
from flask import request


def register_context_processors(app):
    @app.context_processor
    def inject_organizations():
        from auth.services import current_user_id, is_platform_admin, get_zyro_organization_id
        uid = current_user_id()
        conn = get_conn()
        cursor = conn.cursor()
        
        if is_platform_admin():
            cursor.execute("SELECT organization_id, name, currency FROM organizations ORDER BY is_zyro DESC, name")
        else:
            cursor.execute("""
                SELECT o.organization_id, o.name, o.currency
                FROM organizations o
                JOIN organization_memberships om ON om.organization_id = o.organization_id
                WHERE om.user_id = %s
                ORDER BY o.name
            """, (uid,))
        org_rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        active_org_id = current_organization_id()
        active_currency = "PKR"
        organizations = []
        for row in org_rows:
            org_id = row[0]
            name = row[1]
            curr = row[2] or "PKR"
            organizations.append((org_id, name))
            if org_id == active_org_id:
                active_currency = curr
                
        return {
            "organizations": organizations,
            "active_organization_id": active_org_id,
            "active_currency": active_currency,
            "zyro_org_id": get_zyro_organization_id(),
        }

    @app.context_processor
    def inject_active_nav():
        def active_nav():
            endpoint = request.endpoint or ""
            if endpoint.startswith("dashboard."):
                return "dashboard"
            elif endpoint.startswith("sales."):
                if "product" in endpoint:
                    return "products"
                elif "tax" in endpoint:
                    return "salestax"
                else:
                    return "invoices"
            elif endpoint.startswith("customers."):
                if "receipt" in endpoint:
                    return "customer_receipts"
                else:
                    return "customers"
            elif endpoint.startswith("vendors."):
                if "bill" in endpoint:
                    return "vendor_bills"
                elif "payment" in endpoint:
                    return "vendor_payments"
                else:
                    return "vendors"
            elif endpoint.startswith("wht."):
                return "taxrates"
            elif endpoint.startswith("accounting."):
                if "chart" in endpoint or "account" in endpoint:
                    return "chart"
                else:
                    return "journals"
            elif endpoint.startswith("organizations."):
                if "renew" in endpoint:
                    return "subscriptions"
                else:
                    return "organizations"
            elif endpoint.startswith("auth."):
                if "admin" in endpoint:
                    if "user" in endpoint:
                        return "admin_users_list"
                    elif "payment" in endpoint:
                        return "admin_payments_list"
                    elif "plan" in endpoint:
                        return "admin_plans"
                    elif "tenant" in endpoint:
                        return "admin_tenants"
                    else:
                        return "admin_dashboard"
            return ""
        return {"active_nav": active_nav}
