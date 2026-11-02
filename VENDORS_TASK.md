# VENDORS_TASK.md — Autonomous build brief for Gemini CLI

You are a senior Flask / PostgreSQL developer working inside the repo `ledgerpulse-erp` (GitHub Codespaces). Your job is to **build and complete the Vendors section** of this ERP. In the app's top navigation this section lives under the **"Purchases"** menu.

The product owner (Abdul) is an accountant, not a developer. He cannot debug code for you. Work independently, test your own work, and report clearly.

---

## 1. HARD RULES (read first, obey always)

1. **Scope.** Change only what belongs to the Vendors / Purchases section. Do NOT change behavior of Sales, Customers, Accounting, Dashboard, Auth, Organizations, or WHT modules. Allowed exceptions: registering your Blueprint(s) in `app.py`, adding menu links in `templates/base.html`, and adding new tables/columns in `core` or `common.py` schema setup.
2. **Never delete or rewrite working code.** Extend it. If a file must change, make the smallest safe edit. Never remove existing routes, templates, or columns.
3. **Respect module boundaries.** The app uses Flask Blueprints. Keep vendor code inside the `vendors/` folder (routes, helpers) and `templates/vendors/`. Reuse shared helpers from `core`/`common.py` instead of copying them.
4. **Database = PostgreSQL (Neon).** Use PostgreSQL syntax only. Schema changes must be additive and safe to run repeatedly: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Never `DROP`, never `TRUNCATE`, never delete existing data.
5. **Multi-organization.** The app has an organization switcher. Every new table must carry the organization id and every query must filter by the current organization, exactly like existing modules do.
6. **Money correctness.** Use `Decimal`/`NUMERIC` (never float) for amounts. Every posted voucher must create a **balanced** journal entry (total debit = total credit). Base currency is PKR.
7. **Styling.** Reuse the existing look (navy `#142b3a`, teal `#17a598`, canvas `#f5f8f7`, font Inter, Tailwind classes already used in `templates/`). Match the existing dashboard cards, tables, and status pills. No new UI frameworks or heavy dependencies.
8. **No secrets.** Never print, edit, or commit `.env`.
9. **Git safety.** Work on a new branch named `vendors-build`. Commit after each finished phase with a clear message. **Never push, never force anything, never rewrite history.**
10. **Do not ask questions** unless you are completely blocked. If something is unclear, pick the most standard ERP/accounting behavior, and write the assumption in `VENDORS_STATUS.md`.

---

## 2. HOW TO WORK

Work in phases, in order. After each phase:

1. Start the app and confirm it boots with no errors.
2. Smoke-test every new page and route (use Flask's test client, or `pytest` if tests exist). Pages must return 200 and forms must save.
3. For any posting action, verify the journal entry balances.
4. Update `VENDORS_STATUS.md` (create it at repo root): phase name, what was done, files changed, tests run, assumptions, known gaps.
5. Commit. Then move to the next phase.

If a phase fails after 3 honest attempts, log the problem in `VENDORS_STATUS.md`, skip to the next phase, and continue.

---

## 3. PHASE 0 — Audit (no code changes)

Read the codebase: `app.py`, `core/`, `accounting/`, `vendors/`, `wht/`, `templates/`, and the existing `*.md` status files in the repo root. Then write `VENDORS_AUDIT.md` listing, for each feature in Section 4:

- **EXISTS and works**
- **EXISTS but incomplete or buggy**
- **MISSING**

Also record: the table names used for vendors, bills, payments, journal entries/lines, chart of accounts, and WHT; how voucher numbers are generated today; and how the Customers module does its payment voucher (use it as the pattern).

Then continue straight to Phase 1. Do not wait for approval.

---

## 4. TARGET FEATURES

The reference is Abdul's real Odoo system. Replicate the **behavior and workflow**, not the Odoo visuals. The "Purchases" menu should contain these items, in this order:

**Bills · Credit Notes (Refunds) · Payments · Batch Payments · Own Cheques · Vendors**

### Phase 1 — Vendors (master list + card)

**List page** (search + pagination, 80 per page): ID, Name, Credit Limit, Email, Phone, Region, Territory, Category, Country, tags. Search by name, phone, NTN, STRN, CNIC. Real data can exceed 2,000 vendors, so searching and paging must be efficient.

**Vendor card (form)**:
- Person / Company toggle, Name, Email, Phone
- Address: Street, City, State, ZIP, Country
- NTN, STRN, CNIC, Website, Category, Tags, Region, Territory
- "Is Vendor" and "Is Customer" checkboxes (one party can be both)
- **Nature of purchase: Supplies or Services** (this decides which WHT Payable ledger is credited on payment)
- Currency (PKR), Balance (read-only, calculated from the vendor ledger)
- Tabs:
  - *Contacts* (extra contact people)
  - *Purchase* (Payment Terms, default Payment Method)
  - *Accounting* (Default Purchase Journal, Account Payable, Bank Accounts of the vendor)
  - *Notes*
  - *Credit Limit*
- Smart-button style links on the card: Bills (count), Payments (count)

Do NOT store a WHT rate on the vendor. Rates are chosen manually per transaction.

### Phase 2 — Vendor Bills

**List page**: Number, Vendor, Bill Date, Due Date, Reference, Untaxed Amount, Total, Status (Draft / Posted / Paid). Filters: date range, vendor, status. Search by number, vendor, reference, LC reference, total. Paging.

**Bill form**:
- Type toggle: Bill / Receipt
- Vendor (searchable dropdown), Bill Reference, LC Reference
- Bill Date, Receipt Date, Accounting Date, Due Date (or Payment Terms)
- Payment Reference, Recipient Bank
- Journal (e.g. Expenses / Purchase), currency PKR
- Status bar: **Draft → Posted**, plus a **PAID** ribbon once fully paid
- Buttons: **Confirm** (post), Reset to Draft, Credit Note, Print, Duplicate, Delete (Draft only)
- Tabs: **Invoice Lines**, **Journal Items** (read-only Dr/Cr view), **Other Info**
- **Invoice lines** are an editable grid: Label, Account (searchable by code or name, with a "search more" option), Quantity, Unit, Price, Taxes (optional), Amount. Buttons under the grid: Add a line, Add a section, Add a note. Totals shown: Untaxed Amount, Total, Amount Due.
- Bill number format: `BILL/YYYY/MM/NNNN` (auto, sequential, never reused).

**Posting rule on Confirm**:
- Debit: each line's expense/asset account for its line amount
- Credit: Trade Creditors (Accounts Payable) for the bill total, with the vendor as partner
- The entry must balance. Bill status becomes Posted and the lines lock.

Bills created from a Purchase Order (if that feature exists) must appear in the same list.

**Print**: a clean printable Vendor Bill page (company header, vendor, dates, lines table, total) with signature blocks: *Prepared By, Checked By, Approved By, Authorized By*, and a Notes area. Also a printable Journal Entry view for the bill.

### Phase 3 — Vendor Credit Notes (Refunds)

- Created from a Posted bill with one click, or standalone.
- Same layout as a bill. Status Draft → Posted.
- Posting reverses the bill logic: **Debit Trade Creditors, Credit the expense/asset accounts**.
- Show the vendor's open bills so the credit note can be applied against a chosen bill.
- Printable.

### Phase 4 — Vendor Payments

**List page**: Date, Voucher No., Reference Number, Journal (bank/cash), Vendor, Amount, Status (Draft / In Process / Paid). Filters and paging.

**Payment form**:
- Payment Type: Send / Receive
- Vendor, Amount, Currency PKR, Date, Memo
- Journal: Cash in Hand or any bank account (from the existing bank journals)
- Source Document (free text, e.g. original PR reference)
- Voucher Number: auto. **BPV-NNNNN** for bank payments, **CPV-NNNNN** for cash payments
- Payment Method: **Manual Payment** (IBFT / online / cash) or **Cheque**
- LC Reference, Vendor Bank Account
- Optional analytic/segment tag (only if the project already has analytic tags; otherwise skip and log it)
- Status bar: **Draft → In Process → Paid**, with a **Confirm** button

**When method = Cheque**, show a Cheques grid: Cheque Number (auto-suggest the next number for that bank), Label, Cheque Date, Amount.
- **The system MUST block a duplicate cheque number within the same bank account.** Show a clear error.

**When the "Withhold Tax" checkbox is ticked**, show a Withholding grid: Sequence/NTN reference, Withholding Base amount, **Tax (picked manually from the Tax Rates master list)**, Withheld Amount (auto = base × rate), optional analytic tag.
- Example: base 20,000 at 5% → withheld 1,000 → net paid 19,000.
- Do **not** auto-select the rate, do not check exemptions, do not enforce thresholds. The bookkeeper decides.
- Reuse the existing `wht/` module and its tax-rate master if present. If no master list exists, create a simple admin screen (add/edit rate name + percent).

**Posting rule on Confirm** (when WHT applies):
- Debit: Vendor ledger (Accounts Payable) for the **full base amount**
- Credit: **WHT Payable** (Supplies or Services variant, based on the vendor's nature of purchase) for the tax amount
- Credit: Bank or Cash for the **net amount** (base − WHT)

Without WHT: Debit Accounts Payable, Credit Bank/Cash for the amount.

**Actions menu**: Print, Duplicate, Delete (Draft only, with a confirmation popup), Change to Paid, plus links to the Journal Entry and to payment matching (allocate the payment against open bills).

**Print — Bank Payment Voucher**: Voucher No., Vendor, Bank, Payment Method, Date, Memo; a Cheque table (number, label, date, amount); a Withholding table (tax name, withheld amount, net amount paid); **Total Amount Paid**; signature blocks.

**Allocation:** allow a payment to be matched against one or more open bills, updating each bill's Amount Due and flipping it to **Paid** when fully settled.

### Phase 5 — Batch Vendor Payments

- List: Reference (`BVP/YYYY/NNNN`), Payment Date, Journal, Payment Method (Manual Payment or Own Cheques), Total Amount, Status (Draft / Confirmed).
- Form: Payment Date, Journal, Payment Method, Total Amount (auto-sum), and a lines grid: Vendor, Amount, Memo, Payment (link to the generated payment).
- **Confirm** creates one normal Vendor Payment (and journal entry) per line, using the batch's journal and date, and links each line to its payment.
- No WHT in batch lines (it cannot be split per vendor). Add a short note on the form saying so.
- Printable batch sheet.
- This is lower priority. Build it after Phases 1–4 and 6 are stable.

### Phase 6 — Own Cheques report

A **read-only report**, not a data-entry form. Lists all vendor payments made by cheque over a chosen period: Payment Date, Cheque Number, Bank Journal, Vendor, Amount, Issue State (e.g. Handed). Filters: date range, bank, vendor. Export to Excel (use whichever library the project already uses) and a print view.

### Phase 7 — Vendor reports and polish

- **Vendor Ledger / Partner Ledger** for a vendor: opening balance, each bill/credit note/payment with Dr/Cr and running balance, closing balance. Date range filter, print, Excel export.
- **Vendor Aging** (payables aging): current, 1–30, 31–60, 61–90, 90+ days, per vendor.
- Add these under the existing Reports area if a reports module exists; otherwise inside Purchases.
- Check empty states, form validation messages, mobile-width layout, and consistent status pills across all vendor pages.

---

## 5. OUT OF SCOPE — do NOT build

- RFQ → Purchase Order → Goods Receipt chain, Inventory, Products catalog, warehouses
- Sales, Customers, or Customer Receipt changes
- Policy codes (Hextar-specific)
- Post-dated or third-party cheque tracking
- A "WHT Clearing" account (it is an Odoo-internal artifact; do not replicate)
- Login/auth changes, deployment, or hosting changes
- Auto-selecting WHT rates or enforcing tax thresholds

If one of these already exists in the repo, leave it untouched.

---

## 6. DEFINITION OF DONE

- Every Phase 1–7 feature is either working or listed in `VENDORS_STATUS.md` under "Known gaps" with a reason.
- App boots cleanly; all existing pages still work (spot-check Dashboard, Sales, Customers, Accounting).
- Every posted voucher creates a balanced journal entry (verified by a test or script).
- Duplicate cheque numbers per bank are blocked.
- No existing code or data was deleted.
- `VENDORS_AUDIT.md` and `VENDORS_STATUS.md` are complete, and work is committed on branch `vendors-build`.

**Final message to Abdul** (plain, non-technical): what was built, how to open each screen from the menu, 5 simple steps to test a bill → payment with WHT, and anything he must decide or fix himself.
