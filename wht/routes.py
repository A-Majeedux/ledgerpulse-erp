from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from core.money import to_decimal

bp = Blueprint("wht", __name__, url_prefix="/tax-rates")


def get_active_tax_rates(org_id):
    """
    Shared helper other segments (vendors, and later customers) import
    to populate their WHT rate dropdown. Keeping this here means the
    "what counts as an active rate" rule lives in exactly one place.
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tax_rate_id, label, rate_percent FROM tax_rates
        WHERE organization_id = %s AND is_active = TRUE
        ORDER BY rate_percent
    """, (org_id,))
    rates = cursor.fetchall()
    cursor.close()
    conn.close()
    return rates


@bp.route("/")
@require_organization
def tax_rates_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tax_rate_id, label, rate_percent, is_active FROM tax_rates
        WHERE organization_id = %s ORDER BY rate_percent
    """, (org_id,))
    rates = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("wht/tax_rates_list.html", rates=rates)


@bp.route("/new", methods=["GET", "POST"])
@require_organization
def new_tax_rate():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tax_rates (organization_id, label, rate_percent)
            VALUES (%s, %s, %s)
        """, (org_id, request.form["label"].strip(), to_decimal(request.form["rate_percent"])))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("wht.tax_rates_list"))
    return render_template("wht/new_tax_rate.html")


@bp.route("/<int:tax_rate_id>/toggle", methods=["POST"])
@require_organization
def toggle_tax_rate(tax_rate_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM tax_rates WHERE tax_rate_id = %s AND organization_id = %s",
        (tax_rate_id, org_id)
    )
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute(
        "UPDATE tax_rates SET is_active = NOT is_active WHERE tax_rate_id = %s AND organization_id = %s",
        (tax_rate_id, org_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for("wht.tax_rates_list"))
