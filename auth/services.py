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
    
    sub_id, status, expiry_date, grace_end_date, plan_name = row
    
    # Dynamic date-based lifecycle check if active/trial
    if status in ("ACTIVE", "TRIAL") and expiry_date:
        today = date.today()
        if today > expiry_date:
            if grace_end_date and today <= grace_end_date:
                status = "GRACE_PERIOD"
            else:
                status = "SUSPENDED"

    return {
        "subscription_id": sub_id,
        "status": status,
        "expiry_date": expiry_date,
        "grace_end_date": grace_end_date,
        "plan_name": plan_name,
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


def require_feature(module_code):
    from functools import wraps
    from core.context import current_organization_id
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            uid = current_user_id()
            org_id = current_organization_id()
            if not uid or not org_id:
                abort(401, description="Authentication and active organization required.")
            if is_platform_admin():
                return f(*args, **kwargs)
            if not has_feature(org_id, module_code):
                abort(403, description=f"Access denied: Subscription plan does not include module '{module_code}'.")
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission(permission_code):
    from functools import wraps
    from core.context import current_organization_id
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            uid = current_user_id()
            org_id = current_organization_id()
            if not uid or not org_id:
                abort(401, description="Authentication and active organization required.")
            if is_platform_admin():
                return f(*args, **kwargs)
            if not has_permission(uid, org_id, permission_code):
                abort(403, description=f"Access denied: Missing required permission '{permission_code}'.")
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def is_last_active_org_admin(organization_id, user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND (organization_id IS NULL OR organization_id = %s)", (organization_id,))
    admin_roles = [r[0] for r in cursor.fetchall()]
    if not admin_roles:
        cursor.close()
        conn.close()
        return False
    
    cursor.execute("""
        SELECT 1 FROM organization_memberships om
        JOIN users u ON u.user_id = om.user_id
        WHERE om.organization_id = %s AND om.user_id = %s AND om.role_id = ANY(%s) AND u.is_active = TRUE
    """, (organization_id, user_id, admin_roles))
    is_admin = cursor.fetchone()
    if not is_admin:
        cursor.close()
        conn.close()
        return False

    cursor.execute("""
        SELECT COUNT(DISTINCT om.user_id) FROM organization_memberships om
        JOIN users u ON u.user_id = om.user_id
        WHERE om.organization_id = %s AND om.role_id = ANY(%s) AND u.is_active = TRUE
    """, (organization_id, admin_roles))
    admin_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return admin_count <= 1


def get_organization_users(organization_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.user_id, u.name, u.email, u.is_active, r.role_id, r.name AS role_name, om.membership_id, u.created_at
        FROM organization_memberships om
        JOIN users u ON u.user_id = om.user_id
        JOIN roles r ON r.role_id = om.role_id
        WHERE om.organization_id = %s
        ORDER BY u.name
    """, (organization_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def add_organization_user(organization_id, name, email, password, role_id, actor_user_id, ip_address=None):
    email = email.strip().lower()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, password_hash, is_active FROM users WHERE email = %s FOR UPDATE", (email,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
        else:
            if not password:
                raise ValueError("Password is required for new users.")
            pwd_hash = hash_password(password)
            cursor.execute("""
                INSERT INTO users (name, email, password_hash, is_active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING user_id
            """, (name.strip(), email, pwd_hash))
            user_id = cursor.fetchone()[0]

        cursor.execute("SELECT role_id FROM roles WHERE role_id = %s AND (organization_id IS NULL OR organization_id = %s)", (role_id, organization_id))
        if not cursor.fetchone():
            raise ValueError("Invalid role selected.")

        cursor.execute("""
            INSERT INTO organization_memberships (organization_id, user_id, role_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (organization_id, user_id) DO UPDATE SET role_id = EXCLUDED.role_id
        """, (organization_id, user_id, role_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (actor_user_id, organization_id, "ADD_ORGANIZATION_USER", f"Added/updated user_id {user_id} with role_id {role_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return user_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def update_user_role(organization_id, target_user_id, new_role_id, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT role_id FROM organization_memberships
            WHERE organization_id = %s AND user_id = %s
        """, (organization_id, target_user_id))
        membership = cursor.fetchone()
        if not membership:
            raise ValueError("User is not a member of this organization.")

        if is_last_active_org_admin(organization_id, target_user_id):
            cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin' AND (organization_id IS NULL OR organization_id = %s)", (organization_id,))
            admin_role_ids = [r[0] for r in cursor.fetchall()]
            if new_role_id not in admin_role_ids:
                raise ValueError("Cannot change role: this is the last active Organization Admin.")

        cursor.execute("SELECT role_id FROM roles WHERE role_id = %s AND (organization_id IS NULL OR organization_id = %s)", (new_role_id, organization_id))
        if not cursor.fetchone():
            raise ValueError("Invalid role selected.")

        cursor.execute("""
            UPDATE organization_memberships SET role_id = %s
            WHERE organization_id = %s AND user_id = %s
        """, (new_role_id, organization_id, target_user_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (actor_user_id, organization_id, "UPDATE_USER_ROLE", f"Updated user_id {target_user_id} to role_id {new_role_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def toggle_user_active(organization_id, target_user_id, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_active FROM users WHERE user_id = %s FOR UPDATE", (target_user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            raise ValueError("User not found.")
        current_active = user_row[0]

        if current_active and is_last_active_org_admin(organization_id, target_user_id):
            raise ValueError("Cannot deactivate user: this is the last active Organization Admin.")

        new_active = not current_active
        cursor.execute("UPDATE users SET is_active = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s", (new_active, target_user_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (actor_user_id, organization_id, "TOGGLE_USER_ACTIVE", f"Set user_id {target_user_id} active = {new_active}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_organization_roles(organization_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.role_id, r.name, r.description, r.organization_id,
               (SELECT COUNT(*) FROM organization_memberships om WHERE om.role_id = r.role_id AND om.organization_id = %s) as member_count
        FROM roles r
        WHERE r.organization_id IS NULL OR r.organization_id = %s
        ORDER BY r.organization_id NULLS FIRST, r.name
    """, (organization_id, organization_id))
    roles = cursor.fetchall()
    cursor.close()
    conn.close()
    return roles


def save_organization_role(organization_id, role_id, name, description, permission_ids, actor_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if role_id:
            cursor.execute("SELECT organization_id FROM roles WHERE role_id = %s", (role_id,))
            r = cursor.fetchone()
            if not r or r[0] is None:
                raise ValueError("Cannot modify built-in system roles.")
            cursor.execute("""
                UPDATE roles SET name = %s, description = %s WHERE role_id = %s AND organization_id = %s
            """, (name.strip(), description, role_id, organization_id))
        else:
            cursor.execute("""
                INSERT INTO roles (organization_id, name, description)
                VALUES (%s, %s, %s)
                RETURNING role_id
            """, (organization_id, name.strip(), description))
            role_id = cursor.fetchone()[0]

        cursor.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
        for pid in permission_ids:
            cursor.execute("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (role_id, int(pid)))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (actor_user_id, organization_id, "SAVE_ROLE", f"Saved role_id {role_id} ('{name}') with {len(permission_ids)} permissions", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return role_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


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


def get_platform_analytics():
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM organizations")
        total_orgs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
        active_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE is_platform_admin = TRUE")
        platform_admins = cursor.fetchone()[0]

        cursor.execute("""
            SELECT status, COUNT(*) FROM organization_subscriptions
            GROUP BY status
        """)
        sub_status_counts = dict(cursor.fetchall())

        cursor.execute("""
            SELECT status, COUNT(*), COALESCE(SUM(amount), 0) FROM organization_payments
            GROUP BY status
        """)
        payment_stats = {}
        for row in cursor.fetchall():
            payment_stats[row[0]] = {"count": row[1], "amount": row[2]}

        cursor.execute("""
            SELECT al.log_id, al.timestamp, u.name, al.action, al.details
            FROM audit_logs al
            LEFT JOIN users u ON u.user_id = al.user_id
            ORDER BY al.log_id DESC
            LIMIT 25
        """)
        recent_logs = cursor.fetchall()

        return {
            "total_orgs": total_orgs,
            "total_users": total_users,
            "active_users": active_users,
            "platform_admins": platform_admins,
            "sub_status_counts": sub_status_counts,
            "payment_stats": payment_stats,
            "recent_logs": recent_logs,
        }
    finally:
        cursor.close()
        conn.close()


def get_platform_tenants(search=None, status_filter=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        query = """
            SELECT o.organization_id, o.name, o.currency, o.created_at,
                   os.status, os.expiry_date, sp.name AS plan_name,
                   (SELECT COUNT(*) FROM organization_memberships om WHERE om.organization_id = o.organization_id) AS user_count
            FROM organizations o
            LEFT JOIN LATERAL (
                SELECT status, expiry_date, plan_id FROM organization_subscriptions
                WHERE organization_id = o.organization_id
                ORDER BY subscription_id DESC LIMIT 1
            ) os ON TRUE
            LEFT JOIN subscription_plans sp ON sp.plan_id = os.plan_id
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (o.name ILIKE %s OR o.ntn ILIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])
        if status_filter and status_filter != "ALL":
            query += " AND os.status = %s"
            params.append(status_filter)

        query += " ORDER BY o.name"
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_all_subscription_plans():
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT p.plan_id, p.name, p.description, p.price, p.billing_cycle, p.is_active, p.grace_period_days, p.created_at,
                   (SELECT COUNT(*) FROM organization_subscriptions os WHERE os.plan_id = p.plan_id) AS active_sub_count
            FROM subscription_plans p
            ORDER BY p.price, p.name
        """)
        plans = cursor.fetchall()
        
        plan_list = []
        for p in plans:
            cursor.execute("SELECT module_code FROM plan_features WHERE plan_id = %s", (p[0],))
            features = [r[0] for r in cursor.fetchall()]
            plan_list.append({
                "plan_id": p[0],
                "name": p[1],
                "description": p[2],
                "price": p[3],
                "billing_cycle": p[4],
                "is_active": p[5],
                "grace_period_days": p[6],
                "created_at": p[7],
                "active_sub_count": p[8],
                "features": features,
            })
        return plan_list
    finally:
        cursor.close()
        conn.close()


def save_subscription_plan(plan_id, name, description, price, billing_cycle, grace_period_days, is_active, modules, admin_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if plan_id:
            cursor.execute("""
                UPDATE subscription_plans
                SET name = %s, description = %s, price = %s, billing_cycle = %s, grace_period_days = %s, is_active = %s
                WHERE plan_id = %s
            """, (name.strip(), description, price, billing_cycle, grace_period_days, is_active, plan_id))
        else:
            cursor.execute("""
                INSERT INTO subscription_plans (name, description, price, billing_cycle, grace_period_days, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING plan_id
            """, (name.strip(), description, price, billing_cycle, grace_period_days, is_active))
            plan_id = cursor.fetchone()[0]

        cursor.execute("DELETE FROM plan_features WHERE plan_id = %s", (plan_id,))
        for mod in modules:
            cursor.execute("""
                INSERT INTO plan_features (plan_id, module_code)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (plan_id, mod))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (admin_user_id, "SAVE_SUBSCRIPTION_PLAN", f"Saved subscription plan_id {plan_id} ('{name}')", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return plan_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def toggle_subscription_plan_active(plan_id, admin_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_active, name FROM subscription_plans WHERE plan_id = %s FOR UPDATE", (plan_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Subscription plan not found.")
        new_active = not row[0]

        cursor.execute("UPDATE subscription_plans SET is_active = %s WHERE plan_id = %s", (new_active, plan_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (admin_user_id, "TOGGLE_PLAN_ACTIVE", f"Set plan_id {plan_id} active = {new_active}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_zyro_organization_id():
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT organization_id FROM organizations WHERE is_zyro = TRUE LIMIT 1")
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute("SELECT organization_id FROM organizations ORDER BY organization_id LIMIT 1")
        row2 = cursor.fetchone()
        return row2[0] if row2 else 1
    finally:
        cursor.close()
        conn.close()


def get_all_clients(search=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        query = """
            SELECT c.client_id, c.name, c.email, c.phone, c.address, c.is_active, c.created_at,
                   (SELECT COUNT(*) FROM organizations o WHERE o.client_id = c.client_id) AS business_count
            FROM clients c
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (c.name ILIKE %s OR c.email ILIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])
        query += " ORDER BY c.name"
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def create_client(name, email, phone, address, admin_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO clients (name, email, phone, address)
            VALUES (%s, %s, %s, %s)
            RETURNING client_id
        """, (name.strip(), email.strip() or None, phone.strip() or None, address.strip() or None))
        client_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, NULL, %s, %s, %s)
        """, (admin_user_id, "CREATE_CLIENT", f"Created client client_id {client_id} ('{name}')", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return client_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_client_details(client_id):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT client_id, name, email, phone, address, is_active, created_at FROM clients WHERE client_id = %s", (client_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def get_client_businesses(client_id):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT o.organization_id, o.name, o.currency, o.nature_of_business, o.ntn, o.strn, o.created_at,
                   os.status, os.expiry_date, sp.name AS plan_name
            FROM organizations o
            LEFT JOIN LATERAL (
                SELECT status, expiry_date, plan_id FROM organization_subscriptions
                WHERE organization_id = o.organization_id
                ORDER BY subscription_id DESC LIMIT 1
            ) os ON TRUE
            LEFT JOIN subscription_plans sp ON sp.plan_id = os.plan_id
            WHERE o.client_id = %s
            ORDER BY o.name
        """, (client_id,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def create_business_for_client(client_id, name, currency, nature_of_business, ntn, strn, head_office_address, warehouse_address, fiscal_year_start_month, admin_user_id, ip_address=None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO organizations (
                name, currency, nature_of_business, ntn, strn,
                head_office_address, warehouse_address, fiscal_year_start_month, client_id, is_zyro
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
            RETURNING organization_id
        """, (
            name.strip(),
            currency.strip() or "PKR",
            nature_of_business or None,
            ntn.strip() or None,
            strn.strip() or None,
            head_office_address.strip() or None,
            warehouse_address.strip() or None,
            fiscal_year_start_month,
            client_id
        ))
        org_id = cursor.fetchone()[0]

        cursor.execute("SELECT plan_id FROM subscription_plans LIMIT 1")
        plan_row = cursor.fetchone()
        if plan_row:
            cursor.execute("""
                INSERT INTO organization_subscriptions (organization_id, plan_id, status, start_date, expiry_date, grace_end_date)
                VALUES (%s, %s, 'TRIAL', CURRENT_DATE, CURRENT_DATE + INTERVAL '30 days', CURRENT_DATE + INTERVAL '37 days')
            """, (org_id, plan_row[0]))

        cursor.execute("""
            INSERT INTO audit_logs (user_id, organization_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (admin_user_id, org_id, "CREATE_CLIENT_BUSINESS", f"Created business organization_id {org_id} ('{name}') for client_id {client_id}", ip_address or (request.remote_addr if request else None)))

        conn.commit()
        return org_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
