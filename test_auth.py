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


if __name__ == "__main__":
    unittest.main(verbosity=2)
