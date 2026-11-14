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
- 2026-11-14 — The rest of the card: Tags, Also a vendor, Currency, Website, the Contacts & Addresses tab with its
  contact cards, the two tabs, and the Sales / Invoiced / Due figures at the top (see section 13)
- 2026-11-14 — The five tabs under his card, from his own screens: Contacts & Addresses, Sales & Purchase (his
  invoices and receipts, read-only), Accounting (the 1100 lines under his name), Notes (a new column) and Credit
  Limit (see section 13)
- 2026-11-14 — Sales invoice numbers: INV/2026/00001, given at save, never repeated, per company per year; the two
  boxes from his screen (Delivery Date, LC Reference); the invoice page laid out like his; the customer card and the
  journal entry show the number (see section 14)
- 2026-11-14 — Fixed the "column p.organization_id does not exist" crash on his own database: an old products
  table with no company column; products and tax rates now get every missing column and a company, never the ZYRO
  head office while a real company exists (see section 14)
- 2026-11-14 — The four screens he asked for in one go: a **printable sales invoice**, the **products screen as
  cards** with Price and On hand and a search box, the **receipt screen** with Payment Type / Journal / Voucher
  Number / Payment Method and the **Cheques** tab, and the **customer card finished** - a Type on every contact and
  a **Purchases** figure from a linked vendor (see section 15)

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

### The rest of the card (also 14 Nov 2026)

The second half of his customer form. Same rule: only the customers tables are written, and no journal entry is
created or changed.

**Four more boxes on the card:**

- **Tags** — the words he files a customer under, typed on one line separated by commas ("Punjab, VIP"). Tidied
  (spaces off, a tag typed twice kept once), shown as a pill each under the box, at most 20 tags of 40 characters.
- **Also a vendor** — a tick. *A note on the card only:* it does not create a vendor record, and nothing is posted.
  The link between a customer and a vendor comes with the Purchases step.
- **Currency** — the three-letter code he deals with the customer in (PKR, USD). Empty means the company's own
  currency. *For reference only:* ZYRO keeps the books in the company currency; foreign-currency postings do not
  exist, and this box changes no amount anywhere.
- **Website** — checked only for its shape (no spaces, has a dot); nothing is opened or fetched.

**Two tabs on the Edit card:**

- **Customer** — everything above.
- **Contacts & Addresses** — the people and addresses kept under the customer (name, job position, phone, email,
  and a full address). Add, change, remove, and "Make primary". **One rule keeps them tidy:** while a customer has
  any contacts, exactly one of them is the primary one — the first one added becomes primary by itself, "Make
  primary" moves it, and removing the primary hands it to the oldest that is left. Contacts never post anything.

**Three figures at the top of the Edit card** — read from the documents, by the customer's ID:

| Figure | What it counts |
|---|---|
| **Sales** | every sales invoice entered for the customer, drafts included |
| **Invoiced** | only the invoices that are in the books — the posted ones |
| **Due** | Invoiced less the receipts already received from him (a minus means he has paid more than was invoiced) |

These three are the agent's reading of his screens (his pictures are not in the repository), so the owner may want
them worded differently — for example Sales to count only posted invoices. They only read; nothing is posted.
*Known limit:* a manual journal entry that carries his name on account 1100 is not in these three figures, and
neither is anything from another currency.

**Database change — additive only.** 19 columns in all added with `ADD COLUMN IF NOT EXISTS` (the 15 of the card
plus tags, is_vendor, currency, website), the ID given to the customers that had none, one new unique index, and one
new table `customer_contacts` (created with `CREATE TABLE IF NOT EXISTS`, with its own additive columns and index).
Nothing is dropped, renamed or rewritten.

### The five tabs under the card (14 Nov 2026, from his own screens)

The owner sent pictures of his live customer and sales screens, so the Edit card now carries the same tabs, in
the same order, with the card itself always in front of him: **Contacts & Addresses, Sales & Purchase, Accounting,
Notes, Credit Limit.**

- **Contacts & Addresses** — as above, and each contact now shows the same little square with its first letter
  that his own screen shows. A tag pill carries a small **x** that takes the tag out of the box (`Save Changes`
  keeps it out).
- **Sales & Purchase** — two read-only tables: every sales invoice entered for him (date, due date, total, and
  whether it is in the books) and every receipt received (date, amount, memo). *His screen shows an invoice number
  like INV/2026/01749; ZYRO does not print a number on an invoice yet, so the invoice's own number is shown and the
  card says so.* Nothing is changed from this tab — invoices and receipts are entered on their own screens.
- **Accounting** — every line on **Trade Receivables (account 1100)** that carries his name, with the balance
  (debit less credit). This is the one place a hand-written journal entry shows up: it is **in this balance but not
  in the Sales / Invoiced / Due figures** at the top of the card, which read the invoices and the receipts only.
- **Notes** — the one new box: up to 2,000 characters, saved on its own button (`Save Note`). The lines he types
  stay as he typed them. It has its own column, so saving a note does not touch the card.
- **Credit Limit** — the limit he entered on the card, what is due now, and the room left (a minus means over the
  limit). The limit is still typed on the card itself, in Credit Terms. *ZYRO records the limit but does not stop a
  sale that goes over it yet — the invoice is still saved.*

**Database change for the tabs — additive only.** One column, `notes TEXT`, added with `ADD COLUMN IF NOT EXISTS`.
No new table, nothing dropped, renamed or rewritten, and no journal entry is created or changed by any of the tabs.

**Still open from his customer form:** the contact cards do not carry a "Type" (invoice address / delivery /
other). His header strip also carries **Purchases** and **Lots/Serial Numbers**, which need the vendor link and the
stock module and are not on the card yet. LIC Number and LIC Expiry Date are on every company's card for now — the
owner was asked whether they are for Hextar only.

**Checks.** `tests/test_customer_card.py` (57 checks: the rules without a database, and the screens on one), plus
ZYRO's own `tests/` (79 passed; `test_vendors.py::test_10_all_print_templates_and_routes` failed before this work
and still fails — it looks for the words "INVOICE LINES" on the vendor bill print, which the template writes as
"Invoice lines"). Rehearsed on a database built and filled by the previous version: none of its rows changed; the
columns, the index and the contacts table were added; every customer got its ID oldest first; the old blank-named
customer showed as "(no name)" and could be given a name; two customers that already shared a name both stayed; the
name of a customer with a receipt was locked; the three figures came out right from documents the old version had
made; and a contact was added, made primary and shown on an old customer. The five tabs were then opened on that
same upgraded database: his old invoice and his old receipt were listed, the Due figure came out right, and a note
was saved and read back. Not done: a run on the real (Neon) database.

## 14. Sales invoice numbers (built 14 Nov 2026)

His own screen numbers invoices **INV/2026/01749**. ZYRO now numbers a sales invoice the same way:

```
INV / 2026 / 00001
 |     |       |
 |     |       +-- running number, starting again at 1 each year, per company
 |     +---------- the year of the invoice date
 +---------------- the letters every sales invoice starts with
```

**The rules** (`sales/services.py`; the screens in `sales/routes.py` only show and save):

- **A number is given when the invoice is saved, and never changes after that** — not when it is posted, not when
  anything else on it changes. A number on a paper invoice cannot be taken back.
- **A number is never used twice**, even if two invoices are saved in the same second: a unique index on
  (company, number) refuses the second one, and the save simply takes the number after it.
- **Each company numbers its own invoices.** Two companies can both have INV/2026/00001.
- **Drafts are numbered too**, so every invoice has a number to quote from the moment it is saved. If a draft is
  deleted its number is not given out again — a gap in the sequence is right for books; a repeated number is not.
- **The invoices made before numbering existed** get their numbers once, on the first start of this version: oldest
  invoice first, per company per year, so the order on the screen is the order on paper. An invoice that already has
  a number keeps it, so starting again changes nothing.
- **The journal entry carries the number** in its reference and on the Trade Receivables line
  ("Invoice INV/2026/00001 - Gulf Agro Chemicals"), so the books and the customer card's Accounting tab show it too.
  Old journal entries keep the wording they were written with.

**The invoice screens:**

- **The list** has a **Number** column first. Its Grand Total now shows the company's currency — it was printing a
  dollar sign regardless of the company's currency, which was wrong and is fixed here.
- **The invoice page** is titled with its number and shows the details from his own screen: Customer, NTN,
  Delivery Address, LC Reference, Invoice Date, Due Date, Delivery Date, Accounting Date, Journal, Currency.
- **The new-invoice screen** has two new boxes: **Delivery Date** and **LC Reference** (only if there is a letter of
  credit). A date that is not a date, or an LC Reference over 100 characters, stops the invoice before it is saved
  and says what to correct.
- **The customer card's Sales & Purchase tab** now shows the invoice number instead of the invoice's internal number.

**Database change — additive only.** Three columns on `sales_invoices` (`invoice_no VARCHAR(30)`,
`lc_reference VARCHAR(100)`, `delivery_date DATE`) with `ADD COLUMN IF NOT EXISTS`, and one partial unique index,
`sales_invoices_number_per_organization`, on (company, number) where the number is not null. Nothing dropped,
renamed or rewritten. The only change to existing rows is the number itself (empty → its number).

**Not done at the time, and why:** there was **no printout** for a sales invoice yet, so the number was on the
screen but on no paper - printed now, see section 15; and the **dashboard** still lists invoices without their
numbers.

### The crash this found (14 Nov 2026): an old products table

Opening **New Sales Invoice** on the owner's own database gave:

```
psycopg2.errors.UndefinedColumn: column p.organization_id does not exist
```

His `products` table was made by an earlier version of ZYRO and had **no company column at all**.
`CREATE TABLE IF NOT EXISTS` never adds a column to a table that already exists, so the schema's definition
had never reached it, and every sales screen that asks for "the products of this company" crashed.

**The fix, additive only, in `sales/schema.py`:**

- `products` now has a defensive `ADD COLUMN IF NOT EXISTS` for **every** column in its definition
  (`organization_id`, `name`, `unit`, `created_at`, on top of the two it already had).
- `sales_tax_rates` got the same treatment.
- `attach_master_data_to_a_company()` gives the rows that have **no company** one, so they can be seen again:
  first the company of the product's own default revenue account; otherwise the company chosen by
  `_target_company()` — **never** the ZYRO head office while a real company exists, preferring the company
  with the most customers, oldest first — and it prints one line saying how many rows were attached and where.
  A row that already has a company is never touched, and running it again changes nothing.
- The tests build a throwaway database shaped the old way (no company column, a head office plus his real
  company, products on no company) and prove the screens work again, the products land in **his** company and
  not the head office, and a product that already had a company keeps it
  (`tests/test_sales_schema_repair.py`, 5 checks; each test builds and drops its own database).

**Worth knowing:** any other table made by an earlier version can be missing a column in the same way. ZYRO's own
rule - a defensive `ADD COLUMN IF NOT EXISTS` for every column beyond the bare minimum - is what prevents it, and
`products` and `sales_tax_rates` were the two that had slipped through on the Sales side.

**Checks.** `tests/test_sales_invoice_numbers.py` (15 checks: the shape of a number, the two new boxes checked, then
the numbers and the screens on a real database — going up, starting again each year, per company, never repeated,
old invoices numbered once oldest first, the journal entry carrying the number, and a bad date or LC Reference
refused with nothing saved). Whole suite: **94 passed**, with only the vendor print case failing that failed before
this work. Rehearsed on a database built and filled by the previous version (5 invoices, two years, one draft): every
invoice got its number oldest-first and each year started again at 00001, no other row changed except the customers
getting their IDs (which the customer card has always done), and the list, the invoice page and the customer card all
showed the numbers. Not done: a run on the real (Neon) database.

## 15. The four screens of the sixth round (built 14 Nov 2026)

He picked **"All of them in one go"** out of the four things I offered, so all four are here. Every one of them is
additive: new columns are added, never removed or renamed, and every screen that worked before still works.

### A. A sales invoice can be printed

`GET /sales/invoices/<id>/print` (`sales/routes.py: sales_invoice_print`) and the page
`templates/sales/print/sales_invoice_print.html`.

- It uses the **same print frame the vendor bills already print with**
  (`templates/vendors/print/_print_base.html`): the same toolbar with **Print / Save PDF**, the same page setup,
  the same four signature boxes at the bottom (Prepared / Checked / Approved / Authorized). Nothing on the vendor
  side was changed - the frame is only read.
- On the paper: the number (`INV/2026/01749`), the customer with its address and city, the **NTN**, the
  **Delivery Date**, the **LC Reference**, the invoice date, the due date, the payment terms and the status.
- Every line is printed with its product, its own wording, the account it was posted to, the unit, the quantity,
  the rate, the discount per cent, the sales tax and the line amount. **Left joins**, on purpose: a line written
  without a product or without an account still prints, so the paper can never show fewer lines than the invoice.
- The totals box: gross (qty x rate), discount, sales tax, grand total - added up from the lines, the same way the
  invoice page adds them.
- The invoice page itself now has a **Print / Save PDF** button next to its status.
- Another company asking for the invoice gets **404**, never the document.

### B. The products screen, as cards

`/sales/products` (`sales/routes.py: products_list`) and `templates/sales/products_list.html`.

- Cards in a grid (not a table), each showing the **name**, Unit, the revenue account, **Price**, **On hand** and
  an Active / Inactive pill, with **Edit** on the card.
- A **search box on top** that narrows by product name, unit, account code or account name, and says
  "3 of 12 product(s) match ...". A search with no match says so and offers a link back to all products.
- Two new additive columns on `products`: `sales_price NUMERIC(15,2)` and `on_hand NUMERIC(15,3)`, both default 0,
  both added with `ADD COLUMN IF NOT EXISTS` (and both in the `CREATE TABLE` for a brand new database). Nothing else
  reads them, so a product nobody touches behaves exactly as before.
- New screen `/sales/products/<id>/edit` (`edit_product`) to change name, unit, Price, On hand, revenue account and
  Active. A Price or On hand that is not a number, or is negative, is **refused with a message** and nothing is
  saved. The New Product form asks for Price and On hand as well.
- **Honest note:** "On hand" is a figure he keeps on the product, not a stock ledger. Sold and bought quantities are
  not subtracted from it yet; a real stock ledger is a later job.

### C. The receipt screen, with the boxes and the Cheques tab

`/customers/receipts` and `/customers/receipts/new` (`customers/routes.py`) with the templates in
`templates/customers/`.

- New additive columns on `customer_receipts`: `payment_type`, `journal_name`, `voucher_no`, `payment_method`,
  `cheque_no`, `cheque_bank`, `cheque_date`.
- The form asks **Payment Type** (Customer Payment / Advance Payment / Refund / Other), **Journal** (a box that
  offers the journal names already used in the company, default Customer Receipts), **Voucher Number** (shown as
  "Given when you save") and **Payment Method** (Cash / Cheque / Bank Transfer / Online Transfer / Other). Choosing
  **Cheque** opens the cheque boxes: number, bank and date.
- A cheque **without its number or its date is refused** with a message, and nothing is saved.
- The voucher number is his shape: **`CPV/2026/00001`** - letters, the year of the receipt date, and a running number
  that starts again at 1 every year inside each company. It is given when the receipt is saved, never changes, and a
  unique index `(organization_id, voucher_no)` refuses a second receipt with the same one (the save simply takes the
  next number). Receipts made before this version got their numbers **once, oldest first**, on the first start
  (`customers/services.py: number_missing_receipts`, called from `customers/schema.py`).
- The receipts list has a **Receipts | Cheques** tab strip; Cheques shows only the money that came in by cheque, with
  the cheque number and date. The receipt page has the same two tabs and shows the cheque details, or says plainly
  that this receipt was paid by cash / transfer.
- **Compatibility:** a form (or a bookmark) that sends none of the new boxes still saves a receipt exactly as
  before - Payment Type "Customer Payment", Payment Method "Cash", Journal "Customer Receipts" - and it still gets
  its number.

### D. The customer card, finished

- **A Type on every contact**: `customer_contacts.address_type` (additive, default `Other`), chosen from
  **Invoice Address / Delivery Address / Other** on every contact card, shown as a pill next to the name. A type
  that is not one of the three is refused like any other bad box.
- **Purchases in the header strip**: a fourth figure beside Sales / Invoiced / Due, read from the vendor the
  customer is linked to. `customers.vendor_id` is the link (additive, no foreign key - the vendors table is made by
  another part of the program and may not exist when the customers tables are made). The **Sales & Purchase** tab
  now has "The vendor behind this customer": pick a vendor, **Save link**; the figure becomes the total of that
  vendor's bills, with how many bills it came from. **Not linked** shows 0.00 and says to link a vendor.
- Only the Purchases figure reads the link. Nothing about the customer, his invoices, his receipts or the vendor's
  own screens changes, and a vendor of another company cannot be linked (400).

### Checks

- `tests/test_sales_invoice_print.py` (10 checks: the printed page and its contents, the totals adding up, the
  other company getting 404, the button on the invoice page, the cards with Price/On hand, the search box narrowing
  the cards, a search with no match, Price and On hand saved, a bad price refused, a new product keeping its figures).
- `tests/test_customer_receipts_vouchers.py` (14 checks: the shape of a voucher number, the answers offered, a
  contact with no type becoming Other, a bad type refused, the form's boxes, a cheque receipt keeping every box and
  getting CPV/2026/00001, the numbers going up and starting again in 2027, a cheque without number/date refused with
  nothing saved, an old-style receipt still saving, the Cheques tab showing only cheques, another company getting
  404, a contact keeping its Type, the Purchases figure after linking, and a vendor of another company refused).
- Whole suite: **123 passed**, with only the vendor print case failing that failed before this work
  (`test_10_all_print_templates_and_routes`, "INVOICE LINES" vs "Invoice Lines" - it fails on a clean
  zyro-current too).

### Still not done (offered for later)

- The **dashboard** still lists invoices without their numbers.
- **On hand** is a figure he keeps, not a stock ledger.
- The printed invoice has no company logo or bank details block beyond what the vendor print frame already shows.
