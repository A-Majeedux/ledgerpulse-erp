"""
auth/routes.py

Authentication routes: login, logout, and initial setup bootstrap.
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from core.db import get_conn
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
)

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, redirect to dashboard or picker
    if session.get("user_id"):
        if session.get("organization_id"):
            return redirect(url_for("dashboard.home"))
        return redirect(url_for("organizations.pick_organization"))

    conn = get_conn()
    cursor = conn.cursor()

    # Check if any users exist at all. If 0, redirect to setup.
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.close()
        conn.close()
        return redirect(url_for("auth.setup"))

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

            cursor.execute("UPDATE users SET last_login_at = %s WHERE user_id = %s", (datetime.utcnow(), user[0]))
            conn.commit()

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
                # Platform admin or user with no orgs yet
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

        # Check if an organization already exists (legacy), or create a new one
        cursor.execute("SELECT organization_id FROM organizations ORDER BY organization_id LIMIT 1")
        org_row = cursor.fetchone()
        if org_row:
            org_id = org_row[0]
        else:
            cursor.execute("""
                INSERT INTO organizations (name) VALUES (%s) RETURNING organization_id
            """, (org_name,))
            org_id = cursor.fetchone()[0]
            # Ensure subscription for new org
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
