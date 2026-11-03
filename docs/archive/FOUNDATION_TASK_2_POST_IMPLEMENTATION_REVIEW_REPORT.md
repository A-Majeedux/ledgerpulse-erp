# LEDGERPULSE FOUNDATION TASK 2 — POST-IMPLEMENTATION REVIEW REPORT

## A. VERIFIED
- PostgreSQL schema types (`NUMERIC(14, 2)`, `NUMERIC(5, 2)`) remain unchanged.
- Core monetary calculations in `accounting/services.py`, `sales/routes.py`, `vendors/routes.py`, and `customers/routes.py` use `Decimal` and `money_round()` with `ROUND_HALF_UP`.
- `core/money.py` successfully provides string-safe `to_decimal()` and `money_round()`.
- Unit tests in `test_money.py` verify basic conversions, `ROUND_HALF_UP` boundary cases (`0.005`, `1.005`, `10.005`), sales tax, WHT, and float drift avoidance.

## B. FINDINGS
1. **Vendor Bill Line Total (`new_vendor_bill`):**
   - The new implementation uses `total = money_round(raw_tot) if raw_tot else money_round(qty * rate)`.
   - In the original code, `total = float(totals[i] or 0)`.
   - If `totals[i]` is supplied by the form (which it always is from the frontend UI), `raw_tot` is used. However, the fallback `qty * rate` introduces a side calculation that did not exist previously if `totals[i]` was blank/omitted.
2. **Sales Net Revenue vs Line Total Consistency (`new_sales_invoice`):**
   - `net = gross - disc_amt` was kept unrounded when accumulated into `net_revenue_by_account` and credited to revenue accounts, while `line_tot = money_round(net + tax_amt)` and `tax_amt` were rounded.
   - This creates a potential rounding discrepancy where `SUM(line totals)` (debited to AR) might not exactly equal `SUM(net revenue credits) + SUM(tax credits)` if unrounded net values accumulate fractions of a cent.
3. **Remaining Float Usage in Other Routes:**
   - Manual Journal entry creation route (`accounting/routes.py`) still parses debits/credits via `float()`.
   - Customer/Vendor credit limits (`customers/routes.py`, `vendors/routes.py`) still parse via `float()`.
   - Tax rate percentage creation (`wht/routes.py`) still parses via `float()`.

## C. REGRESSION RISKS
1. **Vendor Bill Total Override Risk:** The `qty * rate` fallback in `new_vendor_bill()` could silently recalculate a line total if `totals[i]` is missing or empty, rather than defaulting to `0.00` as original code did.
2. **Sales Journal Imbalance Risk:** Accumulating unrounded `net` into `net_revenue_by_account` while posting rounded line totals (`line_tot`) and rounded taxes (`tax_amt`) risks triggering `UnbalancedEntryError` during invoice creation if line-level rounding causes a cent drift between total debits (AR) and total credits (Revenue + Tax).

## D. REMAINING FLOAT USAGE
| File | Function / Context | Classification | Recommended Action |
| :--- | :--- | :--- | :--- |
| `accounting/routes.py` | Manual journal creation (`debits[i]`, `credits[i]`) | **A. Monetary calculation that should be fixed** | Convert manual journal debit/credit parsing to `to_decimal()`. |
| `wht/routes.py` | Tax rate percentage creation (`rate_percent`) | **A. Monetary calculation that should be fixed** | Convert `rate_percent` parsing to `to_decimal()`. |
| `customers/routes.py` & `vendors/routes.py` | Credit limit parsing (`credit_limit`) | **A. Monetary calculation that should be fixed** | Convert `credit_limit` parsing to `to_decimal()`. |
| `accounting/routes.py` | `journal_entry_detail()` summary sums | **B. Display/reporting-only** | Optional: update to Decimal for consistency. |

## E. TRANSACTION TEST GAPS
1. **Database-Free Tests:**
   - `test_money.py` successfully tests `to_decimal`, `money_round`, sales tax, WHT, and float drift without a database.
   - `create_journal_entry` balance validation can be unit-tested without a DB if account verification queries are mocked, but currently requires a connected test DB because it queries `chart_of_accounts`.
2. **Database-Required Integration Tests:**
   - Multi-line sales invoice with discounts, taxes, and multiple revenue accounts ensuring exact journal balance (`AR = Revenue + Tax`).
   - Vendor bill and vendor payment with WHT verifying `net_payment + WHT = gross_payment`.
   - Customer receipt with WHT verifying `net_received + WHT = gross_receipt`.

## F. RECOMMENDED ACTION
1. **Fix Vendor Bill Line Total:** Remove the `qty * rate` fallback in `new_vendor_bill()` so `total = money_round(totals[i]) if totals[i] else money_round(Decimal('0'))` strictly preserves original business behavior.
2. **Fix Sales Net Revenue Consistency:** Ensure `net` in `new_sales_invoice()` is explicitly rounded via `money_round()` (or derived consistently from rounded line totals) so revenue credits and AR debits match to the cent.
3. **Clean Up Remaining Floats:** Replace `float()` with `to_decimal()` in `accounting/routes.py` (manual journal entries), `wht/routes.py` (tax rates), and credit limits in customer/vendor routes.

## G. FINAL STATUS
* **NEEDS MINOR REFINEMENTS** (Vendor bill fallback adjustment and Sales net rounding consistency).
