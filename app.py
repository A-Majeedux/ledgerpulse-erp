import os
from flask import Flask, session, redirect, url_for, request, render_template, abort
from markupsafe import Markup

from core.db import ensure_all_schemas, get_conn
from core.template_context import register_context_processors

from auth.schema import ensure_schema as auth_schema
from auth.routes import bp as auth_bp
from auth.services import generate_csrf_token, validate_csrf, check_subscription

from organizations.schema import ensure_schema as organizations_schema
from organizations.routes import bp as organizations_bp

from accounting.schema import ensure_schema as accounting_schema
from accounting.routes import bp as accounting_bp

from vendors.schema import ensure_schema as vendors_schema
from vendors.routes import bp as vendors_bp

from wht.schema import ensure_schema as wht_schema
from wht.routes import bp as wht_bp

from customers.schema import ensure_schema as customers_schema
from customers.routes import bp as customers_bp

from sales.schema import ensure_schema as sales_schema
from sales.routes import bp as sales_bp

from dashboard.routes import bp as dashboard_bp

app = Flask(__name__)

# Session security & secret key configuration
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    if os.getenv("FLASK_ENV") == "production":
        raise RuntimeError("CRITICAL: SECRET_KEY environment variable must be set in production.")
    secret_key = "dev-secret-change-me"

app.secret_key = secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"

# --- 1. Make sure every table exists in dependency order ---
if os.getenv("TESTING") != "true" and os.getenv("FLASK_ENV") != "testing":
    ensure_all_schemas([
        auth_schema,
        organizations_schema,
        accounting_schema,
        wht_schema,
        vendors_schema,
        customers_schema,
        sales_schema,
    ])

# --- 2. Register blueprints ---
app.register_blueprint(auth_bp)
app.register_blueprint(organizations_bp)
app.register_blueprint(accounting_bp)
app.register_blueprint(wht_bp)
app.register_blueprint(vendors_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(sales_bp)
app.register_blueprint(dashboard_bp)

# --- 3. Make organization switcher and auth helpers available everywhere ---
register_context_processors(app)


@app.context_processor
def inject_csrf_token():
    token = generate_csrf_token()
    def csrf_field():
        return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')
    return {"csrf_field": csrf_field, "csrf_token": token}


@app.before_request
def security_before_request():
    # 1. CSRF validation for state-changing requests
    validate_csrf()

    # 2. Global Authentication Enforcement
    if request.endpoint and (request.endpoint.startswith("auth.") or request.endpoint == "static"):
        return

    # Check if any users exist
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    if user_count == 0:
        return redirect(url_for("auth.setup"))

    if not session.get("user_id"):
        return redirect(url_for("auth.login"))

    # 3. Subscription & Grace Period Enforcement
    org_id = session.get("organization_id")
    if org_id and request.endpoint and not request.endpoint.startswith("organizations."):
        sub = check_subscription(org_id)
        status = sub.get("status")
        if status == "SUSPENDED" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            abort(403, description="Organization subscription is SUSPENDED. Write operations and transactions are blocked.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG", "True").lower() == "true")
