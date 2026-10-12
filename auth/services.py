"""
auth/services.py

Authentication, RBAC authorization, subscription checks, and CSRF utilities.
"""
import secrets
from decimal import Decimal
from datetime import date, timedelta
from flask import session, request, abort
from werkzeug.security import generate_password_hash, check_password_hash
from core.db import get_conn


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def current_user_id():
    return session.get("user_id")


def current_user_name():
    return session.get("user_name")


def is_platform_admin():
    uid = current_user_id()
    if not uid:
        return False
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT is_platform_admin, is_active FROM users WHERE user_id = %s", (uid,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return bool(row and row[0] and row[1])
    except Exception:
        return False


def user_belongs_to_organization(user_id, organization_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM organization_memberships
        WHERE user_id = %s AND organization_id = %s
    """, (user_id, organization_id))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return bool(row)


from functools import wraps

def require_platform_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        uid = current_user_id()
        if not uid or not is_platform_admin():
            abort(403, description="Access denied: Platform Administrator privileges required.")
        return f(*args, **kwargs)
    return decorated_function


def has_permission(user_id, organization_id, permission_code):
    if not user_id or not organization_id:
        return False

    conn = get_conn()
    cursor = conn.cursor()
    # Verify user is active
    cursor.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,))
    user_row = cursor.fetchone()
    if not user_row or not user_row[0]:
        cursor.close()
        conn.close()
        return False

    cursor.execute("""
        SELECT 1 FROM organization_memberships om
        JOIN role_permissions rp ON rp.role_id = om.role_id
        JOIN permissions p ON p.permission_id = rp.permission_id
        WHERE om.user_id = %s AND om.organization_id = %s AND p.code = %s
    """, (user_id, organization_id, permission_code))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return bool(row)


def grant_platform_admin(target_user_id, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, is_platform_admin, is_active FROM users WHERE user_id = %s FOR UPDATE", (target_user_id,))
        target = cursor.fetchone()
        if not target:
            raise ValueError("Target user not found.")

        cursor.execute("""
            UPDATE users SET is_platform_admin = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (target_user_id,))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (actor_user_id, "GRANT_PLATFORM_ADMIN", f"Granted platform admin privileges to user_id {target_user_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def revoke_platform_admin(target_user_id, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        # Lock all active platform admins to prevent race conditions
        cursor.execute("""
            SELECT user_id FROM users WHERE is_platform_admin = TRUE AND is_active = TRUE FOR UPDATE
        """)
        active_admins = cursor.fetchall()
        active_admin_ids = [row[0] for row in active_admins]

        if target_user_id in active_admin_ids and len(active_admin_ids) <= 1:
            raise ValueError("Cannot revoke platform admin privileges: this is the last active platform admin.")

        cursor.execute("""
            UPDATE users SET is_platform_admin = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (target_user_id,))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (actor_user_id, "REVOKE_PLATFORM_ADMIN", f"Revoked platform admin privileges from user_id {target_user_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def deactivate_user(target_user_id, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_platform_admin, is_active FROM users WHERE user_id = %s FOR UPDATE", (target_user_id,))
        target = cursor.fetchone()
        if not target:
            raise ValueError("Target user not found.")

        if target[0] and target[1]: # is_platform_admin and is_active
            cursor.execute("SELECT user_id FROM users WHERE is_platform_admin = TRUE AND is_active = TRUE FOR UPDATE")
            active_admins = cursor.fetchall()
            active_admin_ids = [row[0] for row in active_admins]
            if target_user_id in active_admin_ids and len(active_admin_ids) <= 1:
                raise ValueError("Cannot deactivate user: this is the last active platform admin.")

        cursor.execute("""
            UPDATE users SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (target_user_id,))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (actor_user_id, "DEACTIVATE_USER", f"Deactivated user_id {target_user_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def check_subscription(organization_id):
    """
    Returns subscription dict or status for the organization.
    Statuses: TRIAL, PENDING_PAYMENT, ACTIVE, GRACE_PERIOD, SUSPENDED, CANCELLED, EXPIRED
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT os.subscription_id, os.status, os.expiry_date, os.grace_end_date, sp.name
        FROM organization_subscriptions os
        JOIN subscription_plans sp ON sp.plan_id = os.plan_id
        WHERE os.organization_id = %s
        ORDER BY os.subscription_id DESC
        LIMIT 1
    """, (organization_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return {"status": "ACTIVE", "plan_name": "Default"}
    return {
        "subscription_id": row[0],
        "status": row[1],
        "expiry_date": row[2],
        "grace_end_date": row[3],
        "plan_name": row[4],
    }


def has_feature(organization_id, module_code):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM organization_subscriptions os
        JOIN plan_features pf ON pf.plan_id = os.plan_id
        WHERE os.organization_id = %s AND pf.module_code = %s AND os.status IN ('TRIAL', 'ACTIVE', 'GRACE_PERIOD')
    """, (organization_id, module_code))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return bool(row)


def generate_csrf_token():
    try:
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return session["csrf_token"]
    except RuntimeError:
        return "test-csrf-token-outside-context-32bytes"


def validate_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf_token"):
            abort(400, description="CSRF token missing or invalid.")


def submit_organization_payment(organization_id, user_id, plan_id, amount, payment_method, reference_number, notes, subscription_id=None, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT price, is_active FROM subscription_plans WHERE plan_id = %s", (plan_id,))
        plan = cursor.fetchone()
        if not plan:
            raise ValueError("Selected subscription plan not found.")
        if not plan[1]:
            raise ValueError("Selected subscription plan is inactive.")

        plan_price = Decimal(str(plan[0]))
        submitted_amount = Decimal(str(amount))
        if submitted_amount != plan_price:
            raise ValueError(f"Payment amount (${submitted_amount}) does not match configured plan price (${plan_price}).")

        cursor.execute("""
            INSERT INTO organization_payments
                (organization_id, subscription_id, plan_id, amount, payment_method, reference_number, notes, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')
            RETURNING payment_id
        """, (organization_id, subscription_id, plan_id, submitted_amount, payment_method, reference_number, notes))
        payment_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, organization_id, "SUBMIT_PAYMENT", f"Submitted payment_id {payment_id} for plan_id {plan_id}, amount {submitted_amount} (ref: {reference_number})", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return payment_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def verify_organization_payment(payment_id, admin_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT payment_id, organization_id, subscription_id, plan_id, amount, status
            FROM organization_payments
            WHERE payment_id = %s FOR UPDATE
        """, (payment_id,))
        payment = cursor.fetchone()
        if not payment:
            raise ValueError("Payment record not found.")
        
        pay_id, org_id, sub_id, pay_plan_id, amount, status = payment
        if status != "PENDING":
            raise ValueError(f"Payment is already processed (Status: {status}).")

        cursor.execute("""
            UPDATE organization_payments
            SET status = 'VERIFIED', verified_by_user_id = %s, verified_at = CURRENT_TIMESTAMP
            WHERE payment_id = %s
        """, (admin_user_id, payment_id))

        target_plan_id = pay_plan_id
        if not target_plan_id:
            cursor.execute("""
                SELECT plan_id FROM organization_subscriptions
                WHERE organization_id = %s ORDER BY subscription_id DESC LIMIT 1
            """, (org_id,))
            sub_p = cursor.fetchone()
            if sub_p:
                target_plan_id = sub_p[0]
            else:
                cursor.execute("SELECT plan_id FROM subscription_plans LIMIT 1")
                def_p = cursor.fetchone()
                target_plan_id = def_p[0] if def_p else 1

        cursor.execute("SELECT billing_cycle FROM subscription_plans WHERE plan_id = %s", (target_plan_id,))
        plan_cycle = cursor.fetchone()
        cycle = plan_cycle[0] if plan_cycle else 'monthly'
        days = 365 if cycle in ('annual', 'yearly') else 30

        cursor.execute("""
            SELECT subscription_id, expiry_date FROM organization_subscriptions
            WHERE organization_id = %s
            ORDER BY subscription_id DESC LIMIT 1
        """, (org_id,))
        sub_row = cursor.fetchone()

        if sub_row:
            s_id, current_expiry = sub_row
            base_date = current_expiry if (current_expiry and current_expiry >= date.today()) else date.today()
            new_expiry = base_date + timedelta(days=days)
            grace_end = new_expiry + timedelta(days=7)

            cursor.execute("""
                UPDATE organization_subscriptions
                SET status = 'ACTIVE', plan_id = %s, expiry_date = %s, grace_end_date = %s
                WHERE subscription_id = %s
            """, (target_plan_id, new_expiry, grace_end, s_id))
            sub_id_to_link = s_id
        else:
            new_expiry = date.today() + timedelta(days=days)
            grace_end = new_expiry + timedelta(days=7)
            cursor.execute("""
                INSERT INTO organization_subscriptions (organization_id, plan_id, status, start_date, expiry_date, grace_end_date)
                VALUES (%s, %s, 'ACTIVE', CURRENT_DATE, %s, %s)
                RETURNING subscription_id
            """, (org_id, target_plan_id, new_expiry, grace_end))
            sub_id_to_link = cursor.fetchone()[0]

        cursor.execute("""
            UPDATE organization_payments SET subscription_id = %s WHERE payment_id = %s
        """, (sub_id_to_link, payment_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (admin_user_id, org_id, "VERIFY_PAYMENT", f"Verified payment_id {payment_id} for plan_id {target_plan_id}; activated/renewed subscription {sub_id_to_link}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def reject_organization_payment(payment_id, admin_user_id, reason=None, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT payment_id, organization_id, status FROM organization_payments
            WHERE payment_id = %s FOR UPDATE
        """, (payment_id,))
        payment = cursor.fetchone()
        if not payment:
            raise ValueError("Payment record not found.")
        
        pay_id, org_id, status = payment
        if status != "PENDING":
            raise ValueError(f"Payment is already processed (Status: {status}).")

        cursor.execute("""
            UPDATE organization_payments
            SET status = 'REJECTED', verified_by_user_id = %s, verified_at = CURRENT_TIMESTAMP, notes = COALESCE(notes, '') || %s
            WHERE payment_id = %s
        """, (admin_user_id, f" [Rejected: {reason or 'No reason provided'}]", payment_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (admin_user_id, org_id, "REJECT_PAYMENT", f"Rejected payment_id {payment_id}. Reason: {reason or 'None'}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
