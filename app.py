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
from reports.routes import bp as reports_bp

app = Flask(__name__)

from datetime import datetime

def fmt_date(value, fmt='%d %b %Y'):
    if not value:
        return '—'
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.strftime(fmt)
        except ValueError:
            return value
    if hasattr(value, 'strftime'):
        return value.strftime(fmt)
    return str(value)

app.template_filter('fmt_date')(fmt_date)


def asset_version(filename):
    """A short stamp for a stylesheet or a script, taken from when it changed.

    Without it a browser keeps the copy it downloaded yesterday and a page looks
    half-new - new HTML in old clothes. With it, "?v=<time>" changes the moment
    the file does, so a normal reload is enough and nobody has to remember to
    press Ctrl+F5.
    """
    path = os.path.join(app.static_folder or 'static', filename)
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


app.template_global('asset_v')(asset_version)


def _build_stamp():
    """Which commit this running program was started from, and when.

    Read once, when the program starts - not on every page. That is the point:
    if the code has been pulled but the server has not been restarted, the
    footer still names the OLD commit, and the page keeps showing the old
    screens. It is the quickest answer to "have I got the new version?".
    """
    root = os.path.dirname(os.path.abspath(__file__))
    stamp = 'unknown'
    try:
        head = open(os.path.join(root, '.git', 'HEAD')).read().strip()
        if head.startswith('ref:'):
            ref = head.split(' ', 1)[1].strip()
            stamp = open(os.path.join(root, '.git', ref)).read().strip()[:7]
        else:
            stamp = head[:7]
    except OSError:
        stamp = 'dev'
    return stamp


BUILD_STAMP = _build_stamp()
BUILD_STARTED = datetime.now().strftime('%d %b %Y %H:%M')


@app.context_processor
def inject_build_stamp():
    return {"build_stamp": BUILD_STAMP, "build_started": BUILD_STARTED}

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
app.register_blueprint(reports_bp)

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

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,))
    user_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user_row or not user_row[0]:
        session.clear()
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
