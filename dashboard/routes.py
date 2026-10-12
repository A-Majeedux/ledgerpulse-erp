from flask import Blueprint, render_template
from core.context import require_organization, current_organization_id
from core.db import get_conn

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@require_organization
def home():
    """
    This is intentionally minimal for now - it just proves the foundation
    (organizations, templates, design tokens, db connection) all work
    together. Real stats (Cash Balance, Receivable, Payable) get added
    once the Accounting segment exists to calculate them from.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM organizations WHERE organization_id = %s", (org_id,))
    org_name = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return render_template("dashboard/home.html", org_name=org_name)
