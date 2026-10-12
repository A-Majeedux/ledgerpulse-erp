# LEDGERPULSE FOUNDATION TASK 2 — MONEY SAFETY AUDIT

## A. EXECUTIVE SUMMARY

LedgerPulse Foundation Task 2 provides a comprehensive, read-only audit of monetary data flows, data types, precision handling, rounding behavior, and accounting balance validation across all modules (Accounting, Sales, Vendors, Customers, WHT, and Organizations).

Our code inspection confirms that PostgreSQL database tables consistently use `NUMERIC(14, 2)` (and `NUMERIC(5, 2)` for percentage rates) for all financial storage (`debit`, `credit`, `amount`, `total`, `tax_amount`, `wht_amount`, `credit_limit`, `rate`). However, Python application logic across routes and services heavily utilizes Python built-in floats (`float(...)`) for input parsing, line item calculations, discount/tax computations, and summation, before inserting them into the database or passing them to the accounting service.

While explicit `round(..., 2)` calls are present in sales invoice lines, vendor payments, and customer receipts, and the central accounting service (`accounting/services.py`) protects against posting unbalanced journal entries by scaling sums to cent-level integers (`round(total * 100)`), the reliance on Python `float` introduces binary floating-point rounding risks and potential penny discrepancies in complex calculations or large aggregations.

## B. MONETARY DATA FLOW

1. **User Input / Forms:** Monetary amounts, quantities, rates, and discounts are submitted via HTML form POST requests as string values (e.g., `request.form["amount"]`, `request.form.getlist("rate[]")`).
2. **Python Parsing & Calculation:** Route handlers (`sales/routes.py`, `vendors/routes.py`, `customers/routes.py`) convert form strings to `float` using `float(...)` or `int(...)`. Intermediate values (gross, discount amount, net, tax amount, line total) are calculated using arithmetic on floats and rounded to 2 decimal places using Python's built-in `round(val, 2)`.
3. **Database Insertion:** Calculated `NUMERIC(14, 2)` values are passed via parameterized SQL queries (`psycopg2`) directly into PostgreSQL columns defined as `NUMERIC(14, 2)`.
4. **Database Retrieval:** PostgreSQL `NUMERIC` columns are automatically typecast by `psycopg2` into Python `Decimal` objects.
5. **Journal Creation (`accounting/services.py`):** Business transactions invoke `create_journal_entry()`, passing dictionaries with `debit` and `credit` values. The service converts these values to `float`, sums them via `sum(...)`, verifies balance using cent-level rounding (`round(total * 100)`), and inserts them into `journal_lines` (`NUMERIC(14, 2)`).
6. **Display / Reporting:** Financial reports and detail views retrieve values from database or sum them using Python `float` conversions or SQL `COALESCE(SUM(...), 0)`.

## C. FLOAT USAGE FINDINGS

| File | Function / Route | Variable / Expression | Monetary Value | Impact on Accuracy | Severity |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `accounting/services.py` | `create_journal_entry()` | `total_debit = sum(float(line.get("debit", 0) or 0)...)` | Journal debits/credits | Binary float summation can introduce penny drift across multiple lines. | HIGH |
| `accounting/services.py` | `post_journal_entry()` | `total_debit = float(total_debit or 0)` | Posted journal validation | Float conversion of database Decimal totals for balance check. | MEDIUM |
| `accounting/routes.py` | `journal_entry_detail()` | `total_debit = sum(float(line[4]) for line in lines)` | Detail view totals | Float summation of database numeric lines. | LOW |
| `sales/routes.py` | `new_sales_invoice()` | `qty`, `rate`, `disc_pct`, `gross`, `disc_amt`, `net`, `tax_amt`, `line_tot` | Invoice lines, subtotal, tax, grand total | Extensive float arithmetic before rounding and database insertion. | HIGH |
| `sales/routes.py` | `sales_invoice_detail()` | `total_gross`, `total_discount`, `total_tax`, `grand_total` | Invoice summary totals | Float summation of fetched numeric line amounts. | MEDIUM |
| `vendors/routes.py` | `new_vendor_bill()` | `total = float(totals[i] or 0)`, `line_total_sum` | Bill lines and total | Float summation of bill line totals. | MEDIUM |
| `vendors/routes.py` | `new_vendor_payment()` | `base_amount`, `wht_rate`, `wht_amount`, `net_amount` | Vendor payment, WHT, net | Float multiplication and rounding for WHT. | HIGH |
| `customers/routes.py` | `new_customer_receipt()` | `gross_amount`, `wht_rate`, `wht_amount`, `net_received` | Customer receipt, WHT, net | Float multiplication and rounding for WHT. | HIGH |

## D. DECIMAL USAGE FINDINGS

- **Python Side:** The `decimal` module (`Decimal`, `Decimal()`, `quantize`) is **never** imported or used in any Python calculation or route handler across the repository.
- **Database Side:** PostgreSQL `NUMERIC(14, 2)` and `NUMERIC(5, 2)` are used universally for all monetary and rate columns. When `psycopg2` fetches these columns, it returns them as Python `Decimal` objects. However, Python route code immediately casts them to `float` (e.g., `float(line[5])`) for summation or display formatting.

## E. ROUNDING FINDINGS

1. **Where rounding occurs:**
   - Sales invoice lines: `disc_amt = round(gross * (disc_pct / 100), 2)`, `tax_amt = round(net * (tax_pct / 100), 2)`, `line_tot = round(net + tax_amt, 2)` (`sales/routes.py`).
   - Customer receipts: `wht_amount = round(gross_amount * (wht_rate / 100), 2)` (`customers/routes.py`).
   - Vendor payments: `wht_amount = round(base_amount * (wht_rate / 100), 2)` (`vendors/routes.py`).
   - Journal balance validation: `round(total_debit * 100) != round(total_credit * 100)` (`accounting/services.py`).
2. **Timing:** Rounding occurs at the individual line calculation step in sales invoices, and at the voucher level for WHT in customer receipts and vendor payments, prior to database insertion.
3. **Float involvement:** Because inputs and intermediate calculations use Python `float`, values passed into `round()` are floating-point numbers, which can occasionally exhibit binary representation artifacts (e.g., `.125` rounding quirks in Python's IEEE 754 half-even rounding `round()`).
4. **Consistency across modules:** Sales, Vendors, and Customers modules all use Python's built-in `round(..., 2)`. No custom rounding mode (such as `ROUND_HALF_UP`) is explicitly specified.

## F. DATABASE MONEY TYPES

All monetary and rate columns are explicitly defined using fixed-precision PostgreSQL data types:
- `chart_of_accounts`: N/A (master table)
- `journal_lines`: `debit NUMERIC(14, 2) NOT NULL DEFAULT 0`, `credit NUMERIC(14, 2) NOT NULL DEFAULT 0`
- `customer_receipts`: `amount NUMERIC(14, 2) NOT NULL`, `wht_amount NUMERIC(14, 2) DEFAULT 0`, `wht_rate NUMERIC(5, 2)`
- `customers`: `credit_limit NUMERIC(14, 2) DEFAULT 0`
- `vendors`: `credit_limit NUMERIC(14, 2) DEFAULT 0`
- `vendor_bill_lines`: `total NUMERIC(14, 2) NOT NULL DEFAULT 0`, `quantity NUMERIC(14, 2) DEFAULT 1`, `rate NUMERIC(14, 2) DEFAULT 0`
- `vendor_payments`: `amount NUMERIC(14, 2) NOT NULL`, `wht_amount NUMERIC(14, 2) DEFAULT 0`, `wht_rate NUMERIC(5, 2)`
- `sales_tax_rates`: `rate_percent NUMERIC(5, 2) NOT NULL`
- `sales_invoice_lines`: `quantity NUMERIC(14, 2) DEFAULT 1`, `rate NUMERIC(14, 2) DEFAULT 0`, `discount_percent NUMERIC(5, 2) DEFAULT 0`, `discount_amount NUMERIC(14, 2) DEFAULT 0`, `tax_amount NUMERIC(14, 2) DEFAULT 0`, `total NUMERIC(14, 2) NOT NULL DEFAULT 0`
- `tax_rates` (WHT): `rate_percent NUMERIC(5, 2) NOT NULL`

## G. ACCOUNTING SERVICE ANALYSIS (`accounting/services.py`)

- **`create_journal_entry()`**:
  - Inputs: `lines` list containing dicts with `debit` and `credit` values.
  - Calculation: Converts debits and credits via `float(line.get("debit", 0) or 0)` and sums them using Python's `sum()`.
  - Balance Validation: Compares `round(total_debit * 100) != round(total_credit * 100)`.
  - Risk: If input numbers have float precision drift, multiplying by 100 and rounding can occasionally mismatch by 1 cent, though `NUMERIC(14,2)` inputs from routes minimize this.
- **`post_journal_entry()`**:
  - Fetches `SUM(debit), SUM(credit)` from database. Since database columns are `NUMERIC(14,2)`, psycopg2 returns `Decimal`. The function explicitly casts them: `total_debit = float(total_debit or 0)` before performing the cent-level rounding check.

## H. SALES TAX ANALYSIS (`sales/routes.py`)

- **Tax Rate Input/Storage:** Stored in `sales_tax_rates` as `NUMERIC(5, 2)`. Manually selected by bookkeeper.
- **Tax Calculation:** `tax_amt = round(net * (tax_pct / 100), 2)`. Uses Python float multiplication/division followed by `round(..., 2)`.
- **Journal Integration:** Aggregated total tax (`total_tax`) is posted as a credit to Account `2200` ("Output Tax Payable").

## I. WHT ANALYSIS (`customers/routes.py` & `vendors/routes.py`)

- **Rate Input/Storage:** Stored in `tax_rates` as `NUMERIC(5, 2)`. Manually selected by bookkeeper.
- **Calculation:**
  - Vendor Payment: `wht_amount = round(base_amount * (wht_rate / 100), 2)`, `net_amount = base_amount - wht_amount`.
  - Customer Receipt: `wht_amount = round(gross_amount * (wht_rate / 100), 2)`, `net_received = gross_amount - wht_amount`.
- **Journal Integration:** Posts WHT Payable (vendors) or WHT Receivable (customers) alongside bank/cash and trade accounts.

## J. SALES INVOICE ANALYSIS (`sales/routes.py`)

- **Line Calculations:**
  - `gross = qty * rate`
  - `disc_amt = round(gross * (disc_pct / 100), 2)`
  - `net = gross - disc_amt`
  - `tax_amt = round(net * (tax_pct / 100), 2)`
  - `line_tot = round(net + tax_amt, 2)`
- **Header / Journal Total:** Sums line totals into `invoice_grand_total` (Dr Trade Receivables), net revenue by account (Cr Revenue Accounts), and total tax (Cr Output Tax Payable).

## K. VENDOR BILL / PAYMENT ANALYSIS (`vendors/routes.py`)

- **Vendor Bills:** Iterates over bill lines, parses `total = float(totals[i] or 0)`, sums line totals to determine bill total and posts Dr Expense / Cr Trade Creditors.
- **Vendor Payments:** Parses base amount, calculates WHT amount via `round(base_amount * (wht_rate / 100), 2)`, computes net payment, and posts Dr Trade Creditors / Cr Bank/Cash and Cr WHT Payable.

## L. CUSTOMER RECEIPT ANALYSIS (`customers/routes.py`)

- **Customer Receipts:** Parses gross amount, calculates WHT via `round(gross_amount * (wht_rate / 100), 2)`, computes net deposit, and posts Dr Bank/Cash, Dr WHT Receivable / Cr Trade Receivables.

## M. DASHBOARD / REPORTING ANALYSIS

- **Dashboard (`dashboard/routes.py`):** Currently displays organization name only; no financial aggregations exist yet.
- **Reports:** Segment-specific list views (sales invoices list, vendor bills list, customer receipts list, vendor payments list) use SQL queries with `COALESCE(SUM(...), 0)` or iterate over fetched database records.

## N. DUPLICATE / INCONSISTENT MONEY LOGIC

- **Inconsistency:** Some modules calculate line items using Python float math (`sales/routes.py`), while others accept pre-calculated totals or direct amounts (`vendors/routes.py`, `customers/routes.py`).
- **Journal Service:** `create_journal_entry()` relies on `float()` conversion of incoming dictionary values for balance verification, whereas database storage is strict `NUMERIC(14, 2)`.

## O. RISK CLASSIFICATION

| Finding | File | Function | Risk | Reason | Recommended Scope |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Python Float Calculation | `sales/routes.py` | `new_sales_invoice()` | HIGH | Use of `float` for quantities, rates, discounts, and taxes before rounding can cause floating-point drift. | Transition intermediate financial calculations to `decimal.Decimal` in future updates. |
| Python Float Summation in Journal Service | `accounting/services.py` | `create_journal_entry()` | HIGH | Summing lines via `float()` before cent-rounding check can risk penny imbalance mismatches. | Use `decimal.Decimal` for debits/credits summation and balance verification. |
| Float Conversion of DB Decimals | `accounting/services.py` | `post_journal_entry()` | MEDIUM | Converting `SUM(debit)` (PostgreSQL numeric) to `float` before check. | Maintain `Decimal` arithmetic throughout post validation. |
| Vendor Bill Line Parsing | `vendors/routes.py` | `new_vendor_bill()` | MEDIUM | Direct parsing of `float(totals[i])` without explicit server-side line multiplication/rounding check. | Add explicit Decimal-based line calculation verification. |

## P. PROPOSED MINIMAL CHANGE SCOPE

When implementation is eventually approved for monetary safety hardening:
1. Replace Python `float` arithmetic and summation in `accounting/services.py`, `sales/routes.py`, `vendors/routes.py`, and `customers/routes.py` with `decimal.Decimal` and `quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)`.
2. Ensure database-fetched `Decimal` values flow through accounting checks without intermediate `float` casts.
3. Keep database schema (`NUMERIC(14,2)`) entirely unchanged.
4. Do **not** perform a global application-wide refactor of unrelated modules or UI templates.

## Q. NOT TESTED

- Execution against a live PostgreSQL database instance with active transactional data was not performed in this read-only audit session (verified solely via static code inspection and architectural analysis).

## R. FINAL AUDIT CONCLUSION

* **READY FOR IMPLEMENTATION** (pending explicit user approval of the proposed minimal change scope).

---
**Verification Basis:**
- **VERIFIED BY CODE INSPECTION:** All schema files, route files, and accounting services inspected via direct file reads and grep searches.
- **VERIFIED BY EXECUTION:** N/A (read-only audit).
- **INFERRED / NOT TESTED:** Database transactional behavior under concurrent load.
