import os
from flask import Blueprint, render_template, request, redirect, url_for, current_app, abort, flash
from werkzeug.utils import secure_filename
from core.db import get_conn
from core.context import current_organization_id, set_current_organization, require_organization
from auth.services import (
    require_permission,
    get_organization_users,
    add_organization_user,
    update_user_role,
    toggle_user_active,
    get_organization_roles,
    save_organization_role,
    current_user_id,
    require_platform_admin,
    is_platform_admin,
    user_belongs_to_organization,
)

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
    looking at. Verifies user membership or platform admin before switching.
    """
    uid = current_user_id()
    if not is_platform_admin() and not user_belongs_to_organization(uid, organization_id):
        abort(403, description="Access denied: You do not belong to this organization.")

    set_current_organization(organization_id)
    return redirect(request.referrer or url_for("dashboard.home"))


@bp.route("/new", methods=["GET", "POST"])
@require_platform_admin
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

        from auth.services import current_user_id
        uid = current_user_id()
        if uid:
            cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin'")
            role_row = cursor.fetchone()
            role_id = role_row[0] if role_row else 1
            cursor.execute("""
                INSERT INTO organization_memberships (organization_id, user_id, role_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (organization_id, user_id) DO NOTHING
            """, (new_id, uid, role_id))

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


@bp.route("/<int:organization_id>/users", methods=["GET"])
@require_organization
@require_permission("users.manage")
def users_list(organization_id):
    if organization_id != current_organization_id():
        abort(403)
    users = get_organization_users(organization_id)
    roles = get_organization_roles(organization_id)
    return render_template("organizations/users_list.html", users=users, roles=roles, organization_id=organization_id)


@bp.route("/<int:organization_id>/users/new", methods=["GET", "POST"])
@require_organization
@require_permission("users.manage")
def new_user(organization_id):
    if organization_id != current_organization_id():
        abort(403)
    roles = get_organization_roles(organization_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role_id = int(request.form.get("role_id", 0))

        if not name or not email or not role_id:
            flash("Name, email, and role are required.", "error")
            return render_template("organizations/new_user.html", roles=roles, organization_id=organization_id)

        try:
            add_organization_user(
                organization_id=organization_id,
                name=name,
                email=email,
                password=password,
                role_id=role_id,
                actor_user_id=current_user_id()
            )
            flash("User added successfully to the organization.", "success")
            return redirect(url_for("organizations.users_list", organization_id=organization_id))
        except Exception as e:
            flash(f"Error adding user: {e}", "error")

    return render_template("organizations/new_user.html", roles=roles, organization_id=organization_id)


@bp.route("/<int:organization_id>/users/<int:user_id>/role", methods=["POST"])
@require_organization
@require_permission("users.manage")
def change_user_role(organization_id, user_id):
    if organization_id != current_organization_id():
        abort(403)
    new_role_id = int(request.form.get("role_id", 0))
    try:
        update_user_role(
            organization_id=organization_id,
            target_user_id=user_id,
            new_role_id=new_role_id,
            actor_user_id=current_user_id()
        )
        flash("User role updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating role: {e}", "error")
    return redirect(url_for("organizations.users_list", organization_id=organization_id))


@bp.route("/<int:organization_id>/users/<int:user_id>/toggle", methods=["POST"])
@require_organization
@require_permission("users.manage")
def toggle_user(organization_id, user_id):
    if organization_id != current_organization_id():
        abort(403)
    try:
        toggle_user_active(
            organization_id=organization_id,
            target_user_id=user_id,
            actor_user_id=current_user_id()
        )
        flash("User status toggled successfully.", "success")
    except Exception as e:
        flash(f"Error toggling user status: {e}", "error")
    return redirect(url_for("organizations.users_list", organization_id=organization_id))


@bp.route("/<int:organization_id>/roles", methods=["GET"])
@require_organization
@require_permission("users.manage")
def roles_list(organization_id):
    if organization_id != current_organization_id():
        abort(403)
    roles = get_organization_roles(organization_id)
    return render_template("organizations/roles_list.html", roles=roles, organization_id=organization_id)


@bp.route("/<int:organization_id>/roles/new", methods=["GET", "POST"])
@require_organization
@require_permission("users.manage")
def new_role(organization_id):
    if organization_id != current_organization_id():
        abort(403)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT permission_id, code, name, module FROM permissions ORDER BY module, code")
    all_permissions = cursor.fetchall()
    cursor.close()
    conn.close()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        permission_ids = request.form.getlist("permission_ids")

        if not name:
            flash("Role name is required.", "error")
            return render_template("organizations/new_role.html", all_permissions=all_permissions, organization_id=organization_id, role=None, assigned_perm_ids=[])

        try:
            save_organization_role(
                organization_id=organization_id,
                role_id=None,
                name=name,
                description=description,
                permission_ids=permission_ids,
                actor_user_id=current_user_id()
            )
            flash("Custom role created successfully.", "success")
            return redirect(url_for("organizations.roles_list", organization_id=organization_id))
        except Exception as e:
            flash(f"Error creating role: {e}", "error")

    return render_template("organizations/new_role.html", all_permissions=all_permissions, organization_id=organization_id, role=None, assigned_perm_ids=[])


@bp.route("/<int:organization_id>/roles/<int:role_id>/edit", methods=["GET", "POST"])
@require_organization
@require_permission("users.manage")
def edit_role(organization_id, role_id):
    if organization_id != current_organization_id():
        abort(403)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT role_id, name, description, organization_id FROM roles WHERE role_id = %s", (role_id,))
    role = cursor.fetchone()
    if not role or role[3] is None:
        cursor.close()
        conn.close()
        abort(403, description="Cannot edit built-in system roles.")

    cursor.execute("SELECT permission_id, code, name, module FROM permissions ORDER BY module, code")
    all_permissions = cursor.fetchall()

    cursor.execute("SELECT permission_id FROM role_permissions WHERE role_id = %s", (role_id,))
    assigned_perm_ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        permission_ids = request.form.getlist("permission_ids")

        if not name:
            flash("Role name is required.", "error")
            return render_template("organizations/new_role.html", all_permissions=all_permissions, organization_id=organization_id, role=role, assigned_perm_ids=assigned_perm_ids)

        try:
            save_organization_role(
                organization_id=organization_id,
                role_id=role_id,
                name=name,
                description=description,
                permission_ids=permission_ids,
                actor_user_id=current_user_id()
            )
            flash("Custom role updated successfully.", "success")
            return redirect(url_for("organizations.roles_list", organization_id=organization_id))
        except Exception as e:
            flash(f"Error updating role: {e}", "error")

    return render_template("organizations/new_role.html", all_permissions=all_permissions, organization_id=organization_id, role=role, assigned_perm_ids=assigned_perm_ids)
