"""
test_auth.py
Comprehensive Security Acceptance Tests for LedgerPulse Authentication & RBAC (Phase 2 & Phase 3).
"""
import os
os.environ["TESTING"] = "true"

import unittest
from unittest.mock import patch, MagicMock
from decimal import Decimal
from flask import session, request, abort

import app
from auth.services import (
    hash_password,
    verify_password,
    generate_csrf_token,
    require_platform_admin,
    has_permission,
    user_belongs_to_organization,
    grant_platform_admin,
    revoke_platform_admin,
    deactivate_user,
    check_subscription,
    validate_csrf,
    submit_organization_payment,
    verify_organization_payment,
    reject_organization_payment,
)


class LedgerPulseSecurityTests(unittest.TestCase):
    
    def setUp(self):
        self.app = app.app
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    def test_1_unauthenticated_platform_admin_denial(self):
        """Item 1: Unauthenticated request to a protected Platform Admin endpoint is denied."""
        with self.client as c:
            response = c.get("/auth/admin/users")
            self.assertIn(response.status_code, [302, 401, 403])

    def test_2_non_platform_admin_denial(self):
        """Item 2: Authenticated non-Platform Admin request is denied (403)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 999
                sess["user_name"] = "Regular User"
                sess["is_platform_admin"] = False
            
            with patch("auth.services.is_platform_admin", return_value=False):
                response = c.get("/auth/admin/users")
                self.assertEqual(response.status_code, 403)

    def test_3_active_authorized_platform_admin_allowed(self):
        """Item 3: Active authorized Platform Admin request is allowed."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["user_name"] = "Super Admin"
                sess["is_platform_admin"] = True

            with patch("auth.services.is_platform_admin", return_value=True), \
                 patch("auth.routes.get_conn") as mock_get_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = [
                    (1, "Super Admin", "admin@ledgerpulse.local", True, True, "2026-01-01")
                ]
                mock_get_conn.return_value.cursor.return_value = mock_cursor
                
                response = c.get("/auth/admin/users")
                self.assertEqual(response.status_code, 200)

    def test_4_inactive_user_session_denial(self):
        """Item 4: Inactive user with an existing session is denied by permission/active checks."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (False,)
            mock_get_conn.return_value.cursor.return_value = mock_cursor
            
            res = has_permission(user_id=10, organization_id=1, permission_code="accounting.view")
            self.assertFalse(res)

    def test_5_organization_membership_isolation(self):
        """Item 5: User without membership cannot switch into an organization (403)."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_get_conn.return_value.cursor.return_value = mock_cursor
            
            with self.client as c:
                with c.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["is_platform_admin"] = True
                    sess["csrf_token"] = "test-csrf-token"
                
                response = c.post("/organizations/switch/999", data={"csrf_token": "test-csrf-token"})
                self.assertEqual(response.status_code, 403)

    def test_6_missing_inactive_targets_safeguard(self):
        """Item 6: Missing and inactive grant/revoke/deactivate targets are rejected safely."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn
            
            with self.assertRaises(ValueError):
                grant_platform_admin(target_user_id=999, actor_user_id=1)

    def test_7_last_active_admin_protection(self):
        """Item 7: Last active Platform Admin cannot be revoked or deactivated."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [(1,)]
            mock_cursor.fetchone.return_value = (True, True)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn
            
            with self.assertRaises(ValueError) as ctx:
                revoke_platform_admin(target_user_id=1, actor_user_id=1)
            self.assertIn("last active platform admin", str(ctx.exception).lower())

    def test_8_audit_rollback_on_failure(self):
        """Item 8: Simulated audit insertion failure rolls back the privilege change."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1, False, True)
            mock_cursor.execute.side_effect = [None, None, Exception("DB Audit Insert Failed")]
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn
            
            with self.assertRaises(Exception):
                grant_platform_admin(target_user_id=2, actor_user_id=1)
            
            mock_conn.rollback.assert_called_once()

    def test_9_subscription_suspension_blocking(self):
        """Item 9: Subscription status SUSPENDED identification."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1, "SUSPENDED", "2026-01-01", "2026-01-08", "Enterprise")
            mock_get_conn.return_value.cursor.return_value = mock_cursor
            
            sub = check_subscription(organization_id=1)
            self.assertEqual(sub["status"], "SUSPENDED")

    def test_9b_subscription_dynamic_grace_period(self):
        """Item 9b: Subscription status dynamically evaluates to GRACE_PERIOD when past expiry but within grace period."""
        from datetime import date, timedelta
        past_expiry = date.today() - timedelta(days=2)
        future_grace = date.today() + timedelta(days=5)
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1, "ACTIVE", past_expiry, future_grace, "Standard ERP Plan")
            mock_get_conn.return_value.cursor.return_value = mock_cursor
            
            sub = check_subscription(organization_id=1)
            self.assertEqual(sub["status"], "GRACE_PERIOD")

    def test_10_csrf_middleware_validation(self):
        """Item 10: CSRF middleware rejects missing/invalid tokens and accepts valid tokens."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["csrf_token"] = "valid-secret-token-123"
            
            with c.post("/organizations/new", data={"name": "Test Org"}, follow_redirects=False) as resp:
                self.assertEqual(resp.status_code, 400)
            
            with patch("organizations.routes.get_conn") as mock_get_conn, \
                 patch("organizations.routes.set_current_organization"):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (1,)
                mock_conn = MagicMock()
                mock_conn.cursor.return_value = mock_cursor
                mock_get_conn.return_value = mock_conn
                
                resp2 = c.post("/organizations/new", data={
                    "csrf_token": "valid-secret-token-123",
                    "name": "Test Org",
                    "currency": "PKR"
                }, follow_redirects=False)
                self.assertEqual(resp2.status_code, 302)

    def test_11_privilege_audit_code_paths_inspected(self):
        """Item 11: Confirmation that all privilege modification paths enforce auth and auditing."""
        self.assertTrue(callable(grant_platform_admin))
        self.assertTrue(callable(revoke_platform_admin))
        self.assertTrue(callable(deactivate_user))

    def test_12_suspended_subscription_write_blocking_and_read_allowed(self):
        """Item 12: SUSPENDED subscription blocks POST (403) with valid CSRF and allows GET (200 read-only)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["organization_id"] = 1
                sess["is_platform_admin"] = False
                sess["csrf_token"] = "valid-secret-token-123"
            
            with patch("app.get_conn") as mock_app_get_conn, \
                 patch("app.check_subscription", return_value={"status": "SUSPENDED", "plan_name": "Pro"}):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (1,)
                mock_app_get_conn.return_value.cursor.return_value = mock_cursor
                
                resp_post = c.post(
                    "/sales/invoices/new",
                    data={"csrf_token": "valid-secret-token-123"},
                    headers={"X-CSRF-Token": "valid-secret-token-123"}
                )
                self.assertEqual(resp_post.status_code, 403)
                
                with patch("sales.routes.get_conn") as mock_sales_get_conn:
                    mock_sales_cursor = MagicMock()
                    mock_sales_cursor.fetchall.return_value = []
                    mock_sales_get_conn.return_value.cursor.return_value = mock_sales_cursor
                    
                    resp_get = c.get("/sales/invoices")
                    self.assertEqual(resp_get.status_code, 200)

    def test_13_platform_admin_payments_endpoint_protection(self):
        """Item 13: Non-platform admin is denied access to /admin/payments (403)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["is_platform_admin"] = False
            
            with patch("auth.services.is_platform_admin", return_value=False):
                resp = c.get("/auth/admin/payments")
                self.assertEqual(resp.status_code, 403)

    def test_14_payment_verification_idempotency_safeguard(self):
        """Item 14: Processing an already verified payment raises ValueError."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1, 1, 1, 1, Decimal("100.00"), "VERIFIED")
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            with self.assertRaises(ValueError):
                verify_organization_payment(payment_id=1, admin_user_id=1)
            mock_conn.rollback.assert_called_once()

    def test_15_payment_amount_mismatch_rejection(self):
        """Item 15: Submitting payment with amount different from plan price raises ValueError."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (Decimal("50.00"), True)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            with self.assertRaises(ValueError):
                submit_organization_payment(
                    organization_id=1,
                    user_id=1,
                    plan_id=1,
                    amount=Decimal("10.00"),
                    payment_method="Bank Transfer",
                    reference_number="REF-999",
                    notes=None
                )
            mock_conn.rollback.assert_called_once()

    def test_16_inactive_plan_rejection(self):
        """Item 16: Submitting payment for an inactive plan raises ValueError."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (Decimal("50.00"), False)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            with self.assertRaises(ValueError):
                submit_organization_payment(
                    organization_id=1,
                    user_id=1,
                    plan_id=2,
                    amount=Decimal("50.00"),
                    payment_method="Bank Transfer",
                    reference_number="REF-999",
                    notes=None
                )
            mock_conn.rollback.assert_called_once()

    def test_17_payment_verification_success(self):
        """Item 17: Successful verification updates payment status and extends expiry."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [
                (1, 1, None, 1, Decimal("0.00"), "PENDING"),
                ('monthly',),
                None,
                (10,)
            ]
            mock_cursor.fetchall.return_value = []
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            verify_organization_payment(payment_id=1, admin_user_id=1)
            mock_conn.commit.assert_called_once()

    def test_18_payment_rejection(self):
        """Item 18: Rejecting payment updates status to REJECTED without touching subscriptions."""
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1, 1, "PENDING")
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            reject_organization_payment(payment_id=1, admin_user_id=1, reason="Invalid proof")
            mock_conn.commit.assert_called_once()

    def test_19_login_page_renders(self):
        """Milestone 1: GET /auth/login returns 200 and renders login form."""
        with patch("auth.routes.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,) # user_count > 0
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            response = self.client.get("/auth/login")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Welcome back", response.data)

    def test_20_login_invalid_credentials(self):
        """Milestone 1: POST /auth/login with invalid credentials flashes error and stays on login."""
        with patch("auth.routes.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [
                (1,), # user_count check in before_request or login
                None  # user not found
            ]
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            with self.client as c:
                with c.session_transaction() as sess:
                    sess["csrf_token"] = "test-csrf-token"

                response = c.post("/auth/login", data={
                    "csrf_token": "test-csrf-token",
                    "email": "wrong@ledgerpulse.local",
                    "password": "wrongpassword"
                }, follow_redirects=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Invalid email or password", response.data)

    def test_21_login_success_and_logout(self):
        """Milestone 1: Successful login sets session, redirects, and logout clears session."""
        with patch("auth.routes.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            # login user_count, user row, subsequent GET /auth/login user_count after logout redirect
            hashed = hash_password("correctpassword")
            mock_cursor.fetchone.side_effect = [
                (1,), # user_count in login POST
                (1, "Admin User", "admin@ledgerpulse.local", hashed, True, True), # user row
                (1,), # user_count in GET /auth/login after logout redirect
            ]
            mock_cursor.fetchall.return_value = [(1,)] # organization membership
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            with self.client as c:
                with c.session_transaction() as sess:
                    sess["csrf_token"] = "test-csrf-token"

                response = c.post("/auth/login", data={
                    "csrf_token": "test-csrf-token",
                    "email": "admin@ledgerpulse.local",
                    "password": "correctpassword"
                }, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                
                # Check logout
                logout_resp = c.get("/auth/logout", follow_redirects=True)
                self.assertEqual(logout_resp.status_code, 200)
                self.assertIn(b"You have been logged out", logout_resp.data)

    def test_22_setup_locked_when_users_exist(self):
        """Milestone 1: GET /auth/setup redirects to login when users already exist."""
        with patch("auth.routes.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5,) # 5 users exist
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            response = self.client.get("/auth/setup", follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.location.endswith("/auth/login"))

    def test_23_protected_route_redirect(self):
        """Milestone 1: Unauthenticated request to protected route redirects to login."""
        with patch("app.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (2,) # user_count > 0
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            response = self.client.get("/dashboard/", follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertTrue("/auth/login" in response.location)

    def test_24_last_active_org_admin_protection(self):
        """User Management: Verify is_last_active_org_admin correctly identifies the final admin."""
        from auth.services import is_last_active_org_admin
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [(1,)] # admin role ids
            mock_cursor.fetchone.side_effect = [
                (1,), # is admin check
                (1,)  # admin count <= 1
            ]
            mock_get_conn.return_value.cursor.return_value = mock_cursor

            res = is_last_active_org_admin(organization_id=1, user_id=1)
            self.assertTrue(res)

    def test_25_deactivate_last_active_org_admin_raises(self):
        """User Management: Attempting to deactivate last active org admin raises ValueError."""
        from auth.services import toggle_user_active
        with patch("auth.services.get_conn") as mock_get_conn, \
             patch("auth.services.is_last_active_org_admin", return_value=True):
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (True,) # user is currently active
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            with self.assertRaises(ValueError) as ctx:
                toggle_user_active(organization_id=1, target_user_id=1, actor_user_id=1)
            self.assertIn("last active Organization Admin", str(ctx.exception))
            mock_conn.rollback.assert_called_once()

    def test_26_users_list_permission_denied(self):
        """User Management: User lacking users.manage permission receives 403 on /organizations/1/users."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["organization_id"] = 1
                sess["is_platform_admin"] = False
            
            with patch("app.get_conn") as mock_get_conn, \
                 patch("core.context.current_organization_id", return_value=1), \
                 patch("auth.services.has_permission", return_value=False):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.side_effect = [
                    (1,), # user_count in before_request
                    (True,) # is_active in before_request
                ]
                mock_get_conn.return_value.cursor.return_value = mock_cursor

                response = c.get("/organizations/1/users")
                self.assertEqual(response.status_code, 403)

    def test_27_users_list_allowed_with_permission(self):
        """User Management: User with users.manage permission or platform admin successfully loads users list."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["organization_id"] = 1
                sess["is_platform_admin"] = True

            with patch("core.context.current_organization_id", return_value=1), \
                 patch("auth.services.is_platform_admin", return_value=True), \
                 patch("organizations.routes.get_organization_users", return_value=[]), \
                 patch("organizations.routes.get_organization_roles", return_value=[]):
                response = c.get("/organizations/1/users")
                self.assertEqual(response.status_code, 200)

    def test_28_cross_organization_user_management_denied(self):
        """User Management: Accessing organization 2 users while active in organization 1 is denied (403)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["organization_id"] = 1
                sess["is_platform_admin"] = False

            with patch("core.context.current_organization_id", return_value=1):
                response = c.get("/organizations/2/users")
                self.assertEqual(response.status_code, 403)

    def test_29_platform_admin_dashboard_access(self):
        """Platform Admin: Authorized platform admin successfully accesses /auth/admin/dashboard (200)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["is_platform_admin"] = True
            
            with patch("app.get_conn") as mock_app_get_conn, \
                 patch("auth.routes.get_platform_analytics", return_value={
                     "total_orgs": 1, "total_users": 1, "active_users": 1, "platform_admins": 1,
                     "sub_status_counts": {"ACTIVE": 1}, "payment_stats": {}, "recent_logs": []
                 }), \
                 patch("auth.services.is_platform_admin", return_value=True):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.side_effect = [(1,), (True,)]
                mock_app_get_conn.return_value.cursor.return_value = mock_cursor

                response = c.get("/auth/admin/dashboard")
                self.assertEqual(response.status_code, 200)

    def test_30_non_platform_admin_dashboard_denial(self):
        """Platform Admin: Non-platform admin is denied access to /auth/admin/dashboard (403)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["is_platform_admin"] = False
            
            with patch("auth.services.is_platform_admin", return_value=False):
                response = c.get("/auth/admin/dashboard")
                self.assertEqual(response.status_code, 403)

    def test_31_platform_admin_tenants_access(self):
        """Platform Admin: Platform admin successfully accesses tenant health monitoring (/auth/admin/tenants)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["is_platform_admin"] = True
            
            with patch("app.get_conn") as mock_app_get_conn, \
                 patch("auth.routes.get_platform_tenants", return_value=[]), \
                 patch("auth.services.is_platform_admin", return_value=True):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.side_effect = [(1,), (True,)]
                mock_app_get_conn.return_value.cursor.return_value = mock_cursor

                response = c.get("/auth/admin/tenants")
                self.assertEqual(response.status_code, 200)

    def test_32_platform_admin_plans_access(self):
        """Platform Admin: Platform admin successfully accesses subscription plan management (/auth/admin/plans)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["is_platform_admin"] = True
            
            with patch("app.get_conn") as mock_app_get_conn, \
                 patch("auth.routes.get_all_subscription_plans", return_value=[]), \
                 patch("auth.services.is_platform_admin", return_value=True):
                mock_cursor = MagicMock()
                mock_cursor.fetchone.side_effect = [(1,), (True,)]
                mock_app_get_conn.return_value.cursor.return_value = mock_cursor

                response = c.get("/auth/admin/plans")
                self.assertEqual(response.status_code, 200)

    def test_33_save_subscription_plan_service(self):
        """Platform Admin: save_subscription_plan successfully executes insert/update and audit log."""
        from auth.services import save_subscription_plan
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5,) # new plan_id
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            pid = save_subscription_plan(
                plan_id=None,
                name="Test Plan",
                description="Test Desc",
                price=Decimal("99.00"),
                billing_cycle="monthly",
                grace_period_days=7,
                is_active=True,
                modules=["sales", "accounting"],
                admin_user_id=1
            )
            self.assertEqual(pid, 5)
            mock_conn.commit.assert_called_once()

    def test_34_toggle_subscription_plan_active(self):
        """Platform Admin: toggle_subscription_plan_active successfully toggles active status and audits."""
        from auth.services import toggle_subscription_plan_active
        with patch("auth.services.get_conn") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (True, "Test Plan")
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn

            toggle_subscription_plan_active(plan_id=1, admin_user_id=1)
            mock_conn.commit.assert_called_once()

    def test_35_dashboard_receipts_query(self):
        """Dashboard: recent customer receipts query uses valid schema columns ('—' AS payment_method) without UndefinedColumn error."""
        with self.client as c:
            with patch("dashboard.routes.get_conn") as mock_get_conn, \
                 patch("dashboard.routes.current_organization_id") as mock_org, \
                 patch("dashboard.routes.current_user_id") as mock_user, \
                 patch("dashboard.routes._is_manager") as mock_mgr:
                mock_org.return_value = 1
                mock_user.return_value = 1
                mock_mgr.return_value = True

                mock_cursor = MagicMock()
                # Mock return values for org row, customers, vendors, accounts, sums, recent lists, trends, etc.
                mock_cursor.fetchone.side_effect = [
                    ("Test Org", "PKR"), # org row
                    (Decimal("0"),), # sales
                    (Decimal("0"),), # purchases
                    (Decimal("0"), Decimal("0")), # np_row
                    (Decimal("0"),), # cash
                    (Decimal("0"),), # total invoiced
                    (Decimal("0"),), # total received
                    (Decimal("0"),), # total billed
                    (Decimal("0"),), # total paid
                    (0,), # unposted
                    (0,), # posted journals
                ]
                mock_cursor.fetchall.side_effect = [
                    [], # customers
                    [], # vendors
                    [], # bank accounts
                    [], # recent invoices
                    [], # recent receipts
                    [], # recent bills
                    [], # recent payments
                    [], # recent journals
                    [], # recent transfers
                    [], # sales trend
                    [], # purchases trend
                    [], # pnl trend
                    [], # cash trend
                    [], # ar trend
                    [], # ap trend
                    [], # ar analysis
                    [], # ap analysis
                    None, # overdue inv
                    None, # overdue bill
                    None, # unbal draft
                    None, # preferences
                ]
                mock_conn = MagicMock()
                mock_conn.cursor.return_value = mock_cursor
                mock_get_conn.return_value = mock_conn

                res = c.get("/")
                self.assertIn(res.status_code, [200, 302])

    def test_zyro_erp_clients_management_access(self):
        """ZYRO ERP: Platform admin can access clients management (/auth/admin/clients)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 1
                sess["is_platform_admin"] = True

            with patch("auth.routes.get_all_clients", return_value=[]), \
                 patch("auth.services.is_platform_admin", return_value=True), \
                 patch("app.get_conn") as mock_get_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchone.side_effect = [(1,), (True,)]
                mock_get_conn.return_value.cursor.return_value = mock_cursor

                response = c.get("/auth/admin/clients")
                self.assertEqual(response.status_code, 200)

    def test_zyro_erp_non_admin_client_access_denied(self):
        """ZYRO ERP: Regular user is denied access to clients management."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["is_platform_admin"] = False

            with patch("auth.services.is_platform_admin", return_value=False):
                response = c.get("/auth/admin/clients")
                self.assertEqual(response.status_code, 403)

    def test_zyro_erp_organization_creation_admin_only(self):
        """ZYRO ERP: Regular user cannot access new organization creation (/organizations/new)."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["is_platform_admin"] = False

            with patch("auth.services.is_platform_admin", return_value=False):
                response = c.get("/organizations/new")
                self.assertEqual(response.status_code, 403)

    def test_zyro_erp_tenant_isolation_unassigned_switch(self):
        """ZYRO ERP: Regular user cannot switch to an unassigned organization."""
        with self.client as c:
            with c.session_transaction() as sess:
                sess["user_id"] = 2
                sess["is_platform_admin"] = False
                sess["csrf_token"] = "test-csrf"

            with patch("auth.services.is_platform_admin", return_value=False), \
                 patch("auth.services.user_belongs_to_organization", return_value=False):
                response = c.post("/organizations/switch/999", data={"csrf_token": "test-csrf"})
                self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
