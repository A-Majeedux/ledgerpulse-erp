# LedgerPulse ERP — PROJECT.md

> **Paste this file at the start of every AI session.** It is the single source of truth.
> If anything here is wrong, fix THIS file first, then continue.
> Last updated: 2026-11-03

---

## 1. What this is

A web-based, multi-organization accounting/ERP system for Pakistan (NTN, STRN, CNIC fields; WHT on supplies/services).
Owner is an accountant (not a developer). Reference ERPs: Odoo, Sage, Tally.

**Golden rule:** accounting correctness and data integrity come before looks or new features.

## 2. Stack

- Python / Flask (Blueprints), PostgreSQL (Neon), Tailwind + `static/tokens.css`
- Dev environment: GitHub Codespaces
- Database access: plain `psycopg2` via `core/db.py::get_conn()` (one connection per request, always closed)
- Config via environment variables: `DATABASE_URL`, `SECRET_KEY`, `FLASK_ENV`, `FLASK_DEBUG` (`.env` file locally, never committed)

## 3. How to run

```
pip install -r requirements.txt
# create .env with DATABASE_URL=... and SECRET_KEY=...
python app.py            # starts on port 5000
python -m pytest         # NOTE: tests use the real DB set in DATABASE_URL — use a SEPARATE test database
```

## 4. Folder map

| Folder | Purpose |
|---|---|
| `app.py` | Starts app, registers blueprints, CSRF + login + subscription checks on every request |
| `core/` | `db.py` (connection, schema loader), `money.py` (Decimal helpers), `template_context.py` |
| `auth/` | Login, setup, roles/permissions, platform admin, subscriptions |
| `organizations/` | Multi-organization, users, roles |
| `accounting/` | Chart of accounts, journal entries, bank transfers, **`services.py` = the only place journal entries are created/posted** |
| `vendors/` | Vendors, bills, credit notes, payments (with WHT), batch payments, print templates |
| `customers/` | Customers, customer receipts |
| `sales/` | Products, sales tax rates, sales invoices |
| `wht/` | Withholding tax rates |
| `dashboard/` | Home dashboard |
| `templates/` | One sub-folder per module; `base.html` is the layout |
| `tests/`, `test_*.py` | Tests (to be consolidated into `tests/`) |
| `docs/archive/` | Old AI-generated reports (history only — do not trust as current status) |

## 5. Non-negotiable rules (every AI tool must follow)

1. **All journal entries go through `accounting/services.py`** (`create_journal_entry`, `post_journal_entry`). Never `INSERT INTO journal_*` anywhere else.
2. **Debits must equal credits** before anything is saved. The service enforces this; never bypass it.
3. **Money = `Decimal`** via `core/money.py`. Never `float` for amounts.
4. **Every query filters by `organization_id`.** No data may leak between organizations.
5. **Draft → Posted workflow.** Posted entries are not edited; they are reversed.
6. **Schema changes are additive only** (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Never DROP in app code.
7. **New accounting treatment, Dr/Cr pattern, account mapping or tax behavior = STOP and ask the owner first.**
8. **One module per session.** Do not touch other modules unless asked. Show changed files in full.
9. After any change, run the tests and tell the owner exactly what passed/failed.

## 6. Database tables (from the schema files)

- **Auth/orgs:** users, organizations, organization_memberships, roles, permissions, role_permissions, subscription_plans, plan_features, organization_subscriptions, organization_payments, clients, audit_logs, user_dashboard_preferences
- **Accounting:** chart_of_accounts, journal_headers, journal_lines, bank_transfers
- **Vendors:** vendors, vendor_bills, vendor_bill_lines, vendor_credit_notes, vendor_credit_note_lines, vendor_payments, vendor_payment_allocations, batch_vendor_payments, batch_vendor_payment_lines, payment_voucher_counters
- **Customers/Sales:** customers, customer_receipts, products, sales_invoices, sales_invoice_lines, sales_tax_rates
- **Tax:** tax_rates (WHT)

Default accounts seen in code: 1000 Cash in Hand, 1010 Bank Account, 1100 Trade Receivables, 2000 Trade Creditors, 2100 WHT Payable – Supplies, 2110 WHT Payable – Services, 4000 Sales Revenue, 5000 Cost of Goods Sold.

## 7. Module status (honest)

Legend: ✅ works as far as reviewed · 🟡 exists but needs checking/fixing · ❌ not built

| Module | Status | Notes |
|---|---|---|
| Login, setup, roles, org isolation | ✅ | Has the most tests (about 40) |
| Platform admin / subscriptions | ✅ | |
| Chart of accounts | 🟡 | Basic list/add only. Odoo-style hierarchy not built |
| Journal entries | 🟡 | Basic form. Planned grid redesign not built |
| Bank internal transfer | 🟡 | Exists, needs review |
| Vendors: master | 🟡 | List/new/detail work. Edit page reuses the New Vendor form (works) |
| Vendors: bills, credit notes, payments, batch | 🟡 | Most complete module. Claimed done; verify by running tests |
| Customers + receipts | 🟡 | Customer card rebuilt 14 Nov 2026 (ID, Edit, search, Columns menu — section 13). Receipts unchanged, older pattern |
| Sales invoices | 🟡 | Invoices only. No quotation → order → delivery chain |
| Purchases (RFQ → PO → Receipt) | ❌ | |
| Inventory | ❌ | |
| Fixed assets | ❌ | |
| Bank reconciliation | ❌ | |
| Reports | ✅ | Trial Balance and General Ledger done; Partner Ledger not built yet |

## 8. Voucher numbering (per old status report, re-verify)

- Vendor bills `<JOURNAL CODE>/YYYY/MM/NNNN`; credit notes `BCN/YYYY/MM/NNNN`
- Payments `BPV` / `VPV` / `CPV` + `/NNNNN`; batch payments `BVP/YYYY/NNNN`
- Planned: BRV, CRV, JV, Sales Invoice, Purchase Invoice formats

## 9. Dr/Cr posting rules (OWNER TO FILL IN, accountant-approved only)

| Transaction | Debit | Credit |
|---|---|---|
| Vendor bill (posted) | | |
| Vendor credit note | | |
| Vendor payment (with WHT) | | |
| Customer invoice | | |
| Customer receipt (with WHT/GST) | | |
| Bank internal transfer | | |

## 10. Build order (do not skip ahead)

1. Clean up repo, commit to GitHub, separate test database
2. Run all tests, fix failures
3. **Trial Balance report** (must always net to zero)
4. Finish Vendors (verify each flow)
5. Customers + Sales invoices to the same standard
6. General Ledger + Partner Ledger
7. Then: purchases chain, inventory, fixed assets, bank reconciliation

## 11. Known problems (update as you fix)

- Old status reports claimed "all tests passed" but the test cache showed failures. Re-run to confirm
- No `requirements.txt` until now
- Tests write to the real database
- `SECRET_KEY` falls back to a dev value and debug is on by default. Set env vars before going live

## 12. Session log (add one line per working session)

- 2026-11-03 — Repo reviewed, cleanup started, PROJECT.md created
- 2026-11-08 — Cleanup done, Trial Balance built and balanced
- 2026-11-14 — Customer card: ID, the new boxes, an Edit screen with Active / Inactive, the list with search and the
  Columns tick-box menu; the rules in the new `customers/services.py` (see section 13)

## 13. The customer card (built 14 Nov 2026)

Built from the owner's pictures of his own customer screens, in ZYRO's look. Only the `customers` table is written;
no journal entry is created or changed.

**The card.** ID, Person / Company, Name, Email, Phone, the address in parts (Street, Street 2, City, State, ZIP,
Country), Region, Territory, Category Parent, Category, LIC Number, LIC Expiry Date, NTN, STRN, CNIC, Nature of supply,
Credit limit. There is an **Edit** screen, with Status **Active / Inactive**.

**The rules** (`customers/services.py`; the pages in `customers/routes.py` only show and save):

- **ID.** A running number inside each company (1, 2, 3…), given when the customer is saved, and never changed.
  Customers saved before this version are numbered oldest first the first time it started. A unique index,
  `customers_id_per_organization` on (company, ID), makes sure no two customers of a company share an ID.
- **Wrong input is refused with a message**, and nothing that was typed is lost: a missing name, text too long for
  its column, a wrong email, a wrong date, a credit limit that is negative or above 999,999,999,999.99. All the
  mistakes are listed together.
- **Two name rules**, both because a journal line remembers its customer by *name*: a second customer with the same
  name in the same company is refused, and the name is locked once the customer has a receipt, an invoice or a
  journal line on Trade Receivables (account 1100). Both rules can go when journal lines store the customer's ID.
- **Inactive** keeps the customer on the list (marked Inactive) with its history and its balance; it is no longer
  offered when a receipt, invoice or journal entry is entered. Receipts and sales already read only active
  customers, so nothing had to change there.
- **The list** has 19 columns and shows 11 of them first (ID, Name, Credit Limit, Email, Phone, Region, Territory,
  LIC Number, LIC Expiry Date, Category Parent, Country). The **Columns** button opens a menu of tick-boxes that
  shows or hides each column; the choice is remembered in that browser only. Search finds a customer by name,
  region, territory, city, both categories, NTN, CNIC, LIC number, phone, email or ID, and every typed word must be
  found.

**Database change — additive only.** 15 columns added with `ADD COLUMN IF NOT EXISTS`, the ID given to the customers
that had none, and one new unique index. Nothing is dropped, renamed or rewritten.

**Still open from his customer form:** Tags, Is Vendor, Currency, Website, the contact cards, the totals on top
(Sales, Invoiced, Due) and the tabs. LIC Number and LIC Expiry Date are on every company's card for now — the owner
was asked whether they are for Hextar only.

**Checks.** `tests/test_customer_card.py` (29 checks, the rules without a database and the screens on one), plus
ZYRO's own `tests/` (51 passed; `test_vendors.py::test_10_all_print_templates_and_routes` failed before this work
and still fails — it looks for the words "INVOICE LINES" on the vendor bill print, which the template writes as
"Invoice lines"). Rehearsed on a database built and filled by the previous version: none of its rows changed; the
15 columns and the index were added; every customer got its ID oldest first; the old blank-named customer showed as
"(no name)" and could be given a name; two customers that already shared a name both stayed; the name of a customer
with a receipt was locked. Not done: a run on the real (Neon) database.
