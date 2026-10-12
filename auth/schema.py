"""
auth/schema.py

Additive database schema bootstrap for Authentication, RBAC, SaaS Subscriptions,
Payments, and Audit Logging in LedgerPulse.
"""


def ensure_schema(cursor):
    # 1. Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            is_platform_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP
        )
    """)

    # 2. Roles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            role_id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,
            description TEXT
        )
    """)

    # 3. Permissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            permission_id SERIAL PRIMARY KEY,
            code VARCHAR(100) UNIQUE NOT NULL,
            name VARCHAR(150) NOT NULL,
            module VARCHAR(50) NOT NULL
        )
    """)

    # 4. Role Permissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER REFERENCES roles(role_id) ON DELETE CASCADE,
            permission_id INTEGER REFERENCES permissions(permission_id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        )
    """)

    # 5. Organization Memberships table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organization_memberships (
            membership_id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(organization_id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
            role_id INTEGER REFERENCES roles(role_id) ON DELETE RESTRICT,
            UNIQUE (organization_id, user_id)
        )
    """)

    # 6. Subscription Plans table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscription_plans (
            plan_id SERIAL PRIMARY KEY,
            name VARCHAR(100) UNIQUE NOT NULL,
            price NUMERIC(12, 2) DEFAULT 0,
            billing_cycle VARCHAR(20) DEFAULT 'monthly',
            is_active BOOLEAN DEFAULT TRUE,
            grace_period_days INTEGER DEFAULT 7,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. Plan Features table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan_features (
            plan_feature_id SERIAL PRIMARY KEY,
            plan_id INTEGER REFERENCES subscription_plans(plan_id) ON DELETE CASCADE,
            module_code VARCHAR(50) NOT NULL,
            UNIQUE (plan_id, module_code)
        )
    """)

    # 8. Organization Subscriptions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organization_subscriptions (
            subscription_id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(organization_id) ON DELETE CASCADE,
            plan_id INTEGER REFERENCES subscription_plans(plan_id) ON DELETE RESTRICT,
            status VARCHAR(30) DEFAULT 'TRIAL',
            start_date DATE DEFAULT CURRENT_DATE,
            expiry_date DATE,
            grace_end_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 9. Organization Payments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organization_payments (
            payment_id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(organization_id) ON DELETE CASCADE,
            subscription_id INTEGER REFERENCES organization_subscriptions(subscription_id) ON DELETE SET NULL,
            plan_id INTEGER REFERENCES subscription_plans(plan_id) ON DELETE SET NULL,
            amount NUMERIC(12, 2) NOT NULL,
            payment_date DATE DEFAULT CURRENT_DATE,
            status VARCHAR(30) DEFAULT 'PENDING',
            payment_method VARCHAR(50) DEFAULT 'Manual',
            reference_number VARCHAR(100),
            verified_by_user_id INTEGER REFERENCES users(user_id),
            verified_at TIMESTAMP,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE organization_payments ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES subscription_plans(plan_id) ON DELETE SET NULL")

    # 10. Audit Logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
            organization_id INTEGER REFERENCES organizations(organization_id) ON DELETE SET NULL,
            action VARCHAR(100) NOT NULL,
            details TEXT,
            ip_address VARCHAR(50)
        )
    """)

    # --- SEED DEFAULT ROLES & PERMISSIONS ---
    default_roles = [
        ("Admin", "Full organizational administration and management"),
        ("Accountant", "Create, edit, post financial documents and view reports"),
        ("Viewer", "Read-only access to records and reports"),
    ]
    for r_name, r_desc in default_roles:
        cursor.execute("""
            INSERT INTO roles (name, description)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
        """, (r_name, r_desc))

    default_permissions = [
        ("accounting.view", "View Chart of Accounts & Journals", "accounting"),
        ("accounting.create", "Create Journal Entries & Accounts", "accounting"),
        ("accounting.post", "Post Journal Entries", "accounting"),
        ("sales.view", "View Sales Invoices & Customers", "sales"),
        ("sales.create", "Create Sales Invoices & Customers", "sales"),
        ("sales.post", "Post Sales Invoices", "sales"),
        ("vendors.view", "View Vendor Bills & Payments", "vendors"),
        ("vendors.create", "Create Vendor Bills & Payments", "vendors"),
        ("vendors.post", "Post Vendor Bills & Payments", "vendors"),
        ("reports.view", "View Financial Reports & Ledgers", "reports"),
        ("users.manage", "Manage Organization Users & Roles", "users"),
        ("subscription.view", "View Organization Subscription", "subscription"),
        ("subscription.manage", "Manage Subscription & Payments", "subscription"),
    ]
    for p_code, p_name, p_mod in default_permissions:
        cursor.execute("""
            INSERT INTO permissions (code, name, module)
            VALUES (%s, %s, %s)
            ON CONFLICT (code) DO NOTHING
        """, (p_code, p_name, p_mod))

    # Map all permissions to Admin
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Admin'")
    admin_row = cursor.fetchone()
    if admin_row:
        admin_role_id = admin_row[0]
        cursor.execute("SELECT permission_id FROM permissions")
        for perm in cursor.fetchall():
            cursor.execute("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (admin_role_id, perm[0]))

    # Map accountant permissions
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Accountant'")
    acc_row = cursor.fetchone()
    if acc_row:
        acc_role_id = acc_row[0]
        cursor.execute("""
            SELECT permission_id FROM permissions
            WHERE code IN (
                'accounting.view', 'accounting.create', 'accounting.post',
                'sales.view', 'sales.create', 'sales.post',
                'vendors.view', 'vendors.create', 'vendors.post',
                'reports.view', 'subscription.view'
            )
        """)
        for perm in cursor.fetchall():
            cursor.execute("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (acc_role_id, perm[0]))

    # Map viewer permissions
    cursor.execute("SELECT role_id FROM roles WHERE name = 'Viewer'")
    view_row = cursor.fetchone()
    if view_row:
        view_role_id = view_row[0]
        cursor.execute("""
            SELECT permission_id FROM permissions
            WHERE code LIKE '%.view'
        """)
        for perm in cursor.fetchall():
            cursor.execute("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (view_role_id, perm[0]))

    # --- SEED DEFAULT SUBSCRIPTION PLAN ---
    cursor.execute("""
        INSERT INTO subscription_plans (name, price, billing_cycle, is_active, grace_period_days)
        VALUES ('Standard ERP Plan', 0.00, 'monthly', TRUE, 7)
        ON CONFLICT (name) DO NOTHING
    """)
    cursor.execute("SELECT plan_id FROM subscription_plans WHERE name = 'Standard ERP Plan'")
    plan_row = cursor.fetchone()
    if plan_row:
        plan_id = plan_row[0]
        modules = ['core_accounting', 'sales', 'customers', 'vendors', 'reports', 'wht']
        for mod in modules:
            cursor.execute("""
                INSERT INTO plan_features (plan_id, module_code)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (plan_id, mod))
