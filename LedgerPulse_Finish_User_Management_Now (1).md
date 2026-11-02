# LedgerPulse ERP --- Finish User Login, User Management & Task Authorization

## Objective

Complete the existing implementation in `/workspaces/ledgerpulse-erp`.
Do not restart the project, create another auth system, or write
documentation instead of code. Inspect the current repository, determine
what already exists, implement only the gaps, and verify the result.

## Read first

1.  `GEMINI.md`
2.  `LEDGERPULSE_BUILD_BRIEF.md`
3.  Existing auth/organization/subscription/accounting routes, services,
    schemas, templates, navigation, and tests.
4.  Any current user/role implementation and prior implementation notes.

## Hard safety requirements

-   No commits or pushes.
-   Do not run `reset_accounting_tables.py` or destructive SQL.
-   Do not drop, truncate, reset, or mass-delete tables or records.
-   Do not change production data or apply production migrations without
    explicit approval.
-   Do not print or expose `.env`, credentials, passwords, hashes,
    session secrets, or database URLs.
-   Use an isolated test database for state-changing database tests. If
    isolation cannot be confirmed, do not run those tests.
-   Preserve current Flask/PostgreSQL/Jinja architecture and existing
    work.
-   Keep tenant isolation by `organization_id`.
-   Keep Platform Admin separate from Organization Admin.
-   Never allow an organization user to grant platform privileges.
-   Preserve access order: authentication → organization membership →
    subscription → feature entitlement → role → permission.
-   Preserve existing accounting rules and central journal posting
    through `accounting/services.py::create_journal_entry()`.
-   Posted accounting documents must not be silently edited/deleted.
-   Tax/WHT selection remains manual. Delivery and Goods Receipt do not
    create accounting entries; inventory/COGS remains deferred.
-   Keep the approved UI palette: `#DDEFE8`, `#278C83`, `#FAFAF6`,
    `#8298B2`, `#243448`.

## Required end-user workflow

An authorized Organization Admin must be able to: 1. Open Organization →
Users (or the equivalent existing route). 2. View/search users in their
current organization. 3. Add a user with name, email/username, and role.
4. Assign or change the user's role. 5. Activate/deactivate a user. 6.
Open Roles & Permissions. 7. Create a custom organization role or edit a
permitted custom role. 8. Select granular permissions per task and save.
9. Review a role's effective permissions. 10. Be prevented from managing
another organization's users/roles by changing URL or submitted IDs.

Use the existing interface conventions. Do not redesign unrelated pages.
If the UI already exists, fix its gaps rather than duplicating it.

## Roles and granular permissions

Reuse the current schema and permission keys where possible. Do not
assume `role_id = 1` means Admin. Resolve roles through stable
identifiers/names and scope; fail safely if missing.

Provide or map sensible roles only if missing: - Organization Admin:
organization-scoped user/role management and permitted organization
tasks; no platform administration. - Accounts Manager: permitted
accounting, review/posting, and reporting tasks. - Accounts Clerk:
permitted draft creation/editing; no posting/approval unless explicitly
granted. - Sales Manager: authorized sales workflows within the
organization. - Sales Officer: authorized sales creation and draft
operations. - Purchase/Vendor Officer: permitted vendor/purchase
workflows. - Read Only/Auditor: view-only. - Custom Role: administrator
chooses permissions from a supported catalogue.

Use actual route/action names discovered in the repository. Suggested
permission concepts (adapt to existing keys and actual features): -
`organizations.users.view`, `.create`, `.edit`, `.activate` -
`organizations.roles.view`, `.manage` - `accounting.journal.view`,
`.create`, `.edit_draft`, `.submit`, `.post`, `.reverse` -
`accounting.reports.view`, `.export` - `sales.invoice.view`, `.create`,
`.edit_draft`, `.post` - `customers.view`, `.create`, `.edit` -
`vendors.view`, `.create`, `.edit` - `vendors.bill.create`, `.approve` -
`vendors.payment.create` - `wht.view`, `wht.configure` - `tax.view`,
`tax.configure` - `reports.view`, `reports.export`

Do not create grants for nonexistent features. Do not automatically
grant every permission to new users. Distinguish viewing, creating,
editing drafts, submitting, approving/posting, exporting, and
configuration. Do not grant delete access to posted accounting records
contrary to policy.

## Server-side authorization

Use the existing `auth/services.py` permission helpers or extend them.
For every protected route/action: - Confirm authenticated and active
user. - Resolve active organization from trusted server-side context. -
Verify membership in that organization. - Apply existing subscription
and feature-entitlement checks. - Resolve effective role/permissions for
this user in this organization. - Enforce the permission required for
the specific operation/HTTP method. - Fail closed on missing context,
invalid role, database errors, and unknown permission keys. - Return
safe 403/redirect behavior without leaking another organization's data.

Protect list/detail, create/edit, submit/post/approve/reverse,
exports/downloads, user/role changes, AJAX endpoints, and bulk
operations. Hiding navigation links is not sufficient.

Scope every tenant-owned lookup by both record ID and `organization_id`.
Validate submitted account/customer/vendor/product/tax/WHT/other
foreign-key IDs against the current organization. Use parameterized SQL.
Return safe not-found/denied responses for cross-tenant IDs.

## User and role security

-   Secure password hashing; never store plaintext passwords or log
    passwords.
-   Use an existing invitation/set-password flow if present. Otherwise
    use the current secure account-creation convention and require
    password change on first login if feasible without inventing email
    delivery infrastructure.
-   Protect all state-changing forms with CSRF.
-   Validate emails/usernames and role/permission input server-side.
-   Prevent privilege escalation, self-escalation, cross-organization
    role assignment, and platform-only permissions in tenant roles.
-   Protect the last active Organization Admin against deactivation,
    membership removal, or demotion.
-   Ensure deactivated users are blocked promptly and access changes
    take effect promptly.
-   Audit user creation, role assignment/change,
    activation/deactivation, role/permission changes, and sensitive
    platform actions using the existing audit design.
-   Do not allow tenant workflows to deactivate/remove the final active
    Platform Admin.
-   Safe redirects only; do not accept arbitrary external redirect URLs.
-   Do not reveal whether an email exists in public recovery flows.

## Schema and migrations

Inspect the real existing schema code first. Reuse existing tables. Make
only additive, idempotent schema changes (`CREATE TABLE IF NOT EXISTS`,
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) consistent with the project.
Do not guess column names. Do not create duplicate conflicting
role/membership systems. If a required schema change cannot safely be
applied, implement the migration code but clearly report that it has not
been applied to a database.

## Minimum tests

Add/run tests for: 1. Unauthenticated and inactive users are denied. 2.
Organization Admin can add/manage users only in their organization. 3.
Cross-organization URL/foreign-key tampering is denied. 4. Read-only
users cannot write, post, approve, configure, or delete. 5. Clerk can
create an allowed draft but cannot post without permission. 6.
Authorized manager can post only after accounting validation. 7. Users
cannot self-escalate or grant platform privileges. 8. Unknown permission
keys and unauthorized role assignment are rejected. 9. Last active
Organization Admin cannot be deactivated, removed, or demoted. 10. Role
changes and deactivation take effect promptly. 11. CSRF is required for
mutations. 12. Subscription/feature restrictions remain independent of
role permissions. 13. Posted accounting documents remain protected. 14.
Existing tests continue to pass.

Run the existing project test suite (normally `pytest -q`) and
`python3 -m py_compile` on repository Python source/test files. Report
exact commands and outputs. Clearly distinguish mocked/unit tests from
real isolated-database integration tests. Do not claim browser
verification unless performed. Fix the existing `datetime.utcnow()`
deprecation warning only if a small compatible change is safe for
current database expectations.

## Execution order

1.  Inspect repository and current implementation.
2.  Report a concise internal map of existing files/helpers to reuse,
    then proceed with implementation; do not stop after the map.
3.  Implement only missing schema, routes, services, templates,
    navigation, and tests.
4.  Run checks and inspect the diff.
5.  Stop and report. Do not commit/push.

## Definition of done

The task is complete only when: - An Organization Admin can add an
organization user and assign a role through the UI. - An authorized
administrator can create/edit a custom role and choose task-level
permissions. - Actual backend routes enforce those permissions. - Tenant
isolation and privilege-escalation safeguards are tested. - Existing
accounting and subscription rules still apply. - Tests and syntax checks
have exact reported results. - Remaining unverified database/browser
steps are explicitly identified.

## Final response format

List: - Implemented versus already-existing behavior. - Files
created/changed and their purpose. - User-management and role-management
routes/pages. - Permission keys and role mappings. - Exact tests run,
pass/fail counts, warnings. - Whether database integration and browser
testing actually occurred. - Any remaining blockers. - Confirm no
destructive operations, production changes, commits, or pushes occurred.
Do not claim "complete" if critical permissions or tenant checks are
missing.
