# LedgerPulse ERP — Full Rebuild Brief

This document is the complete, consolidated specification for rebuilding LedgerPulse ERP.
It was compiled from an extensive requirements discussion between Abdul Majeed (Assistant
Manager Finance, Hextar Chemical Enterprises; CPA candidate) and Claude, referencing a real
Odoo deployment (hextar.odoo.com) as the model for behavior, and a separate reference app
for visual design. Read this whole file before writing any code — it is the single source of
truth for what to build. Do not ask Abdul to re-explain anything covered here.

## 0. Who this is for / working style
- Abdul is a non-developer with deep accounting expertise (Odoo/Sage/Tally background,
  pursuing CPA). He catches accounting-logic bugs a non-accountant would miss — trust his
  corrections on debit/credit logic without pushback, but flag genuine inconsistencies (see
  Fixed Asset Disposal below) rather than silently picking one.
- Always deliver **complete replacement files**, never partial snippets/diffs pasted as prose.
- Beginner-friendly, exact, step-by-step explanations when explaining non-code steps.
- Never erase working code / previously-functioning styling when adding features.
- Schema changes should be additive (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) except where
  this brief explicitly calls for a structural rebuild.
- Standing rule for **every voucher form**: "New X" opens a clean, isolated single-window
  form. Never render history/a list underneath an open draft. List/history is reached only via
  the voucher's own list screen or a breadcrumb.
- Pakistan-specific context throughout: NTN, STRN, CNIC fields; PKR currency; FBR withholding
  tax (WHT) rules that change every Finance Act year (hence WHT rates are never hardcoded).

## 1. Target architecture

Rebuild from scratch as a modular Flask app, one folder per business segment, so a change in
one segment never risks breaking another (the old single-file/monolithic style caused fragile
breakage in the previous iteration — avoid that pattern entirely).

```
ledgerpulse/
  app.py                 # entry point, registers all blueprints
  core/                  # db connection, schema bootstrap, shared helpers, base layout, design tokens
  organizations/         # multi-organization support + switcher (see section 10)
  accounting/            # Chart of Accounts, Journal Entries, Bank Internal Transfer, All Payments
  sales/                 # Quotation -> Sales Order -> Delivery -> Invoice, Sales Credit Notes
  purchases/             # RFQ -> Purchase Order -> Goods Receipt -> Vendor Bill (PO-linked)
  vendors/               # Vendor Bills (direct/non-PO), Vendor Payments, Batch Payments, Own Checks
  customers/             # Customer master, Customer Receipts, Batch Payments, Credit Notes
  inventory/             # Products, warehouses, stock, units of measure
  assets/                # Fixed Asset categories, assets, depreciation
  wht/                   # Tax Rates master list, WHT payable/receivable logic (shared by vendors+customers)
  banking/               # Bank accounts, Bank Reconciliation
  reports/               # Statement reports, ledgers, partner reports
  templates/             # Jinja templates, one subfolder per segment above
  static/                # design tokens CSS, shared assets
```

**Two structural fixes required from the previous codebase, before anything else:**
1. Every table a segment owns must be created idempotently in that segment's own code
   (`CREATE TABLE IF NOT EXISTS ...`). The old `common.py` referenced `chart_of_accounts`,
   `journal_headers`, and `journal_lines` via foreign keys but never created them — they only
   existed because someone set them up manually in Neon once. A fresh database must be able to
   bootstrap the entire schema from code alone.
2. Move off raw Python f-string HTML (the old `page()`/`sidebar()`/`dashboard_page()` pattern
   in `common.py`) to **Jinja templates**. Given the size of the design system and the number
   of voucher screens ahead, f-string HTML will not scale.

Stack stays: Flask (Python), Neon PostgreSQL (remember `&channel_binding=require` in the
connection string), Tailwind CSS, GitHub Codespaces.

## 2. Design system (target visual language)

Abdul explicitly confirmed this exact look as the target — not just inspiration — verified
against both a reference app's source and its live deployment.

**Tokens:**
- Colors: teal `#17a598`, navy `#142b3a`, canvas `#f5f8f7`, plus soft/status variants
  (green/amber/coral/blue) for status pills
- Font: Inter
- Radius scale: `--lp-radius-sm/md/lg/xl`
- Shadow: `--lp-shadow-panel`
- Spacing scale: `--lp-space-1` through `12`

**Components to build:**
- Dashboard hero banner: dark teal/navy gradient block ("Financial Control Center" style)
- Stat cards with soft-colored icon chips and mini sparkline bars
- Status pill system (color-coded badges: Draft/Posted/Overdue/Paid etc.)
- Clean card + table pattern for all reports (Trial Balance/P&L/Balance Sheet/Aging)

**Known bugs in the reference — fix, do not replicate:**
- "Liabilitys" typo (should read "Liabilities")
- Balance Sheet must roll retained earnings/current-year profit into Equity so
  Assets = Liabilities + Equity always holds
- Dashboard revenue-period stats must use correct date-range logic (reference showed a
  bugged "Rs0 revenue with +12.8% trend" — nonsensical, verify real logic)

## 3. Sales chain

**Quotation → (approved) Sales Order → Delivery → Invoice.** Each stage is a separate,
independently numbered/printable document, all cross-linked (e.g. a "Delivery 1" smart-link
shown on the originating quote).

- Quotation/Sales Order line items: Product, Qty, Delivered, Invoiced (running fulfillment
  tracking), Unit, Sale Price, Disc.%, Disc. Amount, Unit Price, Taxes, Amount
- Delivery fields: Delivery Address, Operation Type, Source Location (warehouse), Scheduled
  Date, Deadline, Product Availability check, Source Document link, status
  Draft→Waiting→Ready→Done, "Check Availability" action
- **Delivery posts NO accounting entries of its own** (confirmed from real data — see section 8)
- Sales Order / RFQ list views should have a dashboard-stat header strip: New / Sent /
  Late / Not Acknowledged / Late Receipt / On-Time-Delivery % / Days to Order

**Sales Invoice (final stage) fields:**
- Header: "Customer Invoice" + big doc number (e.g. `D-INV/2026/01816`), breadcrumb back to
  source Quotation/Sales Order
- Dates: Invoice Date, Accounting Date, Due Date, **and** Delivery Date (three distinct dates)
- Journal (which accounting journal it posts through) + currency
- Invoice Lines columns: Product, Batch (lot/batch tracking), Account (revenue account
  selectable per line, not fixed), Analytic, Quantity, Unit, Packaging Qty (UOM conversion,
  e.g. cartons vs. pieces), Price, Taxes, Amount
- Actions on posted invoice: Send, Print, Pay, Preview, Credit Note (one-click)
- Status: Draft → Posted

**Sales Credit Notes:** used for (a) discounts and (b) sales returns. Same line layout as the
Invoice. Draft → Posted. Has an "Outstanding debits" panel listing the customer's open
invoices, each with an ADD button, so the credit note can be applied directly against a
specific invoice.

**Explicitly out of scope:** a recurring "Policy" code seen throughout the Odoo reference
(product/pricing-tier codes like `NPL26`) is Hextar-specific organizational configuration —
do not build it as a LedgerPulse feature.

## 4. Purchase chain (mirrors Sales)

**RFQ → (approved) Purchase Order → Goods Receipt → Vendor Bill.**

- Goods Receipt status: Draft → Ready → Done (no "Waiting" stage — incoming stock isn't
  availability-blocked the way outgoing deliveries are)
- **Goods Receipt posts NO accounting entries of its own** — same reasoning as Delivery. The
  real Vendor Bill example posts the full `Dr Stock-Raw Materials / Cr Trade Creditors` entry
  directly at Bill time, so there is no need for a GRNI/clearing account.
- RFQ/PO fields: Vendor, Vendor Reference, Currency, Confirmation Date, Expected Arrival,
  Deliver To (warehouse), Segment
- PO line columns: Product, Analytic Distribution, Quantity, **Received**, **Billed**, Unit,
  Unit Price, Taxes, Amount (mirrors Delivered/Invoiced on the sales side)
- Goods Receipt fields: Receive From, Operation Type, Destination Location, Scheduled/
  Effective Date, Source Document, Operations tab (Product/Packages/Demand/Quantity/Unit)
- Vendor Bill fields: Vendor, Bill Reference, LC Reference, Bill Date, Accounting Date,
  Payment Reference, Recipient Bank, Payment Terms, Journal + currency; lines
  Label/Account/Analytic/Quantity/Unit/Price/Taxes/Amount; actions Pay/Credit Note;
  Draft → Posted
- RFQ list view: same dashboard-stat header strip as Sales Orders

## 5. Vendors module (non-PO spend: recurring bills, subscriptions, maintenance, services)

Four sub-areas: Bills, Payments, Batch Vendor Payments, Own Checks.

- **Bills**: list filterable by date/vendor. New Bill = Vendor, Bill Date, Due Date, Payment
  Terms, Journal (classifies Expense-nature vs. Purchase-nature). Lines: Label, Account
  (expense account), Qty, Rate, Total, optional Tax/Discount. Lands in the **same** Bills
  register as PO-originated vendor bills — two entry paths, one list.
- **Payments**: Vendor, Amount, Date, Memo, WHT checkbox (reveals tax panel), Bank Journal,
  Payment Method (Cheque or Manual=IBFT/cash). Cheque method reveals Cheque Number (system
  **must block duplicate cheque numbers per bank account**), Label, Cheque Date, Amount.
- **Batch Vendor Payments**: pays multiple vendors from **one** bank/channel in one batch.
  Abdul doesn't actually use this in practice (can't split WHT per vendor line) — build it,
  but treat as lower priority than everything else here.
- **Own Checks**: NOT a data-entry form — a derived/read-only report of cheque payment
  history (Payment Date, Cheque Number, Bank/Journal, Vendor, Amount, Issue State e.g.
  "Handed"). Just a filtered view over Payments, not a new table.

## 6. Customers module

- Customer contact card: NTN, STRN, CNIC, plus Credit Limit, Region, Territory.
- **GST & WHT Receivable**: Abdul's own business is GST-exempt so there's no live reference
  data, but build both anyway since this is meant to be sellable software. GST chargeable on
  invoices where applicable, rate not predefined (same manually-maintained rate-list pattern
  as WHT — see section 7). WHT Receivable needs a checkbox on the Customer Receipt voucher
  (mirrors the Vendor Payment WHT checkbox) — splits the receipt into Bank (net received) +
  WHT Receivable ledger (deducted portion) when a customer withholds tax on their payment.
- **Credit Notes**: same as Sales Credit Notes above (discounts / returns), with the
  Outstanding-debits-plus-ADD-button pattern.
- **Explicitly out of scope**: post-dated cheques and third-party cheques from customers —
  Abdul confirmed they don't use these, do not build tracking for them.
- **Batch Customer Payments**: Abdul DOES use this (opposite direction from vendor batch) —
  entering the **same customer's** payments across **different banks** in one batch. Same
  WHT-can't-split-per-line limitation applies. Fields: Payment Type (Send/Receive), Territory,
  Customer, Amount, Date, Memo, Analytic Distribution, a manual reference number, Journal
  (bank) per line, Payment Method.

## 7. WHT (withholding tax) architecture — Pakistan-specific, fully confirmed

This is the most business-critical piece — get it exactly right.

- **No tax rate is ever stored on a vendor or customer contact card.** The contact card only
  stores a nature-of-purchase classification (Supplies or Services) that routes the WHT
  credit to the correct ledger.
- The actual rate is picked **manually, per transaction**, by the bookkeeper from a **Tax
  Rates master list** that Abdul maintains himself (a simple admin screen to add/edit
  percentage values — because Pakistan's WHT rates change with every Finance Act).
- The software does **not** auto-select a rate, does **not** check exemptions, and does
  **not** enforce min/max thresholds. This is intentional — entirely bookkeeper judgment
  against current FBR rules. Do not add "smart" rate logic.
- WHT applies in (at least) four scenarios, each posting to its own ledger by
  category + Income Tax Ordinance section:
  - Supplies — U/S 153
  - Services — U/S 153
  - Salaries — U/S 149 (calculated when recording the salary-payable liability)
  - Rent — U/S 155 (calculated when paying rented property)
- WHT Payable balances are cleared via a **monthly remittance** to FBR — build a WHT
  remittance/deposit voucher (debits the relevant WHT Payable ledgers, credits Bank).
- WHT Receivable (customer side): when a customer withholds tax on their payment to us, it
  posts to WHT Receivable ledgers, mirrored by category (Supplies/Services/etc.) the same way
  as the payable side.
- Do **not** build anything resembling Odoo's internal "WHT Clearing"/"Withholding Clearing
  Account" mechanism — confirmed to be an artifact of Odoo's own tax engine with no business
  meaning to Abdul. See section 8 for what the real economic postings should look like instead.

## 8. Confirmed Dr/Cr posting rules

These come from real, live transaction screenshots (Sep 2026) reviewed against the design
above. Where two examples of the same transaction type disagreed, that's called out
explicitly — don't silently pick one.

- **Sales Invoice posted**: `Dr Trade Receivables / Cr Sales` (revenue) **plus, same entry**,
  `Dr Cost of Goods Sold / Cr Stock-Finished Goods` (COGS relief) — both post together at
  invoice time.
- **Delivery**: no accounting entry (confirmed — real Delivery doc had no Journal Items at
  all; COGS/inventory movement happens at Invoice time, not Delivery time).
- **Sales Credit Note (return)**: exact reverse of the invoice —
  `Dr Sales / Cr Trade Receivables` and `Dr Stock-Finished Goods / Cr Cost of Goods Sold`.
- **Sales Credit Note (discount)** — no goods return, so should touch only revenue/receivable:
  `Dr Sales (or a dedicated Discount Allowed account) / Cr Trade Receivables`. Recommend a
  separate Discount Allowed account to keep gross revenue visible in the P&L — confirm this
  choice with Abdul before finalizing.
- **Customer Receipt**: `Dr Bank / Cr Trade Receivables`.
- **Customer Receipt with WHT Receivable**: `Dr Bank (net received) / Dr WHT Receivable (tax
  withheld) / Cr Trade Receivables (full invoice amount)`.
- **Batch Customer Payment**: UI wrapper only — each line still creates its own individual
  Customer Receipt as above; can span multiple different banks for one customer.
- **Vendor Bill (PO-linked, raw material)**: `Dr Stock-Raw Materials / Cr Trade Creditors`.
- **Vendor Bill (direct/non-PO, expense)**: `Dr Expense Account / Cr Trade Creditors`.
- **Goods Receipt**: no accounting entry (same reasoning as Delivery — the Vendor Bill posts
  the full stock+payable entry directly; no GRNI/clearing account needed).
- **Vendor Payment (no WHT)**: `Dr Trade Creditors / Cr Bank`.
- **Vendor Payment with WHT**: `Dr Trade Creditors (full base amount) / Cr WHT Payable-
  [category] (tax portion) / Cr Bank (net amount = actual cheque/transfer)`.
- **Batch Vendor Payment**: UI wrapper only — all lines pay from the **same** bank to
  multiple vendors; each line still creates its own individual Vendor Payment underneath.
- **WHT remittance to FBR** (monthly): `Dr each WHT Payable ledger by category / Cr Bank`.
  Real example bundled three categories and three cheque numbers into one journal entry —
  support multi-line remittance JVs.
- **Salaries Payable posting**: `Dr Salary Expense (per department/analytic) / Cr WHT
  Payable-Salaries (tax withheld) / Cr Salaries Payable (net owed to employees, per
  department) / Cr other deduction payables as applicable` (real example showed a Mess
  Deduction and an EOBI contribution both credited to Trade Creditors against a related
  party). Can also include prior-month arrears as additional salary-expense debit lines.
- **WHT on Rent**: different mechanism from Vendor Payment WHT since rent isn't cleared
  through an existing payable balance first — `Dr Rent Expense (net amount paid) / Cr Bank`
  **plus** `Dr Rent Expense (WHT portion) / Cr WHT Payable-Rent`. Together these gross up
  total Rent Expense to the full contracted rent while splitting cash paid vs. tax withheld.
- **Fixed Asset purchase**: `Dr Fixed Asset Category account / Cr Bank/Cash` (or
  `Cr Trade Creditors` if entered via a Vendor Bill).
- **Annual Depreciation JV** (recommended, textbook, consistent with the category account
  mapping): `Dr Depreciation Expense (category's mapped expense account) / Cr Accumulated
  Depreciation (category's contra-asset account)`. Auto-drafted annually per asset, requires
  manual approval before posting.
- **Fixed Asset disposal — INCONSISTENCY FLAGGED, needs Abdul's decision before building:**
  - Example A (vehicle): `Dr Bank (proceeds, possibly multiple receipts) / Cr [Asset account]`
    for each receipt, then a balancing `Dr [Asset account] / Cr Gain on Disposal` for the
    difference between proceeds and book value. Did **not** touch Accumulated Depreciation.
  - Example B (furniture): `Dr Cash (proceeds) / Cr [Asset account]`; separately
    `Dr Accumulated Depreciation / Cr [Asset account]` to clear accumulated depreciation. Did
    **not** include any Gain/Loss line.
  - The textbook-correct combined approach: clear Accumulated Depreciation against the asset
    cost, **then** recognize Gain/Loss for (proceeds − net book value). Recommend building it
    this way and confirming with Abdul, rather than replicating either inconsistent example.

**Still open — no real example seen, use best judgment and flag for Abdul to review:**
credit-note discount account choice (see above), and the exact Depreciation JV line
structure beyond the Dr/Cr pair already given.

## 9. Fixed Assets module

- No separate bare "new asset, just type a name" form — asset creation is a proper setup.
- **Asset Category**: a parent grouping with its own account mapping (Asset account,
  Accumulated Depreciation account, Depreciation Expense account) plus default depreciation
  settings (method, useful life/rate, residual value — all overridable per asset). Real
  category examples from the reference Chart of Accounts: Land, Building, Plant and
  Machinery, Furniture & Fixtures, Vehicles, Computers and Accessories, Electric Installation,
  Other Fixed Assets, Office Equipments — each paired with its own
  "Accumulated Depreciation - X" contra-account.
- **Asset setup fields**: Asset Name, Category (inherits account mapping + depreciation
  defaults), Useful life/rate and Residual Value (pre-filled from category, overridable),
  Purchase/in-service date, Original cost.
- **Value entry**: an asset's opening/purchase value is posted via a Vendor Bill or a Journal
  Voucher — not a separate value field on the creation form.
- **Depreciation**: software auto-schedules an annual Depreciation JV per asset as a Draft,
  requiring manual approval/posting.
- **Assets list view**: grouped by category, columns Asset Name, Acquisition Date, Original
  Value, Method (e.g. "Straight Line"), Book Value, Depreciable Value, the three mapped
  accounts, Analytic Distribution, Status (e.g. "Running").
- **Asset Ledger report**: pick one asset, view its full posted-entry history.

## 10. Multi-organization support

Abdul wants users able to add more than one organization (separate businesses/books) and
switch between them. Default assumptions (confirm with Abdul if he wants something
different before building):
- Same login, full data isolation per organization (separate Chart of Accounts, contacts,
  vouchers — like fully separate books), with an organization-switcher in the UI.
- No cross-organization/consolidated reporting for now — that's a much bigger "consolidation
  accounting" feature, don't build it speculatively.
- Every table in every segment should carry an `organization_id` foreign key from the start,
  so this isn't a retrofit later.

## 11. Journal Entries, Bank Internal Transfer, All Payments (Accounting segment)

**Journal Entries** (`accounting/`):
- History list shows every JV — manual and system-generated from every other segment.
- "New" opens a blank Draft: Reference (searchable), LC Reference (optional), Accounting
  Date, Journal (optional, default e.g. "Miscellaneous Operations").
- Line grid core columns: Account, Partner, Label, Analytic Distribution, Debit, Credit.
- **Plus a column-visibility toggle menu** (user shows/hides, not auto-conditional):
  Deferred Date, Amount in Currency, Currency, Taxes, Asset, Discount Date, Discount Amount,
  Tax Grids. Build this as genuinely toggleable columns.
- Asset-related lines: pick the Fixed Asset account, then a separate "Asset" column to pick
  the specific asset record.

**Bank Internal Transfer** (bank-to-bank transfers or cash withdrawals — currently missing,
must be built):
- Fields: Date, Currency, Amount, Memo, Source Bank, Destination Bank.
- Generates **two** linked journal entries (Source + Destination), each separately viewable.

**All Payments** (currently missing, must be built):
- Unified view merging Journal Entries + Bank Internal Transfers into one list.
- Voucher numbering convention confirmed from real data: **BPV** = Bank Payment Voucher,
  **CPV** = Cash Payment Voucher (part of the broader BPV/BRV/CPV/CRV/JV scheme).

## 12. Bank Reconciliation

- Bank statement enters the system via **CSV/Excel import** — not manual line entry, not a
  live bank feed.
- Matching is **fully manual**, line by line — deliberately no auto-matching (cheque numbers/
  IBFT references/dates rarely line up cleanly with memo text; false auto-matches are worse
  than no matching).
- Must explicitly track **outstanding items**: cheques issued-but-not-cleared and deposits-
  in-transit, as their own view per bank account — not just a matched/unmatched flag.
- **Keep this conceptually and UI-wise separate from "Payment Matching"** (which clears AR/AP
  against invoices on the Customer/Vendor Payment screens) — different job entirely.
- **Reconciliation locking**: once a bank account is reconciled through a given date,
  transactions before that date should be locked/harder to edit, to protect the
  reconciliation from silent invalidation by a backdated entry.

## 13. Reporting

- **Statement Reports**: Balance Sheet, Profit and Loss, Cash Flow Statement, plus
  sales/collections-style reports once the Sales/Inventory layer exists.
- **Ledgers**: Trial Balance, General Ledger.
- **Partner Reports**: Partner Ledger (all/receivable/payable), Aged Receivable, Aged Payable.
- **Trial Balance**: collapsible hierarchical tree matching the Chart of Accounts (parent
  headers expand into child accounts), three columns (Initial Balance / Period Debit-Credit /
  End Balance), with a period-comparison toggle.
- **General Ledger**: filter bar (date range, journal, specific account, Analytic, Posted/
  Accrual toggle, currency), live keyword search, drill-down from account balance into
  individual transactions.
- **Partner Ledger**: transaction-level history for a single contact, filterable by
  year/account/analytic/posted-only.
- **Balance Sheet & P&L**: comparative year-over-year columns, same tree/drill-down pattern
  as Trial Balance. Export (PDF/XLSX) is a **core requirement** — Abdul's team routinely
  exports and makes manual adjustments outside the system to reach the final business
  picture, so don't build in-app adjustment/elimination logic to compensate for that; the
  export step is the intended workflow.
- **Aging**: bucket by **invoice due date**, not invoice date.
- **Cash Flow Statement**: auto-derived entirely from account **Tags** (e.g. an "Investing &
  Extraordinary Activities" tag on an account routes it into that CFS section) — no manual
  classification step. Every account needs a Tags field supporting this.
- Chart of Accounts needs: Code, Name, Type, **Allow Reconciliation** toggle (controls which
  accounts support Payment Matching), Account Currency, and the Tags field above.
- A flexible sales/purchase report-builder (filters + group-by chips + Generate/Reset +
  export) is wanted eventually but **depends on the Inventory/product layer existing first**
  — sequence it after Sales/Purchase/Inventory is built.

## 14. Recommended build sequence

1. **Foundation**: `core/` (db bootstrap with every table created idempotently in its owning
   segment, Jinja base layout, design tokens CSS), `organizations/` (multi-org + switcher,
   since every other table needs `organization_id` from day one).
2. **Accounting**: Chart of Accounts (with Tags/Allow Reconciliation), Journal Entries (with
   the toggleable-column grid), Bank Internal Transfer, All Payments. This is the foundation
   everything else posts through.
3. **Vendors + Purchases**: RFQ→PO→Receipt→Bill chain, direct Vendor Bills, Vendor Payments
   (with WHT), Batch Vendor Payments, Own Checks.
4. **WHT module**: Tax Rates master list, WHT Payable/Receivable ledger logic, monthly FBR
   remittance voucher — built once, shared by both Vendors and Customers.
5. **Sales rework**: rebuild onto the new architecture — Quotation→Order→Delivery→Invoice,
   Sales Credit Notes, Customer Receipts (with WHT Receivable), Batch Customer Payments.
   (The existing Customer Receipt voucher from the old codebase is a reasonable starting
   pattern for the single-window UX, but rebuild it clean on the new schema/templates.)
6. **Inventory**: Products (with UOM), warehouses, stock — needed to make Delivery/Goods
   Receipt availability checks real.
7. **Fixed Assets**: categories, asset setup, annual depreciation JV, disposal.
8. **Banking**: Bank Reconciliation (CSV/Excel import, manual matching, outstanding items,
   locking).
9. **Reporting**: Trial Balance, GL, Partner Ledger, Balance Sheet, P&L, Cash Flow Statement,
   Aging — then the flexible report-builder last, once Sales/Inventory data exists to filter on.
10. **Design system pass**: port the tokens/hero/stat-cards/status-pills/report-table styling
    across every screen built above (can also be threaded through earlier steps as each
    segment is built, rather than saved entirely for the end — Abdul's call).

Throughout: every voucher gets the single-window Draft→Posted pattern, every table gets
`organization_id`, and nothing gets built that duplicates Odoo's internal-only mechanisms
(WHT Clearing accounts, Policy codes, Analytic Plans/Accounts/Distribution Models — all
explicitly out of scope per Abdul).
