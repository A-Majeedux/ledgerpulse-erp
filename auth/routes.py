"""
auth/routes.py

Authentication routes: login, logout, and initial setup bootstrap.
"""
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from core.db import get_conn
from decimal import Decimal
from auth.services import (
    verify_password,
    hash_password,
    require_platform_admin,
    grant_platform_admin,
    revoke_platform_admin,
    deactivate_user,
    current_user_id,
    verify_organization_payment,
    reject_organization_payment,
    get_platform_analytics,
    get_platform_tenants,
    get_all_subscription_plans,
    save_subscription_plan,
    toggle_subscription_plan_active,
    get_zyro_organization_id,
    get_all_clients,
    create_client,
    get_client_details,
    get_client_businesses,
    create_business_for_client,
)

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_conn()
    cursor = conn.cursor()

    # Check if any users exist at all. If 0, redirect to setup.
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.close()
        conn.close()
        return redirect(url_for("auth.setup"))

    # If already logged in with a valid active user, redirect to dashboard or picker
    user_id = session.get("user_id")
    if user_id:
        cursor.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,))
        user_row = cursor.fetchone()
        if user_row and user_row[0]:
            cursor.close()
            conn.close()
            if session.get("organization_id"):
                return redirect(url_for("dashboard.home"))
            return redirect(url_for("organizations.pick_organization"))
        else:
            session.clear()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        cursor.execute("""
            SELECT user_id, name, email, password_hash, is_active, is_platform_admin
            FROM users WHERE email = %s
        """, (email,))
        user = cursor.fetchone()

        if user and user[4] and verify_password(user[3], password):
            # Success
            session["user_id"] = user[0]
            session["user_name"] = user[1]
            session["is_platform_admin"] = user[5]

            cursor.execute("UPDATE users SET last_login_at = %s WHERE user_id = %s", (datetime.now(timezone.utc), user[0]))
            conn.commit()

            if user[5]:
                session["organization_id"] = get_zyro_organization_id()
                cursor.close()
                conn.close()
                return redirect(url_for("dashboard.home"))

            # Find organizations user belongs to
            cursor.execute("""
                SELECT organization_id FROM organization_memberships WHERE user_id = %s
            """, (user[0],))
            org_rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(org_rows) == 1:
                session["organization_id"] = org_rows[0][0]
                return redirect(url_for("dashboard.home"))
            elif len(org_rows) > 1:
                return redirect(url_for("organizations.pick_organization"))
            else:
                return redirect(url_for("organizations.pick_organization"))
        else:
            flash("Invalid email or password, or account is inactive.", "error")

    cursor.close()
    conn.close()
    return render_template("auth/login.html")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("user_id", None)
    session.pop("user_name", None)
    session.pop("is_platform_admin", None)
    session.pop("organization_id", None)
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    conn = get_conn()
    cursor = conn.cursor()

    # If users already exist, setup is locked
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        cursor.close()
        conn.close()
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        org_name = request.form.get("organization_name", "").strip() or "My Organization"

        if not name or not email or not password:
            cursor.close()
            conn.close()
            flash("All fields are required for initial setup.", "error")
            return render_template("auth/setup.html")

        pwd_hash = hash_password(password)

        # Create master admin user
        cursor.execute("""
            INSERT INTO users (name, email, password_hash, is_platform_admin, is_active)
            VALUES (%s, %s, %s, TRUE, TRUE)
            RETURNING user_id
        """, (name, email, pwd_hash))
        user_id = cursor.fetchone()[0]

        # Ensure ZYRO organization exists
        cursor.execute("SELECT organization_id FROM organizations WHERE is_zyro = TRUE LIMIT 1")
        org_row = cursor.fetchone()
        if org_row:
            org_id = org_row[0]
        else:
            cursor.execute("""
                INSERT INTO organizations (name, is_zyro) VALUES ('ZYRO Head Office', TRUE) RETURNING organization_id
            """)
            org_id = cursor.fetchone()[0]
            cursor.execute("SELECT plan_id FROM subscription_plans LIMIT 1")
            plan_row = cursor.fetchone()
            if plan_row:
                cursor.execute("""
                    INSERT INTO organization_subscriptions (organization_id, plan_id, status, start_date, expiry_date, grace_end_date)
                    VALUES (%s, %s, 'ACTIVE', CURRENT_DATE, CURRENT_DATE + INTERVAL '365 days', CURRENT_DATE + INTERVAL '372 days')
                """, (org_id, plan_row[0]))

        # Assign Admin role to the user for this organization
        cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin'")
        role_row = cursor.fetchone()
        role_id = role_row[0] if role_row else 1

        cursor.execute("""
            INSERT INTO organization_memberships (organization_id, user_id, role_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (organization_id, user_id) DO NOTHING
        """, (org_id, user_id, role_id))

        conn.commit()
        cursor.close()
        conn.close()

        session["user_id"] = user_id
        session["user_name"] = name
        session["is_platform_admin"] = True
        session["organization_id"] = org_id

        flash("Initial administrator account created successfully!", "success")
        return redirect(url_for("dashboard.home"))

    cursor.close()
    conn.close()
    return render_template("auth/setup.html")


@bp.route("/admin/users", methods=["GET"])
@require_platform_admin
def admin_users_list():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, name, email, is_active, is_platform_admin, created_at FROM users ORDER BY user_id")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("auth/admin_users.html", users=users)


@bp.route("/admin/users/<int:user_id>/grant", methods=["POST"])
@require_platform_admin
def admin_grant_admin(user_id):
    try:
        grant_platform_admin(user_id, current_user_id())
        flash(f"User {user_id} granted platform admin privileges.", "success")
    except Exception as e:
        flash(f"Error granting platform admin: {e}", "error")
    return redirect(url_for("auth.admin_users_list"))


@bp.route("/admin/users/<int:user_id>/revoke", methods=["POST"])
@require_platform_admin
def admin_revoke_admin(user_id):
    try:
        revoke_platform_admin(user_id, current_user_id())
        flash(f"User {user_id} revoked platform admin privileges.", "success")
    except Exception as e:
        flash(f"Error revoking platform admin: {e}", "error")
    return redirect(url_for("auth.admin_users_list"))


@bp.route("/admin/users/<int:user_id>/deactivate", methods=["POST"])
@require_platform_admin
def admin_deactivate_user(user_id):
    try:
        deactivate_user(user_id, current_user_id())
        flash(f"User {user_id} deactivated successfully.", "success")
    except Exception as e:
        flash(f"Error deactivating user: {e}", "error")
    return redirect(url_for("auth.admin_users_list"))


@bp.route("/admin/payments", methods=["GET"])
@require_platform_admin
def admin_payments_list():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT op.payment_id, op.organization_id, org.name, op.amount, op.payment_date,
               op.status, op.payment_method, op.reference_number, op.notes, op.created_at,
               op.verified_by_user_id, op.verified_at
        FROM organization_payments op
        JOIN organizations org ON org.organization_id = op.organization_id
        ORDER BY op.payment_id DESC
    """)
    payments = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("auth/admin_payments.html", payments=payments)


@bp.route("/admin/payments/<int:payment_id>/verify", methods=["POST"])
@require_platform_admin
def admin_verify_payment(payment_id):
    try:
        verify_organization_payment(payment_id, current_user_id())
    except Exception as e:
        abort(400, description=f"Payment verification failed: {e}")
    return redirect(url_for("auth.admin_payments_list"))


@bp.route("/admin/payments/<int:payment_id>/reject", methods=["POST"])
@require_platform_admin
def admin_reject_payment(payment_id):
    reason = request.form.get("reason", "").strip()
    try:
        reject_organization_payment(payment_id, current_user_id(), reason=reason)
    except Exception as e:
        abort(400, description=f"Payment rejection failed: {e}")
    return redirect(url_for("auth.admin_payments_list"))


@bp.route("/admin", methods=["GET"])
@bp.route("/admin/dashboard", methods=["GET"])
@require_platform_admin
def admin_dashboard():
    analytics = get_platform_analytics()
    return render_template("auth/admin_dashboard.html", analytics=analytics)


@bp.route("/admin/tenants", methods=["GET"])
@require_platform_admin
def admin_tenants():
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "ALL").strip()
    tenants = get_platform_tenants(search=search, status_filter=status_filter)
    return render_template("auth/admin_tenants.html", tenants=tenants, search=search, status_filter=status_filter)


@bp.route("/admin/plans", methods=["GET"])
@require_platform_admin
def admin_plans():
    plans = get_all_subscription_plans()
    return render_template("auth/admin_plans.html", plans=plans)


@bp.route("/admin/plans/new", methods=["GET", "POST"])
@require_platform_admin
def admin_new_plan():
    available_modules = [
        ("core_accounting", "Core Accounting & General Ledger"),
        ("sales", "Sales & Invoicing"),
        ("customers", "Customers Management"),
        ("vendors", "Vendors & Purchases"),
        ("reports", "Financial Reports & Analytics"),
        ("wht", "Withholding & Sales Tax"),
        ("inventory", "Inventory & Stock"),
    ]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = Decimal(request.form.get("price", "0"))
        except Exception:
            price = Decimal("0.00")
        billing_cycle = request.form.get("billing_cycle", "monthly").strip()
        try:
            grace_period_days = int(request.form.get("grace_period_days", 7))
        except Exception:
            grace_period_days = 7
        is_active = bool(request.form.get("is_active"))
        modules = request.form.getlist("modules")

        if not name:
            flash("Plan name is required.", "error")
            return render_template("auth/admin_plan_form.html", plan=None, available_modules=available_modules)

        try:
            save_subscription_plan(
                plan_id=None,
                name=name,
                description=description,
                price=price,
                billing_cycle=billing_cycle,
                grace_period_days=grace_period_days,
                is_active=is_active,
                modules=modules,
                admin_user_id=current_user_id()
            )
            flash("Subscription plan created successfully.", "success")
            return redirect(url_for("auth.admin_plans"))
        except Exception as e:
            flash(f"Error creating plan: {e}", "error")

    return render_template("auth/admin_plan_form.html", plan=None, available_modules=available_modules)


@bp.route("/admin/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@require_platform_admin
def admin_edit_plan(plan_id):
    plans = get_all_subscription_plans()
    plan = next((p for p in plans if p["plan_id"] == plan_id), None)
    if not plan:
        abort(404, description="Plan not found.")

    available_modules = [
        ("core_accounting", "Core Accounting & General Ledger"),
        ("sales", "Sales & Invoicing"),
        ("customers", "Customers Management"),
        ("vendors", "Vendors & Purchases"),
        ("reports", "Financial Reports & Analytics"),
        ("wht", "Withholding & Sales Tax"),
        ("inventory", "Inventory & Stock"),
    ]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = Decimal(request.form.get("price", "0"))
        except Exception:
            price = Decimal("0.00")
        billing_cycle = request.form.get("billing_cycle", "monthly").strip()
        try:
            grace_period_days = int(request.form.get("grace_period_days", 7))
        except Exception:
            grace_period_days = 7
        is_active = bool(request.form.get("is_active"))
        modules = request.form.getlist("modules")

        if not name:
            flash("Plan name is required.", "error")
            return render_template("auth/admin_plan_form.html", plan=plan, available_modules=available_modules)

        try:
            save_subscription_plan(
                plan_id=plan_id,
                name=name,
                description=description,
                price=price,
                billing_cycle=billing_cycle,
                grace_period_days=grace_period_days,
                is_active=is_active,
                modules=modules,
                admin_user_id=current_user_id()
            )
            flash("Subscription plan updated successfully.", "success")
            return redirect(url_for("auth.admin_plans"))
        except Exception as e:
            flash(f"Error updating plan: {e}", "error")

    return render_template("auth/admin_plan_form.html", plan=plan, available_modules=available_modules)


@bp.route("/admin/plans/<int:plan_id>/toggle", methods=["POST"])
@require_platform_admin
def admin_toggle_plan(plan_id):
    try:
        toggle_subscription_plan_active(plan_id, current_user_id())
        flash("Subscription plan status toggled.", "success")
    except Exception as e:
        flash(f"Error toggling plan: {e}", "error")
    return redirect(url_for("auth.admin_plans"))


@bp.route("/admin/clients", methods=["GET"])
@require_platform_admin
def admin_clients_list():
    search = request.args.get("search", "").strip()
    clients = get_all_clients(search=search)
    return render_template("auth/admin_clients.html", clients=clients, search=search)


@bp.route("/admin/clients/new", methods=["GET", "POST"])
@require_platform_admin
def admin_new_client():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        if not name:
            flash("Client name is required.", "error")
            return render_template("auth/admin_client_form.html", client=None)

        try:
            client_id = create_client(name, email, phone, address, current_user_id())
            flash("Client registered successfully.", "success")
            return redirect(url_for("auth.admin_client_detail", client_id=client_id))
        except Exception as e:
            flash(f"Error registering client: {e}", "error")

    return render_template("auth/admin_client_form.html", client=None)


@bp.route("/admin/clients/<int:client_id>", methods=["GET", "POST"])
@require_platform_admin
def admin_client_detail(client_id):
    client = get_client_details(client_id)
    if not client:
        abort(404)
    businesses = get_client_businesses(client_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        conn = get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE clients SET name = %s, email = %s, phone = %s, address = %s
                WHERE client_id = %s
            """, (name, email or None, phone or None, address or None, client_id))
            conn.commit()
            flash("Client details updated successfully.", "success")
            return redirect(url_for("auth.admin_client_detail", client_id=client_id))
        except Exception as e:
            conn.rollback()
            flash(f"Error updating client: {e}", "error")
        finally:
            cursor.close()
            conn.close()

    return render_template("auth/admin_client_detail.html", client=client, businesses=businesses)


@bp.route("/admin/clients/<int:client_id>/businesses/new", methods=["GET", "POST"])
@require_platform_admin
def admin_client_new_business(client_id):
    client = get_client_details(client_id)
    if not client:
        abort(404)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT plan_id, name FROM subscription_plans WHERE is_active = TRUE")
    plans = cursor.fetchall()
    cursor.close()
    conn.close()

    MONTH_NAMES = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        currency = request.form.get("currency", "PKR").strip()
        nature_of_business = request.form.get("nature_of_business", "").strip()
        ntn = request.form.get("ntn", "").strip()
        strn = request.form.get("strn", "").strip()
        head_office_address = request.form.get("head_office_address", "").strip()
        warehouse_address = request.form.get("warehouse_address", "").strip()
        fiscal_year_start_month = int(request.form.get("fiscal_year_start_month", 7))

        if not name:
            flash("Business name is required.", "error")
            return render_template("auth/admin_business_form.html", client=client, plans=plans, month_names=MONTH_NAMES)

        try:
            org_id = create_business_for_client(
                client_id=client_id,
                name=name,
                currency=currency,
                nature_of_business=nature_of_business,
                ntn=ntn,
                strn=strn,
                head_office_address=head_office_address,
                warehouse_address=warehouse_address,
                fiscal_year_start_month=fiscal_year_start_month,
                admin_user_id=current_user_id()
            )
            flash("Client business created successfully.", "success")
            return redirect(url_for("auth.admin_client_detail", client_id=client_id))
        except Exception as e:
            flash(f"Error creating business: {e}", "error")

    return render_template("auth/admin_business_form.html", client=client, plans=plans, month_names=MONTH_NAMES)


@bp.route("/admin/support-org/<int:organization_id>", methods=["POST", "GET"])
@require_platform_admin
def admin_support_org(organization_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT organization_id, name FROM organizations WHERE organization_id = %s", (organization_id,))
    org = cursor.fetchone()
    cursor.close()
    conn.close()

    if not org:
        abort(404)

    session["organization_id"] = organization_id
    flash(f"Entered Support Mode for business: {org[1]}", "success")
    return redirect(url_for("dashboard.home"))


@bp.route("/admin/return-to-zyro", methods=["POST", "GET"])
@require_platform_admin
def admin_return_to_zyro():
    zyro_id = get_zyro_organization_id()
    session["organization_id"] = zyro_id
    flash("Returned to ZYRO Head Office workspace.", "success")
    return redirect(url_for("dashboard.home"))
