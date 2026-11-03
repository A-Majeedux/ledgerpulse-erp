# VENDORS_AUDIT.md — Phase 0 Audit Report

## 1. Table Names Used in Code & Schema
- **Vendors**: `vendors`
- **Vendor Bills**: `vendor_bills`, `vendor_bill_lines`
- **Vendor Payments**: `vendor_payments`
- **Journal Entries / Lines**: `journal_headers`, `journal_lines`
- **Chart of Accounts**: `chart_of_accounts`
- **Withholding Tax / Master**: `wht_rates` (via `wht` module) and accounts `2100` ("WHT Payable - Supplies"), `2110` ("WHT Payable - Services").

## 2. Voucher Number Generation Today
- Vendor Bills: Currently no auto-numbering stored in table (or relies on ID / reference).
- Vendor Payments: Currently no auto voucher number prefix (`BPV-NNNNN` or `CPV-NNNNN`) in `vendor_payments` table, though unique cheque number constraint exists.

## 3. Customer Module Payment Voucher Pattern (Reference)
- Customers module uses `customer_receipts` table with sequence generation and allocation against sales invoices. We should follow similar patterns for vendor payment allocations.

## 4. Feature-by-Feature Audit (Section 4 Target Features)

### Phase 1 — Vendors (Master list + card)
- **EXISTS and works**: Basic vendor list (`vendors`), add vendor form (`new_vendor`), table schema with `nature_of_purchase`, `ntn`, `strn`, `address`, `credit_limit`, `is_active`.
- **EXISTS but incomplete or buggy**: Detail card view, contacts, purchase terms, accounting defaults, smart buttons (bills count, payments count), search & pagination.

### Phase 2 — Vendor Bills
- **EXISTS and works**: Vendor bills list, new vendor bill form with line items, posting action which creates a balanced journal entry (`journal_headers` / `journal_lines`) using `accounting/services.py::create_journal_entry()` and `post_journal_entry()`.
- **EXISTS but incomplete or buggy**: Bill detail view (`vendor_bill_detail.html`), editing/deleting draft bills, print view, bill number auto-generation (`BILL/YYYY/MM/NNNN`), payment status tracking (Draft / Posted / Paid).

### Phase 3 — Vendor Credit Notes (Refunds)
- **EXISTS but incomplete or buggy**: **MISSING** (Credit notes routes, schema, and templates need implementation).

### Phase 4 — Vendor Payments
- **EXISTS and works**: Vendor payments list, new vendor payment form with WHT deduction (supplies vs services), unique cheque number validation per bank account (`uq_vendor_payment_cheque`), journal entry creation (`create_journal_entry`).
- **EXISTS but incomplete or buggy**: Payment detail view, status bar (Draft / In Process / Paid), allocation against open bills, print view (Bank Payment Voucher / BPV).

### Phase 5 — Batch Vendor Payments
- **EXISTS but incomplete or buggy**: **MISSING** (Batch payments schema, routes, templates).

### Phase 6 — Own Cheques Report
- **EXISTS but incomplete or buggy**: **MISSING** (Report view for issued cheques, filters, export/print).

### Phase 7 — Vendor Reports and Polish
- **EXISTS but incomplete or buggy**: **MISSING** (Vendor Partner Ledger, Vendor Aging report).
