import os
from flask import Blueprint, render_template, request, redirect, url_for, current_app, abort
from werkzeug.utils import secure_filename
from core.db import get_conn
from core.context import current_organization_id, set_current_organization, require_organization

bp = Blueprint("organizations", __name__, url_prefix="/organizations")

UPLOAD_FOLDER = os.path.join("static", "uploads")

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

# The full column order used every time we SELECT an organization's
# details, so routes/templates stay in sync by position.
ORG_COLUMNS = """
    organization_id, name, currency, nature_of_business, ntn, strn,
    head_office_address, warehouse_address, logo_path,
    fiscal_year_start_month, created_at
"""


def _save_logo(file_storage):
    """
    Saves an uploaded logo file (if one was actually provided) and returns
    the relative path to store in the database, or None if no file was
    uploaded. Keeping this in one place means every route that needs to
    handle a logo upload does it the same way.
    """
    if not file_storage or file_storage.filename == "":
        return None
    filename = secure_filename(file_storage.filename)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file_storage.save(save_path)
    return save_path


@bp.route("/pick")
def pick_organization():
    """
    Shown whenever nobody has an active organization selected yet - e.g.
    right after logging in, or the very first time the app is opened.
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {ORG_COLUMNS} FROM organizations ORDER BY name")
    organizations = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("organizations/pick.html", organizations=organizations)


@bp.route("/switch/<int:organization_id>", methods=["POST"])
def switch_organization(organization_id):
    """
    Called from the organization-switcher dropdown in the top bar (see
    base.html) to change which company's books the user is currently
    looking at. Verifies user membership before switching.
    """
    from auth.services import user_belongs_to_organization, current_user_id
    uid = current_user_id()
    if not user_belongs_to_organization(uid, organization_id):
        abort(403, description="Access denied: You do not belong to this organization.")

    set_current_organization(organization_id)
    return redirect(request.referrer or url_for("dashboard.home"))


@bp.route("/new", methods=["GET", "POST"])
def new_organization():
    if request.method == "POST":
        logo_path = _save_logo(request.files.get("logo"))
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO organizations (
                name, currency, nature_of_business, ntn, strn,
                head_office_address, warehouse_address, logo_path,
                fiscal_year_start_month
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING organization_id
        """, (
            request.form["name"].strip(),
            request.form.get("currency", "PKR").strip() or "PKR",
            request.form.get("nature_of_business") or None,
            request.form.get("ntn", "").strip() or None,
            request.form.get("strn", "").strip() or None,
            request.form.get("head_office_address", "").strip() or None,
            request.form.get("warehouse_address", "").strip() or None,
            logo_path,
            int(request.form.get("fiscal_year_start_month", 7)),
        ))
        new_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        set_current_organization(new_id)
        return redirect(url_for("dashboard.home"))
    return render_template("organizations/new.html", month_names=MONTH_NAMES)


@bp.route("/<int:organization_id>/edit", methods=["GET", "POST"])
def edit_organization(organization_id):
    if organization_id != current_organization_id():
        abort(404)
    conn = get_conn()
    cursor = conn.cursor()

    if request.method == "POST":
        new_logo_path = _save_logo(request.files.get("logo"))
        if new_logo_path is None:
            # No new logo uploaded - keep whatever logo it already has.
            cursor.execute(
                "SELECT logo_path FROM organizations WHERE organization_id = %s",
                (organization_id,)
            )
            new_logo_path = cursor.fetchone()[0]

        cursor.execute("""
            UPDATE organizations SET
                name = %s, currency = %s, nature_of_business = %s,
                ntn = %s, strn = %s, head_office_address = %s,
                warehouse_address = %s, logo_path = %s,
                fiscal_year_start_month = %s
            WHERE organization_id = %s
        """, (
            request.form["name"].strip(),
            request.form.get("currency", "PKR").strip() or "PKR",
            request.form.get("nature_of_business") or None,
            request.form.get("ntn", "").strip() or None,
            request.form.get("strn", "").strip() or None,
            request.form.get("head_office_address", "").strip() or None,
            request.form.get("warehouse_address", "").strip() or None,
            new_logo_path,
            int(request.form.get("fiscal_year_start_month", 7)),
            organization_id,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("organizations.manage_organizations"))

    cursor.execute(f"SELECT {ORG_COLUMNS} FROM organizations WHERE organization_id = %s", (organization_id,))
    org = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template("organizations/edit.html", org=org, month_names=MONTH_NAMES)


@bp.route("/manage")
def manage_organizations():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {ORG_COLUMNS} FROM organizations ORDER BY name")
    organizations = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template(
        "organizations/manage.html",
        organizations=organizations,
        active_id=current_organization_id(),
        month_names=MONTH_NAMES,
    )


@bp.route("/subscription/renew", methods=["GET", "POST"])
@require_organization
def renew_subscription():
    from decimal import Decimal
    from auth.services import submit_organization_payment, check_subscription, current_user_id
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()

    if request.method == "POST":
        amount = Decimal(request.form.get("amount", "0"))
        payment_method = request.form.get("payment_method", "Bank Transfer").strip()
        reference_number = request.form.get("reference_number", "").strip()
        notes = request.form.get("notes", "").strip() or None
        plan_id = int(request.form.get("plan_id", 1))

        if amount <= 0 or not reference_number:
            conn.close()
            abort(400, description="Invalid payment amount or reference number.")

        try:
            submit_organization_payment(
                organization_id=org_id,
                user_id=current_user_id(),
                plan_id=plan_id,
                amount=amount,
                payment_method=payment_method,
                reference_number=reference_number,
                notes=notes
            )
            return redirect(url_for("organizations.renew_subscription"))
        except Exception as e:
            abort(400, description=f"Payment submission failed: {e}")

    cursor.execute("SELECT plan_id, name, price, billing_cycle FROM subscription_plans WHERE is_active = TRUE")
    plans = cursor.fetchall()

    sub = check_subscription(org_id)

    cursor.execute("""
        SELECT payment_id, amount, payment_date, status, payment_method, reference_number, notes, created_at
        FROM organization_payments
        WHERE organization_id = %s
        ORDER BY payment_id DESC
    """, (org_id,))
    payments = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("organizations/renew.html", sub=sub, plans=plans, payments=payments)
