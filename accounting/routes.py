from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.schema import ACCOUNT_TYPES
from accounting.services import create_journal_entry, post_journal_entry, UnbalancedEntryError
from core.money import to_decimal

bp = Blueprint("accounting", __name__, url_prefix="/accounting")


# ---------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------

@bp.route("/chart-of-accounts")
@require_organization
def chart_of_accounts_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name, account_type, allow_reconciliation, is_active
        FROM chart_of_accounts
        WHERE organization_id = %s
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template(
        "accounting/chart_of_accounts.html",
        accounts=accounts,
        account_types=ACCOUNT_TYPES,
    )


@bp.route("/chart-of-accounts/new", methods=["GET", "POST"])
@require_organization
def new_account():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chart_of_accounts
                (organization_id, code, name, account_type, allow_reconciliation)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["code"].strip(),
            request.form["name"].strip(),
            request.form["account_type"],
            "allow_reconciliation" in request.form,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("accounting.chart_of_accounts_list"))
    return render_template("accounting/new_account.html", account_types=ACCOUNT_TYPES)


# ---------------------------------------------------------------------
# Journal Entries
# ---------------------------------------------------------------------

@bp.route("/journal-entries")
@require_organization
def journal_entries_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            jh.journal_header_id, jh.reference, jh.accounting_date,
            jh.journal_name, jh.status,
            COALESCE(SUM(jl.debit), 0) AS total
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE jh.organization_id = %s
        GROUP BY jh.journal_header_id
        ORDER BY jh.accounting_date DESC, jh.journal_header_id DESC
    """, (org_id,))
    entries = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("accounting/journal_entries_list.html", entries=entries)


@bp.route("/journal-entries/new", methods=["GET", "POST"])
@require_organization
def new_journal_entry():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()

    if request.method == "POST":
        account_ids = request.form.getlist("account_id[]")
        partner_names = request.form.getlist("partner_name[]")
        labels = request.form.getlist("label[]")
        debits = request.form.getlist("debit[]")
        credits = request.form.getlist("credit[]")

        lines = []
        for i in range(len(account_ids)):
            if not account_ids[i]:
                continue  # skip blank rows left over from the dynamic grid
            lines.append({
                "account_id": int(account_ids[i]),
                "partner_name": partner_names[i].strip() or None,
                "label": labels[i].strip() or None,
                "debit": to_decimal(debits[i] or 0),
                "credit": to_decimal(credits[i] or 0),
            })

        try:
            journal_header_id = create_journal_entry(
                cursor,
                organization_id=org_id,
                reference=request.form.get("reference", "").strip() or None,
                accounting_date=request.form["accounting_date"],
                journal_name=request.form.get("journal_name", "Miscellaneous Operations").strip(),
                lines=lines,
                source_type="Manual",
                auto_post=False,
            )
            conn.commit()
        except UnbalancedEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "accounting/new_journal_entry.html",
                accounts=_get_accounts(org_id),
                error=str(error),
                form=request.form,
            )

        cursor.close()
        conn.close()
        return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))

    accounts = _get_accounts(org_id)
    cursor.close()
    conn.close()
    return render_template(
        "accounting/new_journal_entry.html",
        accounts=accounts,
        error=None,
        form=None,
        today=date.today().isoformat(),
    )


@bp.route("/journal-entries/<int:journal_header_id>")
@require_organization
def journal_entry_detail(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT journal_header_id, reference, accounting_date, journal_name, status
        FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s
    """, (journal_header_id, org_id))
    header = cursor.fetchone()
    if not header:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT coa.code, coa.name, jl.partner_name, jl.label, jl.debit, jl.credit
        FROM journal_lines jl
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jl.journal_header_id = %s
        ORDER BY jl.line_order
    """, (journal_header_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total_debit = sum(float(line[4]) for line in lines)
    total_credit = sum(float(line[5]) for line in lines)

    return render_template(
        "accounting/journal_entry_detail.html",
        header=header,
        lines=lines,
        total_debit=total_debit,
        total_credit=total_credit,
    )


@bp.route("/journal-entries/<int:journal_header_id>/post", methods=["POST"])
@require_organization
def post_journal_entry_route(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s",
        (journal_header_id, org_id)
    )
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        abort(404)
    try:
        post_journal_entry(cursor, journal_header_id)
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))


def _get_accounts(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return accounts
