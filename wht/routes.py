from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from core.money import to_decimal
from core.validation import ValidationError, parse_percent, require_text

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


def pick_wht_rate(org_id, raw_rate):
    """
    Shared helper for every screen that has a "WHT rate" dropdown (Vendor
    Payments, Customer Receipts). Takes the value the form sent and returns
    the rate as a Decimal - but only if it really is one of this
    organization's ACTIVE rates on the Tax Rates screen.

    This is what enforces the rule "the rate is always picked from the
    master list": a blank choice, a retired rate, or a figure that was
    never on the list is refused with a core.validation.ValidationError
    instead of being posted to the ledger.
    """
    if not (raw_rate or "").strip():
        raise ValidationError(
            "Withholding tax is ticked - pick the WHT rate that applies, or untick it."
        )
    chosen = parse_percent(raw_rate, "WHT rate")
    for _tax_rate_id, _label, active_rate in get_active_tax_rates(org_id):
        if to_decimal(active_rate) == chosen:
            return to_decimal(active_rate)
    raise ValidationError(
        f"WHT rate {raw_rate.strip()}% is not an active rate on the Tax Rates list. "
        "Add it there first, then pick it here."
    )


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
        # The form's own number box (0 to 100) stops these in the browser;
        # this is the check that holds if the browser is bypassed.
        try:
            label = require_text(request.form.get("label"), "Label", max_length=100)
            rate_percent = parse_percent(request.form.get("rate_percent"), "Rate (%)")
        except ValidationError as error:
            abort(400, description=str(error))

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tax_rates (organization_id, label, rate_percent)
            VALUES (%s, %s, %s)
        """, (org_id, label, rate_percent))
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
