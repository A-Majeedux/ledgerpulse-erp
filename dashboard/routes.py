from datetime import date

from flask import Blueprint, redirect, render_template, request, session, url_for

from core.context import current_organization_id, require_organization
from core.db import get_conn
from dashboard.services import build_dashboard, format_money, format_percent, plain_amount

bp = Blueprint("dashboard", __name__)


def _today():
    """
    The date the dashboard treats as "today" - the server's date, the same
    one every voucher form uses for its default date. It is a function of
    its own so the automated checks can pin it to a fixed day.
    """
    return date.today()


@bp.route("/")
@require_organization
def home():
    """
    The Financial Control Center. Every figure is worked out from POSTED
    journal entries of the active organization - see dashboard/services.py
    for the exact rules. This route only reads; it changes nothing.

    ?period=month | last_month | quarter | fiscal_year   (default: month)
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        dashboard = build_dashboard(
            cursor, current_organization_id(), _today(), request.args.get("period")
        )
    finally:
        cursor.close()
        conn.close()

    if dashboard is None:
        # The organization remembered in the session no longer exists.
        # Forget it and let the user pick one, instead of an error page.
        session.pop("organization_id", None)
        return redirect(url_for("organizations.pick_organization"))

    return render_template(
        "dashboard/home.html",
        d=dashboard,
        user_name=session.get("user_name"),
        money=format_money,
        plain=plain_amount,
        percent=format_percent,
    )
