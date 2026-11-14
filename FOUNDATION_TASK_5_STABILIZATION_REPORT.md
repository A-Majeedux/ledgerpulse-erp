# LEDGERPULSE — FOUNDATION TASK 5 STABILIZATION REPORT

**Date:** 13 November 2026
**Scope:** Make the existing Foundation, Accounting, Vendors, Customers, WHT and Sales screens actually work end to end, and prove it by running them. No new module was started.
**Method:** The app was run against a real PostgreSQL 16 database and driven through its own HTML forms. The Task 4 report's section 14 lists what could not be tested then because live HTTP/PostgreSQL access was unavailable; this task is that testing, plus the fixes it led to.

---

## 1. Verified facts — defects found by running the app, and their fixes

Rows 1–12 were reproduced by running the app before anything was changed; row 13 was found by reading the code. Every row is covered by `test_workflows.py` afterwards.

| # | What happened | Fix | Where |
| - | --- | --- | --- |
| 1 | The app could not start on an empty database: `relation "organizations" does not exist`. | Create `organizations` before `auth` (auth's tables reference it). | `app.py` |
| 2 | 21 of 29 POST forms had no CSRF token, so submitting them returned HTTP 400. Only login, setup, the two admin screens and renewal worked. (Security Review Finding 5.) | `{{ csrf_field() }}` added to each form. | 21 templates |
| 3 | The company name typed at first-time setup was ignored; the company stayed "My Organization". | Setup renames the untouched placeholder to the typed name. A company that already has a real name is never renamed. | `auth/routes.py`, `organizations/schema.py` |
| 4 | A company created with "New Organization" had no Chart of Accounts, WHT rates or Sales Tax rates until the app was restarted; payments in it failed. | Each segment's starter data moved into `seed_organization(cursor, organization_id)`; `core.db.seed_new_organization()` runs them inside the transaction that creates the company. | `core/db.py`, five `schema.py` files, `organizations/routes.py` |
| 5 | The creator of a new company got no membership, so switching away and back returned 403. | `auth.services.add_organization_admin()` in the same transaction. | `auth/services.py`, `organizations/routes.py` |
| 6 | The top-bar switcher, the picker and the Organizations screen listed every company in the database (name, NTN, STRN, addresses), member or not. | All three list only the logged-in user's memberships. | `core/template_context.py`, `organizations/routes.py` |
| 7 | Posting an already-posted journal entry again (double click, stale tab) overwrote its `posted_at`. | `post_journal_entry()` changes Draft entries only. | `accounting/services.py` |
| 8 | The draft journal behind a vendor bill or sales invoice could be posted from the Journal Entries screen, leaving the ledger Posted and the bill Draft. | Refused (HTTP 409); the Post button is replaced by a link to the owning document. | `accounting/routes.py`, `journal_entry_detail.html` |
| 9 | These were saved and posted: payments and receipts of zero or a negative amount; WHT ticked with no rate; a WHT rate that is not on the Tax Rates list (99% and 150% were both posted); a cheque with no number; a journal entry, bill or invoice with no lines; journal lines with a negative amount or with a debit and a credit together; an account of an unknown type; a company named only spaces. | Refused with a message; nothing is written. A WHT rate must now be an active rate on the Tax Rates list, so a retired rate is refused too. | `core/validation.py` (new), `accounting/services.py`, all route files |
| 10 | These showed "Internal Server Error": a duplicate account code; a journal entry using another company's accounts (a tab left open before switching company); a non-numeric amount; a missing date. | Each returns a message on the form. | `accounting/routes.py`, `core/validation.py` |
| 11 | A logo upload accepted any file. `page.html` containing a script was stored and then served as `text/html` from the app's own address. Two companies uploading `logo.png` shared one file; the second overwrote the first. | Only `.png .jpg .jpeg .gif .webp` are accepted, and every upload is stored under a fresh random name. | `organizations/routes.py` |
| 12 | Every page printed its sidebar key ("vendors", "journals", …) as stray text at the top. | The key moved into a `<body>` attribute. | `templates/base.html` |
| 13 | A cheque number left in the hidden cheque box was stored on a non-cheque payment. | Cheque fields are kept only when the method is Cheque. | `vendors/routes.py` |

With rows 1–2 fixed, the existing posting logic turned out to be correct: journal entries, vendor bills, vendor payments with WHT, customer receipts with WHT and sales invoices all produce the journals in the brief's section 8, and no company can read or write another's records.

---

## 2. Rules now enforced in one place (for every future segment)

- **`create_journal_entry()`** refuses, with a `JournalEntryError` whose message can be shown to the user: no lines; a line with no account or another company's account; a negative, NaN or infinite amount; a debit and a credit on one line; an entry that does not balance. Routes catch `JournalEntryError` (the base of `UnbalancedEntryError` and the new `InvalidEntryError`).
- **`post_journal_entry()`** posts Draft entries only and returns whether it posted.
- **`core/validation.py`** — `parse_amount`, `parse_percent`, `parse_date`, `require_text`, raising `ValidationError`. Voucher forms use these instead of converting `request.form` by hand.
- **`wht.routes.pick_wht_rate()`** — the submitted WHT rate must be one of the company's active rates. Vendor Payments and Customer Receipts both use it.
- **Starter data** — a segment that seeds per-company data provides `seed_organization()` and is added to `register_organization_seeders([...])` in `app.py`.

---

## 3. Files

**Created:** `core/validation.py`, `test_workflows.py`, `requirements.txt`, `.env.example`, this report.

**Changed:** `app.py`, `core/db.py`, `core/template_context.py`, `accounting/{schema,services,routes}.py`, `auth/{services,routes}.py`, `organizations/{schema,routes}.py`, `vendors/{schema,routes}.py`, `customers/{schema,routes}.py`, `sales/{schema,routes}.py`, `wht/{schema,routes}.py`, `README.md`, `.gitignore` (`static/uploads/`), and 21 templates.

---

## 4. Database changes

**None.** A database built by the original code (with only the start-order swap it needs to boot) and one built by the current code were compared in full: 207 columns, 76 constraints, 33 indexes and 80 seeded rows are identical. An existing database needs no migration.

---

## 5. Verification

| Check | Result |
| --- | --- |
| `python3 -m py_compile` on every `.py` | OK |
| `python3 test_money.py` | passed |
| `python3 test_integration.py` | passed |
| `python3 test_auth.py` | 18 of 18 passed. Four of them need a reachable database (they fail with HTTP 500 without one; that was already so before this task). |
| `python3 test_workflows.py` (new) | 118 of 118 passed |
| Would the new tests notice a regression? | 14 of the defects above were deliberately put back, one at a time, in throwaway copies of the code. `test_workflows.py` failed every time. |
| JavaScript changed in three forms | Run in a DOM emulator (jsdom) on the pages as rendered by the app: 34 of 34 checks passed. This check is not part of the repository. |

---

## 6. Not verified

- **Neon.** Everything ran on a local PostgreSQL 16.2, not on Neon.
- **A real browser.** Server behaviour was tested through the real forms, and the scripts in a DOM emulator, but no page was looked at in a browser. Visual appearance is unchecked.
- **An existing database with real data.** Only fresh databases were used. Section 4 shows the structure is unchanged, but no legacy data set was available to run against.
- **Two people working at the same moment** (concurrency).

---

## 7. Still open — needs a decision

1. **Roles are not enforced** (Security Review Finding 6). `has_permission()` exists but no route calls it: every member of a company can do everything in it. There is also no screen to add a second user.
2. **Companies that existed before logins** have no member assigned. They could already not be opened (403); after fix 6 they are also not listed. The approved plan (Remediation Plan, Rule 2) calls for an explicit mapping step, which does not exist yet.
3. **Setup still hands the first existing company to the first administrator** (Security Review Finding 2). Only the placeholder-name case was changed.
4. **"Paid from" and "Deposit to" list every account**, including revenue and equity. The brief does not say how a bank or cash account is identified.
5. **A zero-value bill or invoice that has lines** is still accepted. Only documents with no lines are refused.
6. **Negative lines on a vendor bill** (e.g. a discount line) are refused, because a negative debit is. If they are wanted, how they should post needs to be defined.
7. **`python3 app.py` starts Flask's debugger by default** on every network interface. `.env.example` documents `FLASK_DEBUG=false`; the default was left as it was.
8. **Payment and receipt journals carry no link back** to their payment (`source_id` is empty). This will matter for the All Payments list and for drill-down from reports.
