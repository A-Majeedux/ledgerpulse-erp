# LedgerPulse — the whole project, stage by stage

This is the build plan for everything in `LEDGERPULSE_BUILD_BRIEF.md`, cut into
stages that can each be finished, checked and looked at on their own.

It follows the build order the brief recommends (its section 14), with one
change asked for by Abdul: **the dashboard comes first**, so the look the rest
of the system will be built in is settled early.

| # | Stage | Brief sections | Status |
|---|---|---|---|
| 0 | Foundation and stabilization | 1, 10, 14 (step 1) | **Done** |
| 1 | Dashboard — "Financial Control Center" | 2 | **Done** (see below) |
| 2 | Accounting core | 11, 13 (account fields), 14 (step 2) | **In progress** — account kinds done; internal transfer next |
| 3 | Vendors and purchases | 4, 5, 14 (step 3) | Partly built |
| 4 | Withholding tax | 7, 8, 14 (step 4) | Partly built |
| 5 | Sales chain | 3, 6, 14 (step 5) | Partly built |
| 6 | Inventory | 3, 4, 8, 14 (step 6) | Not started |
| 7 | Fixed assets | 9, 14 (step 7) | Not started |
| 8 | Banking and reconciliation | 12, 14 (step 8) | Not started |
| 9 | Reports | 13, 14 (step 9) | Not started |
| 10 | Users, roles and going live | `FOUNDATION_TASK_4_REMEDIATION_PLAN.md` | Partly built |
| 11 | Design pass over every remaining screen | 2, 14 (step 10) | Threaded through 2-10 |

## How every stage is run

1. **Plan** — what will be built, which files change, and the questions only
   Abdul can answer. Accounting treatment is never guessed: a new entry
   pattern, account mapping or tax behaviour waits for Abdul's approval.
2. **Build** — small, additive changes. Database changes only ever add
   (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`); nothing is
   dropped or renamed.
3. **Check** — every stage adds its own automated checks to the ones already
   there, and all of them must pass before the stage is called done.
4. **Report** — what changed in plain words, with **verified facts** kept apart
   from **inferences**, and a list of what was *not* tested.

Each new screen is built directly in the new look (stage 1), so stage 11 is a
final sweep, not a redesign. Nothing is committed to Git until Abdul says so.

---

## Stage 0 — Foundation and stabilization · DONE

Organizations (separate books per company, with a switcher), logins, and the
screens that already existed made to work end to end: Chart of Accounts,
Journal Entries, Vendors, Vendor Bills, Vendor Payments, Customers, Customer
Receipts, Products, Sales Invoices, WHT and Sales Tax rate lists.
Record: `FOUNDATION_TASK_5_STABILIZATION_REPORT.md`.

## Stage 1 — Dashboard · DONE

**What you get.** The home page is now a working Financial Control Center
instead of a placeholder:

- a dark hero banner with a period switcher (This month / Last month / This
  quarter / This fiscal year) and one-click "New" buttons for the five vouchers;
- six stat cards, each with an icon chip, the figure, how it moved, and six
  small bars for the last six months;
- a revenue-against-expenses chart for the last six months;
- "Needs attention": drafts waiting to be posted, and posted entries dated
  after today;
- cash and bank accounts one by one, the tax ledgers (WHT payable, WHT
  receivable, output sales tax), and the largest customer and vendor balances;
- the newest journal entries, and Assets = Liabilities + Equity with a
  "Books balance" check;
- for a company with nothing recorded yet, four "Start your books" steps.

**Exactly what each figure is.** Everything comes from *posted* journal
entries of the active company. Drafts are never included.

| Figure | How it is worked out |
|---|---|
| Cash & bank | Balance (debits − credits) of the accounts whose Kind is Bank or Cash on the Chart of Accounts, up to the last day of the period. (Until 13 Nov 2026 this was "Asset accounts coded 1000–1099".) |
| Trade receivables | Balance of account 1100, the account invoices and receipts post to |
| Trade payables | Balance (credits − debits) of account 2000, the account bills and payments post to |
| Revenue | Credits − debits on every Revenue account, dated inside the period |
| Expenses | Debits − credits on every Expense account, dated inside the period |
| Net profit | Revenue − Expenses |
| WHT payable / receivable | Accounts 2100, 2110 / 1150, 1160 |
| Output sales tax payable | Account 2200 |
| Equity | Equity accounts **plus** profit not yet closed into Retained Earnings, so Assets = Liabilities + Equity always holds |

**The date rules.**

- A balance "as at 13 Nov" is everything dated up to and including 13 Nov.
- Comparisons are like for like. On 13 Nov, "This month" is 1–13 Nov compared
  with 1–13 Oct — not with the whole of October.
- "Last month" is the whole month against the whole month before it. Quarters
  and the fiscal year follow the company's fiscal-year start month (July
  unless changed).
- A percentage is shown only when there is a real, positive figure to compare
  with. Otherwise the two amounts are shown. The reference app's
  "Rs0 revenue, +12.8%" cannot happen.
- An entry posted with a date after today is left out until its date arrives,
  and the dashboard says how many such entries there are.

**Files.** New: `dashboard/services.py` (all the arithmetic),
`test_dashboard.py`. Rewritten: `dashboard/routes.py`,
`templates/dashboard/home.html`. Added to: `static/tokens.css` (new rules only,
at the end — no existing rule changed), `test_workflows.py` (section 9),
`README.md`. No database change of any kind.

**How it was checked.** `test_dashboard.py` (period dates for every day of
2023–2030, percentages, rounding) and section 9 of `test_workflows.py` (a known
set of transactions entered through the real screens, then every figure on the
page compared with hand-worked answers and with the journal tables).
Outside the repository: 40 deliberate mistakes were put into a copy of the
code one at a time, and the checks caught 39 (the 40th cannot change what the
page shows); and the page was opened and clicked through in a real browser
(headless Chrome) on a running server, and looked at in five window widths.

**Known limits.**

- "Today" is the server's date, the same one the voucher forms use. On a
  server running on UTC, the first five hours of a Pakistani day still count
  as yesterday. A company time-zone setting would fix this everywhere at once.
- Overdue invoices and bills are not shown, because the system cannot yet tell
  which invoice a receipt paid (payment matching — stages 3 and 5).
- The balance lists group by the partner *name* written on each journal line.

**Decisions from this stage.**

1. *Which accounts are cash and bank?* Decided by Abdul on 13 Nov 2026: the
   accounts marked Bank or Cash on the Chart of Accounts (stage 2, below).
2. *Colours.* Still for Abdul to confirm.
   `static/tokens.css` calls its palette "official locked colors"
   (teal `#278C83`, charcoal `#243448`, ivory `#FAFAF6`); section 2 of the
   brief gives different ones (teal `#17a598`, navy `#142b3a`, canvas
   `#f5f8f7`). The dashboard uses the ones in `tokens.css`. If the brief's
   colours are wanted instead, the main ones change in one place (the named
   tokens at the top of that file); the lighter and darker tints of them that
   are written out by hand (about twenty in the dashboard rules, plus those in
   the older rules) would have to be matched as well.

---

## Stage 2 — Accounting core · IN PROGRESS

**Goal.** Finish the ledger everything else posts through.

### Done on 13 Nov 2026 — account kinds

Every account now has a **Kind**, on top of its type. The kind decides where
the account can be picked.

| Kind | What it is | Where it is offered |
|---|---|---|
| Bank, Cash | Money accounts | The **only** accounts in "Paid from" (vendor payment) and "Deposit to" (customer receipt). Also in the General account column of a journal entry. Counted as Cash & bank on the dashboard. |
| Trade receivable, Trade payable | The two accounts all customers / all vendors share (1100, 2000) | Never picked directly. On a journal entry the customer or vendor is picked **by name** in the Partner account column, and the line is posted to 1100 / 2000 with that name on it — the same thing an invoice, bill, receipt or payment does. |
| Non-trading receivable, Non-trading payable | One account **per person** outside normal trade: a director, an employee | The Partner account column of a journal entry. |
| General | Everything else: revenue, expenses, tax ledgers, equity, other assets and liabilities | The General account column of a journal entry. |

- **Chart of Accounts:** a Kind column, a Kind box on New Account (it follows
  the Type: an Asset can be General / Bank / Cash / Non-trading receivable, a
  Liability General / Non-trading payable, the rest General), and a new
  **Edit** screen (name, kind, active or inactive, reconciliation). Code and
  type cannot be edited. 1100 and 2000 keep their kind and stay active.
- **Journal entry:** two columns, *General account* and *Partner account*;
  each line uses exactly one. The saved entry shows each line in the same
  column.
- **The server enforces all of it**, not just the drop-downs: a payment from a
  non-bank account, a line posted straight to 1100, a customer of another
  company, an inactive account — each comes back as a sentence on the form
  and nothing is saved.
- **Inactive** accounts keep their balance and history and are still counted
  in every figure; they are only left out of the lists of new documents.
- **Database:** one added column, `chart_of_accounts.account_kind`
  (`ADD COLUMN IF NOT EXISTS`, default `General`). On the first start of this
  version, existing accounts are marked **once**: 1100 and 2000 by their code;
  Asset accounts coded 1000–1099 — exactly what the dashboard used to count as
  cash & bank — as Bank, or as Cash when the name has "cash" in it and no
  "bank". Everything else stays General until marked by hand with Edit. A
  later start never re-marks anything.
- **Checked by:** section 10 of `test_workflows.py` (66 checks, including the
  upgrade of an older database), `test_dashboard.py`, 30 defects re-introduced
  on purpose (all 30 noticed by the checks), and 29 checks in a real browser
  against the running app.

### Still to build in this stage, in this order

1. **Bank Internal Transfer** (design only proposed, NOT approved — point 5
   below). Fields: date,
   amount, memo, source and destination, both taken from the same Bank / Cash
   list as payments and receipts. Two linked, posted journal entries.
2. **Voucher numbers and All Payments** (design only proposed, NOT approved —
   point 6 below): one
   list of payments, receipts, transfers and journal entries, each with its
   number. Each payment and receipt gets linked to its journal entry (today
   that link is empty).
3. **Journal entry extras:** LC Reference, and the show/hide column menu
   (Deferred Date, Amount in Currency, Currency, Taxes, Asset, Discount Date,
   Discount Amount, Tax Grids).
4. **Chart of Accounts extras:** parent headings (reports show accounts as a
   tree), Tags (they drive the Cash Flow Statement — the list is fixed with
   that report, stage 9), Account Currency.

- *Done when:* a transfer shows as two posted, linked entries; All Payments
  lists every voucher with its number.

## Stage 3 — Vendors and purchases

**Goal.** Buy, receive, get billed and pay — with every step traceable.

- *Already working:* vendor cards, direct vendor bills (Draft → Posted), vendor
  payments with WHT, duplicate cheque numbers blocked.
- *To build:* RFQ → Purchase Order → Goods Receipt → Vendor Bill, with Received
  and Billed running totals (a Goods Receipt posts no journal entry); the extra
  bill fields (Bill Reference, LC Reference, Payment Reference, Recipient Bank,
  Payment Terms); matching payments to bills; the Own Checks report; Batch
  Vendor Payments (lowest priority).
- *The dashboard gains:* bills overdue, and bills due this week.
- *Decided by Abdul, 14 Nov 2026:* a bill line may use Expense, Stock and
  Fixed asset accounts, picked through filter boxes (not built yet).
- *Decisions needed:* whether a bill may carry a negative (discount) line and
  how it posts; whether a zero-value bill is allowed.

## Stage 4 — Withholding tax

**Goal.** Every WHT situation in the brief, and the monthly deposit to FBR.

- *Already working:* the Tax Rates list (rates are always picked by hand),
  WHT on vendor payments (Supplies / Services), WHT receivable on customer
  receipts.
- *To build:* Salaries u/s 149 and Rent u/s 155 with their own ledgers and
  postings; the monthly FBR remittance voucher (several ledgers and several
  cheques in one entry).
- *The dashboard gains:* WHT due for deposit, by section.
- *Decisions needed:* account codes and names for the new ledgers.

## Stage 5 — Sales chain

**Goal.** Quotation → Sales Order → Delivery → Invoice, linked both ways.

- *Already working:* customer cards, products, sales invoices with discount and
  sales tax, customer receipts with WHT.
- *To build:* quotations, orders and deliveries (a Delivery posts no journal
  entry) with Delivered and Invoiced running totals; the extra invoice fields;
  Sales Credit Notes for returns and discounts with the "Outstanding debits +
  ADD" panel; Batch Customer Payments; Region and Territory on the customer;
  matching receipts to invoices.
- *The dashboard gains:* invoices overdue, and money expected this week.
- *Decisions needed:* a discount credit note posts to Sales, or to a separate
  Discount Allowed account (the brief recommends the second).

## Stage 6 — Inventory

**Goal.** Real stock, so deliveries and receipts can be checked against it.

- *To build:* units of measure and packaging, warehouses, stock movements from
  deliveries and receipts, the availability check, batch numbers; cost of goods
  sold posted with the invoice, and stock posted with the bill.
- *The dashboard gains:* stock value, and items running low.
- *Decisions needed:* how stock is costed (weighted average, or first-in
  first-out). The brief does not say.

## Stage 7 — Fixed assets

**Goal.** Asset categories, assets, yearly depreciation, disposal.

- *To build:* categories with their three accounts and default depreciation
  settings; asset setup; the yearly depreciation entry created as a Draft for
  approval; the assets list; the Asset Ledger; disposal.
- *Decisions needed:* the disposal entry. The brief's two real examples
  disagree; it recommends clearing accumulated depreciation first and then
  recording the gain or loss.

## Stage 8 — Banking and reconciliation

**Goal.** Prove the bank balance in the books against the bank's statement.

- *To build:* statement import from CSV or Excel; matching by hand, line by
  line (no automatic matching, on purpose); cheques not yet cleared and
  deposits in transit; locking of reconciled periods.
- *The dashboard gains:* reconciled-through date for each bank account.
- *Decisions needed:* which banks' statement layouts to support first.

## Stage 9 — Reports

**Goal.** The statements, the ledgers and the partner reports, with export.

- *To build:* Trial Balance (a tree, with initial / period / end columns),
  General Ledger, Partner Ledger, Balance Sheet and Profit & Loss with
  year-on-year columns, Cash Flow Statement built from account Tags, Aged
  Receivable and Aged Payable by **due date**, export to PDF and Excel; the
  flexible sales and purchase report builder last.
- *The dashboard gains:* each figure opens the report behind it.

## Stage 10 — Users, roles and going live

**Goal.** More than one person can use it safely, on the real database.

- *Already there:* login, first-time setup and company memberships (checked
  end to end). The platform-administrator and subscription screens exist and
  open; their rules are covered by `test_auth.py`, but the screens themselves
  have not been driven end to end.
- *To build:* roles actually enforced on every screen; an add-user screen;
  sign-out and action messages shown in the page frame; companies created
  before logins existed given an owner; the starting subscription state of a
  new company; safe defaults for a live server; database indexes for a large
  ledger; a first run against the real (Neon) database.
- This stage can be moved earlier — it should be finished before a second
  person starts entering real data.

## Stage 11 — Design pass

A final sweep so every screen uses the same hero, cards, tables and status
pills as the dashboard. Known to need it today: the first-time setup screen,
the subscription renewal screen and the two platform-administrator screens.

---

## Decisions — who decided what

Only Abdul approves an accounting treatment. A point marked **Proposal** is
NOT approved and nothing has been built from it.

| # | Point | Who decided | State |
|---|---|---|---|
| 1 | A bank or cash account is marked by its **Kind** (Bank / Cash). | Abdul, 13 Nov 2026 | Built |
| 2 | On a journal entry the **General account** column lists every account that is not a partner account: revenue and expenses, and also bank, cash, tax ledgers, equity and other assets and liabilities. | Assumed by the agent. Abdul to confirm. | Built. If only revenue and expense accounts are wanted there, it is a one-line change. |
| 3 | Customers and vendors are picked **by name**; the system posts to 1100 / 2000. Directors and employees get **one account each**, marked Non-trading receivable / payable. | Abdul, 13 Nov 2026 | Built |
| 4 | "Available" bank accounts means **active** ones. An inactive account keeps its balance and is still counted in every total. | Assumed by the agent. Abdul to confirm. | Built |
| 5 | **Bank Internal Transfer:** two entries dated the same day, meeting in one dedicated asset account, "Internal Transfers in Transit". Out: Dr Transit / Cr source. In: Dr destination / Cr Transit. | **Proposal** by the agent | Not approved. Not built. |
| 6 | **Voucher numbers:** `BPV/2026/0001` style (BPV, CPV, BRV, CRV, JV), given when the voucher is posted. | **Proposal** by the agent | Not approved. Not built. |
| 7 | The **Analytic Distribution** column is left out. | **Proposal** by the agent | Nothing built either way. |
| 8 | **Colours:** the palette in `static/tokens.css` is used (teal `#278C83`, charcoal `#243448`, ivory `#FAFAF6`); the brief gives slightly different shades. | Existing colours kept. Abdul to confirm. | No change made. |
| 9 | A **negative line on a vendor bill** is refused. | Existing behaviour kept. Abdul to confirm. | No change made. |
| 10 | **Roles and the add-user screen** are built in stage 10, before going live. | **Proposal** by the agent | Not built. |
| 11 | A **vendor bill line** may use Expense accounts, Stock accounts and Fixed asset accounts, picked through filter boxes. | Abdul, 14 Nov 2026 | Not built. How the filter boxes should look is still open. |

**Still with Abdul:**

1. Does the real database hold companies created before logins existed?
   (They would have no member and could not be opened.)
2. After this version first starts on the real database: look at the Kind
   column of the Chart of Accounts once. A bank account kept under a code
   outside 1000–1099 needs to be marked Bank by hand (Edit), and any director
   or employee account needs its kind set.
3. Is there a newer copy of the project than the one on GitHub? The owner's
   `AGENTS.md` describes modules (reports, credit notes, batch payments, bank
   transfers, a `tests/` folder, `PROJECT.md`) that this repository does not
   have.
4. The product's name. Abdul's last commit renamed the project to `zyro-erp`
   (the README title); the build brief and every screen say LedgerPulse.
   Nothing was changed either way.
5. Is a posted document reversed on its original date or on today's date?
   Should a payment that makes cash negative be blocked, or only warned?

---

## Known problems — found 14 Nov 2026, NOT fixed

Each was reproduced on a throwaway copy of the demo database.

1. **Vendor bill total is not checked.** Quantity 2 × rate 50 with 999 in the
   Total box was saved as 999.
2. **A bill line can use any account.** Bills on the Bank account, on Sales
   Revenue and on Trade Creditors itself were accepted (see point 11 above).
3. **Nothing can be corrected.** No edit or delete for a draft and no reversal
   for a posted document, on any screen. Payments and receipts post at once;
   sending the payment form twice saved two posted payments.
4. **No limits on amounts or dates.** 99,000,000 was paid from Cash in Hand
   holding 90,000; invoices dated 1990 and 2099 were posted.
5. **Partners are known by name only.** Two customers with the same name can
   be saved, and journal lines record only the name.
6. **Ordinary typing can crash a form** ("Internal Server Error"): payment
   terms over 50 characters, a memo over 255, a journal entry reference over
   100, an NTN over 30, an amount of 13 digits. In all, 194 of 1,411 wrong
   inputs crashed, on 9 of 13 forms.
7. **A refused form comes back empty.** Invoice lines, bill lines and the
   payment memo are lost after one wrong value.
8. **A failed posting shows no message** (tested on a sales invoice).
9. **Screens break at laptop width.** New Vendor Bill runs 253 px off a
   1366 px screen; on New Sales Invoice the Account and Unit boxes are cut
   short. On a phone-width screen almost every page scrolls sideways.
10. **Screen and saved total can differ by 0.01.** 2.5 × 33.33 is 83.32 by the
    screen's arithmetic and is saved as 83.33.
11. **Switched-off records are still accepted** by the server (customer,
    vendor, product, tax rate), and no screen can edit or switch them off.
12. Smaller: a customer whose name is only spaces is saved; lists show
    amounts without thousands separators; `accounting/routes.py` totals a
    journal entry's detail page with `float`.

**Missing in Sales:** quotation, sales order, delivery, credit note, invoice
number, print, paid / unpaid status.
**Missing in Purchases:** RFQ, purchase order, goods receipt, credit note,
batch payment, the vendor's bill number, sales tax on a bill.
**Roles:** Admin, Accountant and Viewer exist in the database; no screen
checks them.
