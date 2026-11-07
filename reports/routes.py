from datetime import date, datetime

from flask import Blueprint, render_template, request

from core.db import get_conn
from core.context import current_organization_id, require_organization
from reports.services import (
    fetch_trial_balance_rows,
    fetch_unbalanced_entries,
    compute_trial_balance,
)

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _parse_date(value, fallback):
    """Reads YYYY-MM-DD from the URL. Bad or empty input -> fallback."""
    if not value:
        return fallback
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return fallback


@bp.route("/trial-balance")
@require_organization
def trial_balance():
    org_id = current_organization_id()
    as_at = _parse_date(request.args.get("as_at"), date.today())
    include_drafts = request.args.get("include_drafts") == "1"

    conn = get_conn()
    cursor = conn.cursor()
    try:
        rows = fetch_trial_balance_rows(cursor, org_id, as_at, include_drafts)
        unbalanced = fetch_unbalanced_entries(cursor, org_id, as_at, include_drafts)
    finally:
        cursor.close()
        conn.close()

    tb = compute_trial_balance(rows)
    return render_template(
        "reports/trial_balance.html",
        tb=tb,
        unbalanced=unbalanced,
        as_at=as_at,
        include_drafts=include_drafts,
    )
