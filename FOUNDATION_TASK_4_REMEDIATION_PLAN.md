# LEDGERPULSE — FOUNDATION TASK 4 REMEDIATION PLAN (VERSION 5.3)

**Document Version:** 5.3  
**Status:** FULLY APPROVED BY USER (READY FOR IMPLEMENTATION UPON AUTHORIZATION)  
**Target Architecture:** Multi-Organization SaaS ERP with Granular RBAC, Subscription Licensing, Grace Periods, Payment Verification, Secure Legacy Migration, CSRF Protection, and Complete Auditability  

---

## 1. Executive Summary & Objective
This remediation plan establishes the definitive, production-grade security architecture for LedgerPulse Foundation Task 4. It resolves all security vulnerabilities, authorization gaps, subscription enforcement omissions, and CSRF compliance risks while strictly preserving Foundation Tasks 1–3 tenant isolation (`organization_id` scoping), Decimal-safe financial calculations, and posted accounting document integrity.

---

## 2. Detailed Architectural Core Design

### Rule 1: Hardened Platform Admin Bootstrap, Session Fixation Protection & Rate Limiting
- **Concurrent Setup Hardening:** The `/auth/setup` route is hardened against race conditions during concurrent initialization requests using PostgreSQL advisory locks (`pg_advisory_xact_lock`) or atomic unique constraint checks. If users already exist, concurrent setup attempts are safely rejected with HTTP 403.
- **Session Fixation Protection:** Upon successful authentication (`/auth/login`), the session store is completely cleared (`session.clear()`) before establishing new session keys (`session['user_id']`, `session['user_name']`, `session['csrf_token']`), neutralizing session fixation attacks.
- **Authentication & Setup Rate Limiting:** A practical rate-limiting policy (e.g., maximum 5 failed login attempts per minute per IP, and 1 setup attempt per deployment) is enforced using in-memory token bucket counters or Redis/Flask-Limiter. *Testing:* Automated security tests simulate brute-force login floods and verify HTTP 429 Too Many Requests responses.
- **Privilege Granting, Revocation & Last-Admin Protection:** Platform administrative privileges (`is_platform_admin = TRUE`) can be granted or revoked exclusively by verified platform administrators via authenticated routes. Guardrails prevent revoking or deactivating the last active platform admin.
- **Immutable Audit Requirements:** All platform admin actions, grants, revocations, and payment verifications generate immutable entries in the `audit_logs` table.
- **Separation & Non-Access:** Platform admins possess no automatic access to client financial ledgers, journal entries, or invoices. **No impersonation mechanism** exists or is permitted.

### Rule 2: Controlled, Reviewable Legacy Migration Process
- **Data Preservation:** All existing organizations, accounting records, journal headers, journal lines, customers, vendors, and products are preserved in full. No destructive schema drops or accounting table resets are executed.
- **Explicit Organization-to-User Mapping:** Blanket automatic seeding of `ACTIVE` or `TRIAL` subscriptions for legacy organizations is strictly prohibited. Legacy migration is executed via an explicit administrative mapping script or utility:
  - Each legacy organization is explicitly mapped to authorized user accounts in `organization_memberships`.
  - Each legacy organization receives an explicitly approved subscription record with defined plan IDs, start dates, and expiry dates.
- **Idempotency & Safety:** Migration scripts are fully idempotent (using `ON CONFLICT DO NOTHING`) and execute additive schema changes (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). **No reset scripts** are used.

### Rule 3: Comprehensive Subscription State Machine & Transitions
- **Subscription States:** `TRIAL`, `PENDING_PAYMENT`, `ACTIVE`, `GRACE_PERIOD`, `SUSPENDED`, `CANCELLED`, `EXPIRED`.
- **State Transitions:**
  - *Initial Activation:* New organizations start in `PENDING_PAYMENT` (or `TRIAL`). Upon verified payment, subscription transitions to `ACTIVE` with explicit `start_date` and `expiry_date`.
  - *Renewal:* Submitting a renewal payment creates a `PENDING` payment record. Upon authorized platform verification, subscription `expiry_date` is extended.
  - *Early Payment:* Permitted at any time; extends current `expiry_date` cumulatively.
  - *Grace-Period Renewal:* If an `ACTIVE` subscription expires, it automatically transitions to `GRACE_PERIOD` (default 7 days). Payment during grace period transitions status back to `ACTIVE` and extends expiry.
  - *Rejected / Duplicate Payments:* Failed verifications are marked `REJECTED`. Duplicate submissions are handled idempotently.
  - *Suspension / Cancellation / Expiry / Reactivation:* Grace exhaustion leads to `SUSPENDED`. Reactivation requires `VERIFIED` payment.
- **Pending Payment Rule:** Payments remain strictly `PENDING` until authorized platform admin verification (`VERIFIED`). Unverified payments never automatically activate subscriptions.

---

## 4. Complete Route-by-Route Permission Matrix
*Inventory includes all actual registered Flask routes plus explicitly designated NEW ROUTES TO CREATE.*

| Module | Route Path | Methods | Required Permission | Required Feature | Tenant Check (`org_id`) | FK & Ownership Validation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Auth** | `/auth/login` | GET, POST | Public | None | N/A | Rate-limited + session fixation defense |
| **Auth** | `/auth/logout` | GET, POST | Authenticated | None | N/A | Clears session securely |
| **Auth** | `/auth/setup` | GET, POST | Bootstrap (`users == 0`) | None | N/A | Concurrent lock + rate limiting |
| **Dashboard** | `/` | GET | Authenticated | None | Enforced | Active organization dashboard |
| **Organizations** | `/organizations/pick` | GET | Authenticated | None | Verified Membership | List user's organizations |
| **Organizations** | `/organizations/switch/<id>` | POST | Authenticated | None | Verified Membership | Validates membership in target org |
| **Organizations** | `/organizations/new` | GET, POST | `organizations.create` | `core_accounting` | N/A | Creates organization & assigns Admin |
| **Organizations** | `/organizations/<id>/edit` | GET, POST | `organizations.manage` | `core_accounting` | Enforced | Verifies org ownership / Admin role |
| **Organizations** | `/organizations/manage` | GET | `organizations.manage` | `core_accounting` | Enforced | Org membership management |
| **Accounting** | `/accounting/chart-of-accounts` | GET | `accounting.view` | `core_accounting` | Enforced | Chart of accounts list |
| **Accounting** | `/accounting/chart-of-accounts/new`| GET, POST | `accounting.create` | `core_accounting` | Enforced | Create account (tenant-scoped) |
| **Accounting** | `/accounting/journal-entries` | GET | `accounting.view` | `core_accounting` | Enforced | Journal entries list |
| **Accounting** | `/accounting/journal-entries/new` | GET, POST | `accounting.create` | `core_accounting` | Enforced | Create journal (debit == credit) |
| **Accounting** | `/accounting/journal-entries/<id>` | GET | `accounting.view` | `core_accounting` | Enforced | Journal detail & line verification |
| **Accounting** | `/accounting/journal-entries/<id>/post`| POST | `accounting.post` | `core_accounting` | Enforced | Post draft journal entry |
| **Sales** | `/sales/products` | GET | `sales.view` | `sales` | Enforced | Product catalog list |
| **Sales** | `/sales/products/new` | GET, POST | `sales.create` | `sales` | Enforced | Create product (tenant-scoped) |
| **Sales** | `/sales/tax-rates` | GET | `sales.view` | `sales` | Enforced | Tax rates list |
| **Sales** | `/sales/tax-rates/new` | GET, POST | `sales.create` | `sales` | Enforced | Create tax rate |
| **Sales** | `/sales/tax-rates/<id>/toggle` | POST | `sales.manage` | `sales` | Enforced | Toggle tax rate status |
| **Sales** | `/sales/invoices` | GET | `sales.view` | `sales` | Enforced | Sales invoices list |
| **Sales** | `/sales/invoices/new` | GET, POST | `sales.create` | `sales` | Enforced | Invoice with product/tax FK checks |
| **Sales** | `/sales/invoices/<id>` | GET | `sales.view` | `sales` | Enforced | Invoice detail |
| **Sales** | `/sales/invoices/<id>/post` | POST | `sales.post` | `sales` | Enforced | Post sales invoice |
| **Vendors** | `/vendors/` | GET | `vendors.view` | `vendors` | Enforced | Vendors list |
| **Vendors** | `/vendors/new` | GET, POST | `vendors.create` | `vendors` | Enforced | Create vendor |
| **Vendors** | `/vendors/bills` | GET | `vendors.view` | `vendors` | Enforced | Vendor bills list |
| **Vendors** | `/vendors/bills/new` | GET, POST | `vendors.create` | `vendors` | Enforced | Bill with vendor FK validation |
| **Vendors** | `/vendors/bills/<id>` | GET | `vendors.view` | `vendors` | Enforced | Vendor bill detail |
| **Vendors** | `/vendors/bills/<id>/post` | POST | `vendors.post` | `vendors` | Enforced | Post vendor bill |
| **Vendors** | `/vendors/payments` | GET | `vendors.view` | `vendors` | Enforced | Vendor payments list |
| **Vendors** | `/vendors/payments/new` | GET, POST | `vendors.create` | `vendors` | Enforced | Payment with vendor/bill FK check |
| **Vendors** | `/vendors/payments/<id>` | GET | `vendors.view` | `vendors` | Enforced | Vendor payment detail |
| **Customers** | `/customers/` | GET | `customers.view` | `customers` | Enforced | Customers list |
| **Customers** | `/customers/new` | GET, POST | `customers.create` | `customers` | Enforced | Create customer |
| **Customers** | `/customers/receipts` | GET | `customers.view` | `customers` | Enforced | Customer receipts list |
| **Customers** | `/customers/receipts/new` | GET, POST | `customers.create` | `customers` | Enforced | Receipt with customer FK check |
| **Customers** | `/customers/receipts/<id>` | GET | `customers.view` | `customers` | Enforced | Customer receipt detail |
| **WHT** | `/wht/` | GET | `accounting.view` | `core_accounting` | Enforced | WHT rates list |
| **WHT** | `/wht/new` | GET, POST | `accounting.create` | `core_accounting` | Enforced | Create WHT rate |
| **WHT** | `/wht/<id>/toggle` | POST | `accounting.manage` | `core_accounting` | Enforced | Toggle WHT status |
| **SaaS / Admin** | `/admin/plans` | GET, POST | `platform.plans.manage` | N/A | Platform Admin Flag | **NEW ROUTE TO CREATE** |
| **SaaS / Admin** | `/admin/payments` | GET, POST | `platform.payments.verify` | N/A | Platform Admin Flag | **NEW ROUTE TO CREATE** |
| **SaaS / Billing** | `/organizations/payments/submit` | GET, POST | `organizations.manage` | N/A | Enforced | **NEW ROUTE TO CREATE** |
| **SaaS / Billing** | `/organizations/subscription/renew` | GET, POST | `organizations.manage` | N/A | Enforced | **NEW ROUTE TO CREATE** |

---

## 5. CSRF Inventory & Future Webhook Policy
- **HTML Forms & State-Changing Endpoints Inventory:** All `POST`, `PUT`, `DELETE` routes in Section 4 require CSRF token validation.
- **Future Payment-Webhooks CSRF Policy:** There are **no blanket CSRF exemptions**. Incoming payment gateway webhooks (e.g., Stripe) bypass session CSRF tokens but **must** use verified cryptographic signatures (hmac-sha256) and timestamp replay protection headers.
- **Rollout Sequence:** Inject `{{ csrf_field() }}` into all form templates, activate dual-logging, then enforce strict HTTP 400 rejection.

---

## 6. Testing Strategy & Local vs. Database Separation
- **Local Unit Tests:** Password hashing, token generation, CSRF validation logic, permission checks, and Decimal safety. Run anywhere without DB.
- **Integration & Security Tests:** Tenant isolation, database foreign key constraints, subscription lifecycle states, journal posting workflows, and RBAC enforcement. Requires live PostgreSQL/Neon database.
- **Epistemology:** Never claim tests ran unless they actually executed and produced verified output.

---

## 7. Decoupled Payment Workflows & Concurrency Safety
- **Separation:** Tenant admin submits payment via `/organizations/payments/submit` (`PENDING`). Platform admin verifies via `/admin/payments` (`VERIFIED` / `REJECTED`).
- **Concurrency & Duplicate Safety:** Database constraints and transaction row-locking prevent duplicate verification. All decisions are audited in `audit_logs`.

---

## 8. Central Subscription Access Policy (Suspension Enforcement)
- When `status == 'SUSPENDED'`:
  - **Blocked:** All transactional creations and financial mutations (`POST`, `PUT`, `DELETE` across accounting, sales, vendors, customers). Returns HTTP 403.
  - **Allowed:** Read-only inspection (`GET` views for existing records/reports) and subscription renewal/payment submission screens.

---

## 9. Seven-Finding Cross-Check Table (Security Review Resolution)

| Original Finding | Severity | Resolution Section | Intended Security Control | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **1. Platform Admin Membership Bypass** | CRITICAL | Section 2.1 & Section 4 | Removed blanket `is_platform_admin` bypass from `has_permission()`. Platform admins must have explicit membership or use dedicated platform admin routes. | Test verifying platform admin without organization membership receives HTTP 403 when accessing client general ledgers. |
| **2. Unsafe Legacy Org Claiming in Setup** | HIGH | Section 2.2 | Removed automatic assignment of the first legacy organization in `/auth/setup`. Setup creates master admin only; legacy orgs remain unassigned. | Test verifying setup creates fresh isolated org or leaves existing legacy org unassigned. |
| **3. Automatic Active Subscription Seeding** | HIGH | Section 2.2 & 3.2 | Prohibited blanket seeding of 1-year `ACTIVE` subscriptions. Legacy orgs require explicit administrative mapping and approved dates. | Test verifying migrated legacy orgs have no active subscription by default until explicitly mapped. |
| **4. Unimplemented Suspension Enforcement** | HIGH | Section 3.4 & Section 8 | Replaced placeholder `pass` in `security_before_request()` with strict write-blocking (`POST`/`PUT`/`DELETE` blocked with HTTP 403 for `SUSPENDED` tenants). | Test verifying `SUSPENDED` tenant cannot create/post sales invoices but can view existing reports (`GET`). |
| **5. Missing CSRF Form Integration** | HIGH | Section 5 | Inventoried all state-changing forms, defined Jinja helper `{{ csrf_field() }}`, and session-bound validation middleware. | Test verifying form submission with valid token succeeds, while missing/invalid token yields HTTP 400. |
| **6. Unenforced RBAC Decorators** | MEDIUM | Section 4 (Route Matrix) | Applied `@require_permission` decorators across all transactional and administrative route handlers. | Test verifying `Viewer`-role user receives HTTP 403 when attempting to post journal entries or invoices. |
| **7. Session-Trusted Flag / Stale User Check** | MEDIUM | Section 2.1, 3.6, & App Middleware | Added active-user validation (`is_active = TRUE`) on every request, session fixation clearing (`session.clear()`), and last-admin protection. | Test verifying deactivating a user (`is_active = FALSE`) immediately invalidates their active session. |

---

## 10. Exact Files Affected & Confirmed Decision Log

### A. Exact Files Affected
- `app.py`, `core/context.py`, `auth/routes.py`, `auth/services.py`, `auth/schema.py`, `organizations/routes.py`, `accounting/routes.py`, `sales/routes.py`, `vendors/routes.py`, `customers/routes.py`, `wht/routes.py`, `templates/**/*.html`, and test files (`test_security.py`, `test_auth.py`, `test_integration.py`).

### B. Confirmed Decision Log (User Approvals Recorded)
1. **Platform Admin Flag (`is_platform_admin` on `users` table):** **APPROVED**. Global SaaS management implemented via internal user flag with strict permission boundaries, last-admin protection, and immutable audit logging.
2. **Explicit Legacy Migration Mapping:** **APPROVED**. Legacy organizations require explicit administrative mapping to users and approved subscription plans/dates/statuses (no automatic active seeding).
3. **Suspension Write-Blocking Policy:** **APPROVED**. Blocking `POST`/`PUT`/`DELETE` requests for `SUSPENDED` organizations while retaining read-only inspection (`GET`) and renewal/payment screen access.
