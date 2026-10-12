# LEDGERPULSE — FOUNDATION TASK 4
# AUTHENTICATION & AUTHORIZATION AUDIT REPORT (READ-ONLY)

**Audit Date:** October 8, 2026  
**Auditor / Agent:** Senior Security & Full-Stack Engineering Agent  
**Scope:** Read-only security audit of Authentication, Authorization, Session Handling, IDOR, CSRF, and Tenant Access controls across LedgerPulse ERP.  
**Classification:** READ-ONLY SECURITY AUDIT REPORT  

---

## 1. Executive Summary

This audit evaluates the security posture of LedgerPulse ERP with respect to user authentication, role-based authorization, user-to-organization membership controls, IDOR protections, CSRF defenses, and session security.

Following Foundation Tasks 1–3 (which established tenant data isolation via `organization_id`, Decimal-safe monetary arithmetic, and accounting document integrity), LedgerPulse's core architectural state is **unauthenticated and single-tenant/open-access by design**. 

### Key Findings Summary:
1. **No Authentication System:** There are no user accounts, no login/logout routes, no password hashing, and no user sessions.
2. **No Authorization / Role System:** There are no user roles, permissions, administrative privileges, or permission decorators. All users (visitors) have full access to all features.
3. **Unrestricted Organization Switching:** While Foundation Task 1 correctly scopes database queries to the active `organization_id`, any visitor can switch the active organization to any tenant ID in the database via `/organizations/switch/<id>` without user membership verification.
4. **No CSRF Protection:** POST, PUT, and DELETE operations lack anti-CSRF tokens or framework-level CSRF protection (e.g. Flask-WTF).
5. **Development Secret Key Fallback:** `app.secret_key` falls back to a weak default (`"dev-secret-change-me"`) if the `SECRET_KEY` environment variable is not explicitly set.

---

## 2. Authentication Findings

- **Login / Logout Systems:** **ABSENT.** There are no login, registration, or logout endpoints in the codebase.
- **User Representation & Tables:** **ABSENT.** There is no `users` table or user model in any module schema.
- **Password Storage & Hashing:** **ABSENT.** No password hashing libraries (e.g., `werkzeug.security`, `bcrypt`) are imported or used.
- **Protected Routes & Session Handling:** 
  - Routes are protected only by `@require_organization`, which checks if `session.get("organization_id")` is present. If not, it redirects to the organization picker (`/organizations/pick`), which lists all organizations.
  - Unauthenticated visitors can access all ERP modules and pages directly by URL, provided an organization is active in their session.
- **Secret Key:** Configured in `app.py` as:
  ```python
  app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
  ```
  If `SECRET_KEY` is omitted from the environment, sessions are signed with a predictable default key.

---

## 3. Authorization Findings

- **Roles & Permissions:** **ABSENT.** There are no roles (`Admin`, `Accountant`, `Manager`, `Viewer`), permissions, or access-control lists (ACLs).
- **Module & Document Access:** Any visitor can create, read, update, post, or settle sales invoices, vendor bills, customer receipts, vendor payments, manual journal entries, WHT tax rates, and sales tax rates.
- **Organization Management:** Any visitor can create new organizations (`/organizations/new`), edit organization details (`/organizations/<id>/edit`), and manage organization master lists.

---

## 4. Organization Access / Session Audit

- **Organization Selection & Switching:** Stored in Flask session via `session["organization_id"]`. Swapped via `/organizations/switch/<int:organization_id>`.
- **Membership Verification:** **ABSENT.** There is **no user-organization membership table** or user association mapping. 
- **Security Implication:** While data queries are correctly scoped to `organization_id` (satisfying tenant data isolation #1), there is **no user authorization check** (#2). Any session can arbitrarily switch to any organization ID in the database and read/write that organization's financial records.

---

## 5. IDOR / Direct URL Access Audit

- **Record-Level Isolation:** Foundation Task 1 successfully added tenant scoping (`WHERE organization_id = %s` or FK validation against `organization_id`) across almost all queries in accounting, sales, vendors, customers, wht, and organizations.
- **IDOR Risk within Active Tenant:** Within an active organization session, sequential ID guessing (e.g. `/sales/invoices/1`, `/accounting/journal-entries/5`) is guarded by scoped queries (`WHERE ... AND organization_id = %s`), returning `404 Not Found` for records outside the active organization or nonexistent IDs.
- **Cross-Tenant Access via Session Switching:** Because organization switching requires no user membership verification, an attacker can simply post to `/organizations/switch/<target_org_id>` to adopt another tenant's context and access their isolated data.

---

## 6. CSRF / Form Security Audit

- **CSRF Protection:** **ABSENT.** Flask-WTF, Flask-SeaSurf, or manual CSRF token validation is not implemented across any forms or POST endpoints.
- **Risk:** All POST routes (such as posting invoices, paying vendors, receiving customer payments, posting manual journals, and switching organizations) are vulnerable to Cross-Site Request Forgery (CSRF).

---

## 7. Password & Session Security Findings

- **Password Hashing:** Not applicable (no passwords).
- **Session Cookie Flags:** Cookie attributes (`HttpOnly`, `Secure`, `SameSite`) rely on default Flask/Werkzeug behavior and are not explicitly hardened in configuration.
- **Debug Mode:** `app.run(host="0.0.0.0", port=5000, debug=True)` runs with Flask debug mode enabled by default in `app.py`.

---

## 8. Risk Classification

| Finding ID | Finding Description | Category | Severity |
|---|---|---|---|
| AUTH-01 | Complete absence of user authentication and login system | Authentication | **CRITICAL** |
| AUTH-02 | Unauthenticated open access to all ERP modules and financial data | Access Control | **CRITICAL** |
| AUTH-03 | Absence of user-to-organization membership verification during organization switching | Authorization / Tenant Isolation | **HIGH** |
| AUTH-04 | Absence of CSRF protection on all state-changing POST endpoints | CSRF | **HIGH** |
| AUTH-05 | Fallback to weak default secret key (`dev-secret-change-me`) when `SECRET_KEY` env var is unset | Session Security | **MEDIUM** |
| AUTH-06 | Absence of explicit session cookie security hardening flags (`HttpOnly`, `Secure`, `SameSite`) | Session Security | **MEDIUM** |
| AUTH-07 | Flask debug mode enabled by default (`debug=True`) | Configuration | **MEDIUM** |

---

## 9. VERIFIED FACTS
- The codebase contains zero user tables, authentication routes, password hashing mechanisms, or login/logout views.
- Organization context is stored entirely in Flask session (`session["organization_id"]`) without user membership checks.
- Foundation Task 1 tenant filtering (`organization_id = %s`) successfully isolates data queries per organization, but does not restrict *which* session can select *which* organization.
- No CSRF protection mechanism is present in dependencies or routes.
- `app.secret_key` uses `os.getenv("SECRET_KEY", "dev-secret-change-me")`.

---

## 10. INFERENCES
- LedgerPulse in its current repository state is designed as a single-user / local prototyping environment or a foundational backend skeleton where application-level authentication and role management were intentionally deferred to a future hardening phase.
- Implementing robust multi-user authentication, user-organization membership mapping, and CSRF protection will be required before multi-tenant production deployment.

---

## 11. NOT TESTED
- Live penetration testing or exploitation of missing CSRF tokens.
- Cookie interception or session hijacking against HTTPS/HTTP transport layers.
- Concurrent user session race conditions.

---

## 12. Recommended Remediation Plan (Future Phase)
When authentication and authorization development is officially approved:
1. **Implement User Authentication:** Create a `users` table with Werkzeug password hashing (`generate_password_hash`, `check_password_hash`), login routes, and session management.
2. **Implement User-Organization Membership:** Create an `organization_users` (membership) mapping table linking users to allowed organizations with assigned roles (e.g. Owner, Admin, Accountant, Viewer).
3. **Enforce Tenant Authorization:** Update organization switching and context verification to ensure the logged-in user is an authorized member of the target organization.
4. **Implement CSRF Defense:** Integrate Flask-WTF or a robust CSRF token mechanism across all forms and state-changing endpoints.
5. **Harden Session Security:** Enforce mandatory `SECRET_KEY` environment validation, secure cookie flags, and production WSGI server execution (disabling Flask debug mode).
