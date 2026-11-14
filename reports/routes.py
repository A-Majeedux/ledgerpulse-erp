from datetime import date, datetime

from flask import Blueprint, abort, render_template, request

from core.db import get_conn
from core.context import current_organization_id, require_organization
from reports.services import (
    fetch_trial_balance_rows,
    fetch_unbalanced_entries,
    compute_trial_balance,
)
from reports.gl_services import (
    fetch_accounts,
    fetch_account,
    fetch_opening_totals,
    fetch_ledger_lines,
    compute_general_ledger,
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


@bp.route("/general-ledger")
@require_organization
def general_ledger():
    org_id = current_organization_id()
    from_date = _parse_date(request.args.get("from"), None)
    to_date = _parse_date(request.args.get("to"), date.today())
    include_drafts = request.args.get("include_drafts") == "1"

    account_id_raw = request.args.get("account_id", "").strip()
    account_id = int(account_id_raw) if account_id_raw.isdigit() else None

    conn = get_conn()
    cursor = conn.cursor()
    try:
        accounts = fetch_accounts(cursor, org_id)
        account = None
        gl = None
        if account_id is not None:
            account = fetch_account(cursor, org_id, account_id)
            if account is None:
                abort(404)  # not an account of this organization
            opening_debit, opening_credit = fetch_opening_totals(
                cursor, org_id, account_id, from_date, include_drafts
            )
            rows = fetch_ledger_lines(
                cursor, org_id, account_id, from_date, to_date, include_drafts
            )
            gl = compute_general_ledger(opening_debit, opening_credit, rows)
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "reports/general_ledger.html",
        accounts=accounts,
        account=account,
        gl=gl,
        from_date=from_date,
        to_date=to_date,
        include_drafts=include_drafts,
    )
