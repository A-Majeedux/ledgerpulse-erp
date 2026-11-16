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
| Vendors: bills, credit notes, payments, batch | 🟡 | Most complete module. Bills can now carry their LC charges and show a landed cost per product (section 18) |
| Customers + receipts | 🟡 | Customer card rebuilt 14 Nov 2026 (ID, Edit, search, Columns menu — section 13). Receipts can be applied to invoices since 15 Nov 2026 (section 18) |
| Sales invoices | 🟡 | Invoices only. No quotation → order → delivery chain. Each invoice now shows what is still due |
| Purchases (RFQ → PO → Receipt) | ❌ | |
| Inventory | ❌ | |
| Fixed assets | ❌ | |
| Bank reconciliation | ❌ | |
| Reports | ✅ | Trial Balance, General Ledger, both Partner Ledgers, Customer and Vendor Aging, Own Cheques |

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
- 2026-11-14 — Look and feel, seventh round: a lighter palette, real icons everywhere, an infinity mark that is
  actually an infinity mark, charts on the dashboard and the two reports, a Ctrl+K jump-to box, and page
  transitions instead of blinks (see section 16)
- 2026-11-15 — The customer receipt voucher, and the four corrections after it: LC removed from the sales
  invoice, tick-box columns on the invoice lines, the menu split into Reports and Business Settings, and the
  Customer Partner Ledger (see section 17)
- 2026-11-15 — Round nine, the first three of the list: **LC + landed cost on purchases**, **receipts matched to
  invoices**, and the **Customer Aging Report** (0-30 / 31-60 / 61-90 / older) under Reports (see section 18)

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

## 16. The new look, and getting around without scrolling (built 14 Nov 2026)

This round changed **how the program looks and how you move through it**. It changed no figure, no rule and no
screen's job. Every page that existed before still exists at the same address, with the same boxes and the same
buttons; the buttons now carry a small drawing beside their words.

### A. The colours and the paper

- Four files now sit under the look: `static/tokens.css` (the colours and sizes), `static/ui.css` (the new pieces),
  `static/app.js` (the small behaviours) and `static/charts.js` (the graphs). `templates/_icons.html` holds the
  drawings as one hidden block of shapes, so no page has to fetch an icon from the internet — the preview window has
  no internet, and neither may a customer's server.
- Lighter paper (`#F6F9FB`), softer ink (`#17313F`), one accent (`#0EA5A0`) and one rounded corner size (14px).
  Shadows are two very light layers instead of one grey blur. The old rules are still underneath, so any screen I did
  not touch keeps working exactly as before.
- Nothing animates unless the machine says it can: every movement in `ui.css` is switched off under
  `prefers-reduced-motion`. Nothing loops on its own except the faint background mark, which drifts once every 26
  seconds and draws itself once on arrival.

### B. The background mark

The old shape was wrong — it read as a pair of glasses. It is now a true infinity sign
(`M18.178 8c5.096 0 5.096 8 0 8-5.095 0-7.133-8-12.739-8-4.585 0-4.585 8 0 8 5.606 0 7.644-8 12.74-8z`) used in
three places: the faint mark behind every page, the small mark in the top bar, and the sign-in screen. The sign-in
screen's pretend bar chart is now a real drawn line.

### C. Icons

- `templates/_icons.html` has 53 drawings drawn as thin outlines that take the colour of the text around them
  (`currentColor`), so the same invoice drawing is grey in a menu and white on a dark button.
- The top bar, every menu, the palette, the buttons that create something (`New Invoice`, `New Customer`,
  `New Vendor Bill`, `New Receipt`, `New Journal Entry`, `New Product`, `New Account`, `New Vendor`), `Search`,
  `Clear`, `Edit` and `Show` all have their drawing.

### D. Getting around without scrolling ("flying through")

- **Jump to…** in the top bar, or **Ctrl+K** (⌘K on a Mac): a box opens, you type three letters — `inv`, `cus`,
  `jrn` — and press Enter. It lists every screen from the menu, plus the eight screens you start new work on
  (`New sales invoice`, `New customer receipt`, `New vendor bill`, `New vendor payment`, `New journal entry`,
  `New customer`, `New vendor`, `Trial balance`). Your last five visits are remembered at the top.
- Two-key shortcuts: press `g` then `d` (dashboard), `i` (invoices), `p` (products), `c` (customers), `v` (vendors),
  `r` (trial balance), `s` (customer receipts). `/` jumps to the search box on the page you are on. `Esc` closes
  whatever is open.
- A thin line runs across the top while a page is loading, the old page lifts and fades, and table headings stay
  stuck to the top as you scroll, so you never lose the column names.
- A round arrow appears at the bottom-right once you have scrolled, to take you back.

### E. Charts

- `static/charts.js` draws four kinds of graph as plain SVG: `area`, `bars`, `donut` and `hbar` (a ranking of bars).
  A page supplies the numbers in a small `application/json` block; the drawing happens when the card scrolls into
  view. Hovering any point shows the exact figure and the period. No library, no internet.
- **Dashboard** — six cards: Sales revenue and Cash & bank as areas, Profit & loss and Purchases as bars, Biggest
  customers and Biggest vendors as rankings. Each keeps the filter it had (`Monthly / Weekly / Daily`, and the
  customer or vendor chooser). The six figures at the top (Sales, Net profit, Cash & bank, Receivables, Payables,
  Journals) are now cards with a coloured icon, the period chooser at the bottom, and a plain warning when a figure
  is negative or something is overdue.
- **Trial balance** — three figures (total debits, total credits, the difference with a tick or a warning), a ring
  showing how the balance splits between Asset / Liability / Equity / Revenue / Expense, and a ranking of the nine
  largest balances. Both read the same lines as the table underneath, so they cannot disagree with it.
- **Vendor payables aging** — the total owed, the largest single balance, the average, a ranking of the eight
  biggest vendors, and a share bar next to each row.
- `reports/routes.py` now hands the trial balance page two small lists (`type_totals`, `top_accounts`) for those two
  graphs. Nothing else in that file changed.

### F. The dashboard's own tick boxes

The three "Show on this page" boxes (Summary / Charts / Recent activity) work as before and are remembered per user
through `dashboard.save_preferences`. The box now sends its own CSRF token, so the "Remember my choice" button is no
longer refused.

### Checks

- Whole suite: **123 passed**, with only the vendor print case failing that already failed
  (`test_10_all_print_templates_and_routes`, "INVOICE LINES" vs "Invoice Lines").
- Browser-free checks with jsdom (not committed): every page's charts either draw or say "Nothing has been entered
  for this chart yet"; the palette opens on Ctrl+K, filters as you type and closes on Escape; the progress line,
  the back-to-top arrow and the `g`-then-`d` shortcut all fire; every `#i-…` drawing a page asks for exists in the
  hidden block (`53` shapes, `34` in use); every address the palette offers answers 200.
- Every GET screen was requested with a real login on a database with sample invoices, journals and vendors: all
  answered 200 (the `/auth/admin/…` screens answer 403 for a normal company user, as they should, and 200 for the
  platform administrator).

### Known gaps in this round

- The look was checked by reading the markup and running the scripts without a browser; nobody has yet looked at it
  on a real screen at a real size.
- The printed pages (invoice, vendor bill, receipts) were deliberately left alone.

## 17. The receipt voucher, and four corrections (built 15 Nov 2026)

### A. The printable voucher for a customer receipt

There was no printed voucher for a customer receipt - the receipt page had a Print button nowhere, and printing
the page itself gave the menu and the buttons on paper. There is now a proper voucher:

- **Where it is:** the receipt's own page has a **Print voucher** button at the top right, and every row of
  Customer Receipts has a small printer button. Both open `GET /customers/receipts/<id>/print` in a new tab.
- **What is on it:** the company's printed head (name, address, NTN/STRN, logo) from the same A4 frame every other
  printed document uses, the voucher number (CPV/2026/00007), the customer with his address, city, NTN and phone,
  the date, payment method, payment type, the account the money went into, the journal, and the memo.
- **The title follows the money:** CASH RECEIPT VOUCHER, CHEQUE RECEIPT VOUCHER, or RECEIPT VOUCHER for a transfer.
- **Cheque details** (number, bank, date, amount) appear only when the method is Cheque. **Withholding tax** (rate,
  the amount withheld, and the net received) appears only when tax was withheld - and it says plainly that the
  withheld amount is a recoverable tax, not a discount.
- **The amount is written in words** as well as figures ("Rupees Five Hundred Ninety-Two Thousand Two Hundred
  Seventy-Seven and Paisa Sixty-Eight Only"), from `customers.services.amount_in_words`. Thousands / millions /
  billions, in English.
- A **Signature of the customer** line, the four signature boxes (Prepared / Checked / Approved / Authorized), and
  a short note that cheques are subject to realisation.
- The voucher is read-only: it looks at the receipt and prints it. Nothing is posted, nothing is numbered here.

The receipt's own screen was tidied at the same time: the two tabs (Receipt / Cheques) are now the same tabs the
receipts list uses, the details sit in a card with the amount beside them as a large figure, and the cheque tab
says plainly when the money did not come by cheque.

### B. LC Reference is gone from the sales invoice

A letter of credit is an **import** reference - it belongs to buying raw material and to landed cost, not to a
sale. So the box is gone from:

- the **New Sales Invoice** screen (the row of boxes under the customer),
- the **invoice's own page** (the Delivery Address row now has the full width instead of being paired with it),
- the **printed invoice** (the Delivery Date row now pairs with Payment Terms, and the Status row with Journal).

The invoice no longer reads or stores it. **The database column stays** (`sales_invoices.lc_reference`) so every
invoice entered before today keeps whatever it had; nothing is dropped or renamed. `sales.services.clean_lc_reference`
is kept, ready for the purchase screens where it belongs.

### C. The sale invoice's columns

The line table had ten columns squeezed into fixed percentages (20%, 14%, 12%, 8%, 8%, 10%, 8%, 12%, 10%), which is
why the widths looked wrong. Now:

- A **Columns** strip sits above the table with a tick box for each optional column: **Description, Account, Unit,
  Discount %, Tax**. Product, Qty, Rate, Total and the remove button are always there.
- The widths are **not fixed any more**: the browser gives each visible column the space it needs, and an unticked
  column hands its width to the others. Minimum widths keep a box from collapsing to a sliver.
- The choice is **remembered in that browser** (`zyro.invoiceColumns`), so the next invoice opens the way the last
  one was set up. The Discount and Tax lines in the totals box follow their columns.
- Hiding a column never changes the money: the figures already typed stay in the invoice.

### D. Where the screens sit in the menu

- **Reports** is now its own menu: Trial Balance, General Ledger, **Customer Partner Ledger**, Vendor Partner Ledger,
  Vendor Aging Report, Own Cheques Report.
- **Business Settings** is now its own menu: Sales Tax Rates and WHT Rates. (There is no settings module behind it
  yet - this is the shelf they belong on, as the owner asked.)
- **Accounting** keeps Chart of Accounts, Journal Entries and New Journal Entry. **Purchases** keeps Vendors, Vendor
  Bills, Vendor Credit Notes, Vendor Payments and Batch Vendor Payments. **Sales** keeps Sales Invoices, Products,
  Customers and Customer Receipts.
- Nothing was removed: every screen that had a place still has one, one menu further along. The Ctrl+K jump-to box
  lists all of them again, and each menu lights up on its own screens.

### E. Customer Partner Ledger (new)

`GET /customers/reports/partner-ledger` - one customer at a time, laid out like the Vendor Partner Ledger:

- Every **posted** invoice (adds to what he owes) and every **receipt** (takes it away), oldest first, with the
  balance after each line.
- A **From / To** pair of dates. The balance before the first date is shown as "Balance brought forward", so a
  part-period never looks like the whole story.
- Three figures at the top: invoiced in the period, received in the period, and what the customer still owes.
- A draft invoice is **not** a debt yet, so it is not counted; the customer's own card still lists it. The screen
  says this in one line underneath.
- Read-only, like every report: it reads documents and writes nothing. Another company's customer gives 404.

The figures come from the documents themselves (`customers.services.partner_ledger`), not from the 1100 ledger
lines - the same basis as the vendor ledger, so the two reports can be read side by side.

### Checks

- `tests/test_receipt_voucher_and_columns.py` (19 checks): the words on a voucher; the voucher page and what it
  carries; a cheque voucher showing the cheque and a cash voucher not showing one; withholding tax appearing only
  when it happened; another company getting 404; the receipt page and the list both leading to the voucher; the
  partner ledger's running balance, drafts excluded, dates narrowing the period, the stranger's customer refused and
  the screen opening without a customer; the invoice screen having no LC box and having the five column boxes; the
  line header having no fixed percentages; an invoice saving with no LC field; and the menu putting the two tax
  screens under Business Settings and both ledgers under Reports.
- Whole suite: **142 passed**, with only the vendor print case failing that already failed before this work
  (`test_10_all_print_templates_and_routes`, "INVOICE LINES" vs "Invoice Lines").
- Every GET screen was requested with a real login against the demo database: 53 routes, none returning an error.
- The invoice entry screen's column boxes were exercised without a browser: the default columns, ticking Discount,
  unticking Account, the choice being remembered, and the totals still adding up (3 x 250 = 750.00).

### Known gaps

- The owner sent a screenshot of the bad formatting; it did not reach the workspace, so the voucher was laid out in
  the same house style as the printed invoice and the vendor payment voucher. If it still does not match, the
  screenshot is needed.
- The partner ledger reads documents, not the 1100 ledger lines: a hand-written journal entry against a customer
  does not appear on it (it does appear on the customer's own Accounting tab).


## 18. Landed cost, receipts against invoices, and customer aging (built 15 Nov 2026)

Three things from the owner's own list, built together because they lean on each other: a landed cost needs the
purchase bill, an aging report needs to know what is really still owed, and what is really still owed needs the
receipts to be matched to the invoices.

### A. LC + landed cost on a vendor bill

A letter of credit belongs to buying (see section 17B), so this is on the **purchase** side only.

- The **New Vendor Bill** screen has an **LC Reference** box and a **Spread LC charges** choice:
  **By value of the goods** or **By quantity**.
- Each line now has a **Product** box (goods lines only) and a **Landed cost** tick. A ticked line is one of the
  LC's own charges - freight, customs duty, clearing, bank charges - and names no product; a goods line names the
  product it brought in.
- The running total at the bottom splits as it is typed: **Goods** and **LC charges** under the **Bill Total**, so
  the charges are visible before the bill is saved.
- Nothing new is posted. The charges are ordinary bill lines - the same debit they always were - and they are
  simply **spread over the goods lines** to show what each unit really cost. `vendors/landed_cost.py` does the
  arithmetic and nothing else:

```
split_landed_cost(goods_lines, cost_total, basis)   -> (rows, summary)
landed_unit_cost_by_product(goods_lines, cost_total, basis) -> {product_id: cost per unit}
```

  The shares **always add up to the charges exactly**: every line but the last is rounded to the paisa and the last
  takes the remainder, so 33,333.33 over three lines loses nothing. A line with no quantity is given no cost per
  unit rather than passing the whole line off as one.
- The **bill's own page** gains a **What these goods really cost** panel (only when the bill has charges): three
  figures - Goods, LC charges, Landed value - and a table of each goods line with its share of the charges, its
  landed value and its **cost per unit**. The lines table itself gains **Product** and **Kind** columns.
- When the bill is **posted**, each product's **last landed cost** is written to its card
  (`products.last_landed_cost`), and the products screen shows it beside Price and On hand together with the margin
  at the sale price. **On hand is not changed** by any of this - it was already an untracked figure.
- A bill with **no** LC lines behaves exactly as before: no panel, no product requirement, nothing posted
  differently. That is checked by a test.

### B. A receipt knocks off the invoices it settles

Vendor payments could already be allocated to vendor bills; customer receipts could not. Now they can, the same way.

- The **New Customer Receipt** screen, under the amount, lists **the customer's posted invoices with something
  still owed** (oldest first) with what is left on each: a **Full** button fills the whole balance, and any figure
  can be typed. The money you have typed is shown above as "Applied x of y" and turns red if you apply more than
  the receipt is for.
- Applied money is stored in `customer_receipt_allocations` (one row per invoice a receipt settles), which mirrors
  `vendor_payment_allocations`. The invoice's `paid_amount` and `amount_due` are then rewritten from those rows
  (`customers.services.refresh_invoice_balance`), so the allocation rows are the truth and the two columns on the
  invoice are a cache of them.
- **Nothing is posted by an allocation.** The receipt's own journal entry already credited Trade Receivables with
  the whole amount; matching it to invoices only says *which* invoices it paid.
- What is refused, with a message that names the invoice: an amount bigger than the invoice still owes; amounts
  that add up to more than the receipt; another customer's invoice; another company's invoice; a **draft** invoice
  (it is not in the ledger yet, so money cannot honestly be applied to it). A refused receipt saves nothing at all.
- A receipt that matches nothing still saves - it becomes an unapplied credit on the customer's account, and the
  receipt page says so.
- The **receipt's page** now lists the invoices it settles, with anything left unapplied named separately, and the
  **invoice list** has a **Still due** column while the **invoice's page** shows "Receipts applied" and "Still due"
  under the total.

### C. Customer aging under Reports

`GET /customers/reports/ageing`, in the menu as **Customer Aging Report**, beside the Vendor Aging Report.

- Four buckets, exactly as asked: **0-30**, **31-60**, **61-90** and **Over 90 days**, per customer, with the total,
  each customer's share, and the age counted from the **due date** (or the invoice date when it has none) to the
  **as at** date - which can be changed on the page.
- Only **posted** invoices with something still owed are counted, and "still owed" is the invoice total less what
  the receipts applied to it - the same basis as the vendor aging, and the same money the partner ledger shows as
  owed by that customer, so the two tie.
- Below the table, **every open invoice** is listed with its age in days, so a customer's total can be walked back
  to the documents.

### D. One bug found on the way: the Trade Payables account

Saving a vendor bill (or a credit note or a payment) looked the payables account up by the code **2000**, which is
what this program's default chart uses. The owner's own chart numbers it **2100 Trade Payables**, so on that
company every one of those screens would have failed on a NULL account. `_trade_payables_account_id` now takes
2000, then 2100, then any liability named payables / creditors, and - if the company has none at all - says so on
the form ("This company has no Trade Payables account…") instead of crashing. The bill form asks for the account
before it writes anything, so nothing is left half-saved.

### Checks

- `tests/test_landed_cost_and_ageing.py` (34 checks):
  - the split itself: shares adding up exactly for awkward figures in both bases; by-value and by-quantity giving
    the expected shares; a cost per unit per line; no charges meaning no extra cost; a zero-quantity line; the two
    bases and the fallback for an old bill with no answer; the same product on two lines giving one cost; the
    remainder landing on the last line;
  - the bill: the product and the kind stored per line, the basis kept, the panel and its figures
    (7,692.31 of charges on Urea, 3,269.23 a bag), posting writing 3,269.23 and 10,461.54 to the two products, the
    products screen showing them, a bill with no LC lines unchanged, and the form offering the box, the product
    picker and the tick;
  - the receipt: settling one invoice and the balance being real, one receipt over several invoices, one invoice
    by two receipts, over-allocation refused three ways, a draft invoice refused, another customer's invoice
    refused, a receipt that matches nothing still saving, and the picker listing only posted invoices with
    something owed;
  - the aging: the four buckets with four invoices, an invoice with no due date, a settled invoice dropping out, a
    draft invoice not aged, and the report sitting under Reports;
  - the payables account: a chart numbered 2100 still saving a bill, and a company with no payables account being
    told so on the form rather than crashing.
- Whole suite: **176 passed**, with only the vendor print case still failing that already failed before this work
  (`test_10_all_print_templates_and_routes`, "INVOICE LINES" vs "Invoice Lines").
- Every GET screen was requested with a real login against the demo database after the change: 72 screens, none
  returning an error (the ten admin screens answer 403 for a non-admin, as they should).
- The whole story was then walked through on the demo data with the app's own screens: an imported consignment -
  200 bags of Urea and 120 of DAP with ocean freight 95,000, customs duty 148,000 and clearing 32,000 - was saved
  as a bill (LC 118/2026) and posted, giving a landed cost of **2,817.62** a bag of Urea and **9,112.30** a bag of
  DAP (goods 1,382,000 + charges 275,000 = the bill's 1,657,000), and a receipt of 120,000 was applied to two of
  Al-Noor Agri Traders' June invoices (56,550 and 58,860), which then read fully paid while 4,590 stayed on the
  account as a credit.

### Known gaps

- **Stock is still not tracked.** A landed cost per unit is a figure on the product card, not a valuation of stock;
  On hand and any inventory value are untouched.
- **A receipt cannot be un-matched.** To correct a wrong allocation the receipt would have to be deleted; there is
  no "remove this allocation" button yet.
- **Aging reads documents, not the ledger.** A hand-written journal entry against a customer does not appear on it
  (the same gap the partner ledger has, section 17).
- The **vendor payment** screen still has no allocation grid of its own, although the route has accepted
  `bill_id[]` / `alloc_amount[]` since it was written.

## 19. The Customers module, gone through end to end (built 15 Nov 2026)

The first module of a module-by-module pass. Everything here is about one thing: **a customer's balance must be
one figure, everywhere, and it must be the figure the documents support.** Six pieces, in the order they were
built.

### A. One definition of "due"

Before this, four screens counted what a customer owed in four ways, because receipts could only be *received*,
never *matched* (section 18B added the matching, this makes everyone use it):

| screen | before | now |
|---|---|---|
| customer card, "Due" | invoiced − received | **invoiced − matched** |
| aging report | invoiced − paid | invoiced − paid (unchanged; it was already right) |
| partner ledger closing | invoiced − received | invoiced − received (unchanged: it is the ledger) |
| dashboard "Receivables" | `max(0, invoiced − received)` | **invoiced − matched** |

`customers.services.customer_totals` now returns four figures that are not the same thing and are no longer
confused with each other:

```
invoiced    every posted invoice, tax included
received    every receipt, matched or not
applied     the part of those receipts matched to invoices   (customer_receipt_allocations)
unapplied   received - applied      (money in hand, waiting for an invoice)
due         invoiced - applied      (what the aging report and the card's "Still due" show)
net         invoiced - received     (what the partner ledger closes at, and what the credit limit counts)
```

On the demo company all four now tie exactly: Al-Noor 136,425.60 invoiced, 712,277.68 received, 115,410 matched,
**21,015.60 due**, and a **−575,852.08 net** (he has paid more than he was billed). Chaudhry Fertilizers: 1,645,673.60
invoiced, 891,257.12 received, nothing matched, **1,645,673.60 due**. Balochistan Farm Supply: 2,305,658.40 both
ways. The dashboard's receivables and the aging report's total are the same **5,778,065.80**.

The card's **Sales** tab gained a **Still due** column beside each invoice, and **Posted only** is stated on it -
a draft is not money owed.

### B. A customer's own page, and a statement to print

- **`GET /customers/<id>`** - the customer **read only**: name and ID, tax details, terms, the four figures above,
  the credit position, how old the balance is in four buckets, the open invoices oldest first, the last invoices
  and receipts, and the contacts and addresses. No boxes to fill in and nothing to save, so a colleague who may
  not edit a customer can still answer "what does he owe and since when". The customer **list** now opens this page
  when a name is clicked; the card is a click from there.
- **`GET /customers/<id>/statement`** - a **Statement of Account** on the A4 print frame used by the bills and
  invoices: the company's letterhead, the customer, date range, opening balance brought forward, every invoice as
  a debit and every receipt as a credit with a running balance, the totals, the aging summary, and every invoice
  still owing with its due date. It closes with the sentence that matters on a statement - *this is a statement of
  account, not a tax invoice* - and signature lines. A start and an end date may be given (`?start_date=&end_date=`);
  with none, it covers everything up to the later of today and the last document in the ledger.
  The arithmetic is checked: **opening + debits − credits = closing**, the closing is the same whether the
  statement covers everything or any period ending at the same date, and the aging summary equals the "Still due"
  on the card.

### C. Credit limit: warn, block, and tell

The owner asked for all three behaviours. They are not alternatives on a setting - they are three moments of the
same rule, so a company gets all of them:

1. **Warn while typing.** Choosing a customer on **New Sales Invoice** fills the **Due Date** from his payment terms
   (and says so in the strip under the summary how much room is left on his limit, turning coral when the invoice
   being typed is bigger than the room). `GET /customers/<id>/terms` is the JSON the screen reads. The same endpoint
   ticks the **withholding tax** box on a new receipt and picks the customer's rate, because he is the one who
   deducts it.
2. **Block at posting.** An invoice that would take the customer over his limit is **not posted**; it is sent back
   to its own page with a red panel saying by how much, and a **Save anyway (over the limit)** tick beside the Post
   button. A **draft is always allowed** - it changes no balance - and a limit of 0.00 means no limit, so nothing
   is ever blocked for a company that does not use limits.
3. **Tell, passively.** The invoice page and the customer page state the position (limit, held, room) without
   blocking anything, and the card's **Credit Limit** tab explains the rule in one paragraph.

`credit_position` is the one place the arithmetic lives: **held = net receivable** (invoiced less every rupee
received), `room = limit − held`, `over` only when a limit was actually set, `over_by` for the warning, and a
signed `room_text` (a customer with no limit shows a dash rather than an invented negative room).
The **Due Date** is also filled server-side when the box is left empty, so an invoice saved with JavaScript off
still has the date its terms call for.

### D. Duplicate guard

A second customer with the same **NTN** or **CNIC** is refused, and so is one with the same **phone number**
(compared as digits, so `0300-1234567` and `(0300) 123 45 67` are the same number). The message names the customer
that already has it:

> NTN 1234567-8 is already on customer "Gulf Agro" (ID 2). One business has one NTN - open that customer instead
> of adding a second, or correct the NTN here.

Blank numbers are never compared - most walk-in customers have none. A customer keeping his own numbers is fine
(the check excludes the row being saved), and a number may not be moved onto another customer's card.

### E. Tax and terms on the card

Six boxes, in a **Tax** section and a **Terms and withholding** section of the card:

- **Province** (Punjab, Sindh, Khyber Pakhtunkhwa, Balochistan, Islamabad Capital Territory, Gilgit-Baltistan,
  Azad Kashmir - a datalist, so anything else may still be typed)
- **Sales Tax Registration**: Registered / Unregistered / Retail
- **On the active taxpayers list (filer)**
- **Payment terms (days)** - what the due date comes from
- **This customer deducts WHT when he pays**, and **Default WHT rate (%)**

A blank rate or a blank number of days means "not set", not zero; a rate outside 0-100, more than 365 days or
"thirty" typed into a days box are refused with a sentence that says what to correct. The read-only page shows all
six as English ("Registered · Filer · Punjab", "Deducted by the customer at 4.50%").

### F. `journal_lines.customer_id` - not built

The last slice of the list was to give each journal line a customer ID so that a rename or a merge stops
splitting a balance. It is the one that touches posting and every report, so it is deliberately left until the
module above is proved. See the gaps below.

### Checks

- `tests/test_customers_module.py` (22 checks): the duplicate guard on NTN, CNIC and phone, blank numbers never
  colliding, a customer keeping his own number, and the numbers staying per company; the terms saved and read
  back, four kinds of nonsense refused, the terms offered as JSON, and an invoice with no due date taking
  2026-10-01 from 30 days; the four figures on a receipt that is partly matched (1,500 invoiced, 900 received,
  600 matched: **900 due**, 300 unapplied, **600 net**); the card, the aging report, the ledger closing and the
  open-invoice list agreeing on one figure; a statement adding up over a period and closing where the whole one
  closes; the room left (800 invoiced, 500 received, 1000 limit: 300 held, **700 room**); a draft over the limit
  still saved, the same invoice **refused at posting**, the refusal page explaining itself and offering the tick,
  the same invoice posted with the tick, and a customer with no limit never stopped.
- The three earlier customer-card tests that describe the card's shape were updated where this round changed it
  deliberately - the two new list columns, the six new fields, the blank card starting at "Registered, Punjab",
  and a customer with no limit no longer being told he is "over the limit" he does not have.
- Whole suite: **198 passed**, with only the vendor print case still failing that already failed before this work
  (`test_10_all_print_templates_and_routes`, "INVOICE LINES" vs "Invoice Lines").
- Every GET screen requested with a real login against the demo database: **75 screens**, none returning an error
  (the ten admin screens answer 403 for a non-admin).
- The demo company was given real tax and terms data - Chaudhry Fertilizers (Punjab, Registered, filer, WHT 4.5%,
  30 days, limit 1,500,000), Sindh Agro Services (Sindh, WHT 4.5%), Balochistan Farm Supply (Retail, 7 days, no
  limit) - and its statement, its page, its card and its JSON were all rendered and read.

### Known gaps

- **`journal_lines.customer_id` is not there yet.** A customer's name is still copied onto every journal line, so
  a rename is blocked once he has entries (section 13) and two customers cannot be merged. Next round.
- **A receipt still cannot be un-matched.** A wrong allocation can only be corrected in Accounting, as noted in
  section 18.
- **The statement ages what is open today.** A statement printed for a past period shows the invoices that are
  still unpaid *now*, not the ones that were unpaid on that date - the allocations carry no history.
- **The customer's `currency` is still display only.** The books stay in the company's currency, so a statement is
  in PKR whatever the card says.
