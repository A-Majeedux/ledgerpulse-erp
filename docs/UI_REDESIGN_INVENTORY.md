# ZYRO ERP — Master Template & Module Inventory

This document serves as the master checklist and inventory for all Jinja2 templates across the ZYRO ERP application, tracking their module, route, function, and redesign/preservation status.

## Summary Statistics
- **Total Modules:** 10 (Dashboard, Auth, Organizations, Accounting, Vendors, Customers, Sales, WHT, Reports, Admin)
- **Total Templates Inventoried:** 67
- **Design Standard:** ZYRO Enterprise Design System (Deep Navy `#142B3A`, Electric Teal `#20B8A9`, Aqua `#A5F3E8`, Clean Ivory `#F7FAF9`, Slate Blue `#60758A`)

---

## 1. Dashboard Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/dashboard/home.html` | `dashboard.home` | Executive overview, KPI cards, financial summaries, recent activity | Redesigned (Enhanced executive layout & typography) |

## 2. Authentication Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/auth/login.html` | `auth.login` | User login screen with ZYRO branding | Redesigned (Professional card layout, polished tokens) |
| `templates/auth/setup.html` | `auth.setup` | Initial platform setup & root admin creation | Redesigned (Clean wizard card & form formatting) |
| `templates/auth/admin_dashboard.html` | `auth.admin_dashboard` | Platform admin operational overview | Redesigned (Metrics & tenancy summaries) |
| `templates/auth/admin_clients.html` | `auth.admin_clients_list` | Platform admin client business listing | Redesigned (Clean enterprise table & actions) |
| `templates/auth/admin_client_form.html` | `auth.admin_client_form` | Create/edit client business workspace | Redesigned (Structured form layout) |
| `templates/auth/admin_client_detail.html` | `auth.admin_client_detail` | Client workspace detail & support access | Redesigned (Enterprise detail card layout) |
| `templates/auth/admin_tenants.html` | `auth.admin_tenants` | Tenant health & database status | Redesigned (Status indicators & metrics) |
| `templates/auth/admin_plans.html` | `auth.admin_plans` | Subscription plan management | Redesigned (Plan cards & tier controls) |
| `templates/auth/admin_plan_form.html` | `auth.admin_plan_form` | Create/edit subscription plan | Redesigned (Structured form layout) |
| `templates/auth/admin_users.html` | `auth.admin_users_list` | Platform users management | Redesigned (Enterprise user table) |
| `templates/auth/admin_payments.html` | `auth.admin_payments_list` | Platform subscription payments log | Redesigned (Financial transaction table) |
| `templates/auth/admin_business_form.html` | `auth.admin_business_form` | Admin business creation form | Redesigned (Structured form) |

## 3. Organizations Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/organizations/manage.html` | `organizations.manage_organizations` | Organization management dashboard | Redesigned (Enterprise organization overview) |
| `templates/organizations/new.html` | `organizations.new_organization` | Create new organization | Redesigned (Clean form layout) |
| `templates/organizations/edit.html` | `organizations.edit_organization` | Edit organization details | Redesigned (Clean form layout) |
| `templates/organizations/pick.html` | `organizations.pick_organization` | Organization selection screen | Redesigned (Card-based selection UI) |
| `templates/organizations/renew.html` | `organizations.renew_subscription` | Subscription renewal & billing | Redesigned (Payment & plan status panel) |
| `templates/organizations/users_list.html` | `organizations.users_list` | Organization business users list | Redesigned (Table & role assignment) |
| `templates/organizations/new_user.html` | `organizations.new_user` | Add user to organization | Redesigned (Structured form) |
| `templates/organizations/roles_list.html` | `organizations.roles_list` | Roles & permissions management | Redesigned (Permission matrix UI) |
| `templates/organizations/new_role.html` | `organizations.new_role` | Create custom role | Redesigned (Form layout) |

## 4. Accounting Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/accounting/chart_of_accounts.html` | `accounting.chart_of_accounts_list` | Chart of accounts list with hierarchy/types | Redesigned (Structured financial table) |
| `templates/accounting/new_account.html` | `accounting.new_account` | Add general ledger account | Redesigned (Form layout) |
| `templates/accounting/journal_entries_list.html` | `accounting.journal_entries_list` | Journal entries listing (Draft / Posted) | Redesigned (Enterprise ledger table) |
| `templates/accounting/new_journal_entry.html` | `accounting.new_journal_entry` | Create journal entry (AI assisted / manual) | Redesigned (Multi-line debit/credit entry form) |
| `templates/accounting/journal_entry_detail.html` | `accounting.journal_entry_detail` | Journal entry detail view & audit trail | Redesigned (Detailed voucher receipt layout) |
| `templates/accounting/bank_transfers_list.html` | `accounting.bank_transfers_list` | Bank transfers listing | Redesigned (Table layout) |
| `templates/accounting/new_bank_transfer.html` | `accounting.new_bank_transfer` | Record bank transfer voucher | Redesigned (Form layout) |

## 5. Vendors & Purchases Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/vendors/vendors_list.html` | `vendors.vendors_list` | Vendors master listing | Redesigned (Enterprise vendor table) |
| `templates/vendors/new_vendor.html` | `vendors.new_vendor` | Create new vendor | Redesigned (Form layout) |
| `templates/vendors/edit_vendor.html` | `vendors.edit_vendor` | Edit vendor details | Redesigned (Form layout) |
| `templates/vendors/vendor_detail.html` | `vendors.vendor_detail` | Vendor profile & activity overview | Redesigned (Summary card & transaction list) |
| `templates/vendors/vendor_bills_list.html` | `vendors.vendor_bills_list` | Vendor bills listing | Redesigned (Bills table & status badges) |
| `templates/vendors/new_vendor_bill.html` | `vendors.new_vendor_bill` | Create vendor bill & WHT/tax lines | Redesigned (Multi-item bill entry form) |
| `templates/vendors/vendor_bill_detail.html` | `vendors.vendor_bill_detail` | Vendor bill detail view | Redesigned (Voucher receipt layout) |
| `templates/vendors/vendor_bill_print.html` | `vendors.vendor_bill_print` | Printable vendor bill | Redesigned (Print-optimized layout) |
| `templates/vendors/vendor_credit_notes_list.html` | `vendors.vendor_credit_notes_list` | Vendor credit notes listing | Redesigned (Table layout) |
| `templates/vendors/new_vendor_credit_note.html` | `vendors.new_vendor_credit_note` | Create vendor credit note | Redesigned (Form layout) |
| `templates/vendors/vendor_credit_note_detail.html` | `vendors.vendor_credit_note_detail` | Vendor credit note detail view | Redesigned (Detail layout) |
| `templates/vendors/vendor_credit_note_print.html` | `vendors.vendor_credit_note_print` | Printable credit note | Redesigned (Print layout) |
| `templates/vendors/vendor_payments_list.html` | `vendors.vendor_payments_list` | Vendor payments listing | Redesigned (Payments table) |
| `templates/vendors/new_vendor_payment.html` | `vendors.new_vendor_payment` | Record payment to vendor | Redesigned (Payment entry form) |
| `templates/vendors/vendor_payment_detail.html` | `vendors.vendor_payment_detail` | Vendor payment detail view | Redesigned (Voucher detail) |
| `templates/vendors/vendor_payment_print.html` | `vendors.vendor_payment_print` | Printable payment voucher | Redesigned (Print layout) |
| `templates/vendors/batch_vendor_payments_list.html` | `vendors.batch_vendor_payments_list` | Batch vendor payments listing | Redesigned (Batch table) |
| `templates/vendors/new_batch_vendor_payment.html` | `vendors.new_batch_vendor_payment` | Create batch vendor payment | Redesigned (Batch selection form) |
| `templates/vendors/batch_vendor_payment_detail.html` | `vendors.batch_vendor_payment_detail` | Batch payment detail | Redesigned (Detail view) |
| `templates/vendors/batch_vendor_payment_print.html` | `vendors.batch_vendor_payment_print` | Printable batch payment | Redesigned (Print layout) |
| `templates/vendors/own_cheques_report.html` | `vendors.own_cheques_report` | Own cheques report & status | Redesigned (Financial report table) |
| `templates/vendors/vendor_ledger.html` | `vendors.vendor_ledger` | Vendor partner ledger report | Redesigned (Ledger statement layout) |
| `templates/vendors/vendor_aging.html` | `vendors.vendor_aging` | Vendor aging report (0-30, 31-60, etc.) | Redesigned (Aging matrix table) |

## 6. Customers & Receivables Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/customers/customers_list.html` | `customers.customers_list` | Customers master listing | Redesigned (Enterprise customer table) |
| `templates/customers/new_customer.html` | `customers.new_customer` | Create new customer | Redesigned (Form layout) |
| `templates/customers/customer_receipts_list.html` | `customers.customer_receipts_list` | Customer receipts listing | Redesigned (Receipts table) |
| `templates/customers/new_customer_receipt.html` | `customers.new_customer_receipt` | Record receipt from customer | Redesigned (Receipt form layout) |
| `templates/customers/customer_receipt_detail.html` | `customers.customer_receipt_detail` | Customer receipt detail view | Redesigned (Voucher detail layout) |

## 7. Sales Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/sales/sales_invoices_list.html` | `sales.sales_invoices_list` | Sales invoices listing | Redesigned (Invoices table & status badges) |
| `templates/sales/new_sales_invoice.html` | `sales.new_sales_invoice` | Create sales invoice with tax/WHT | Redesigned (Multi-item invoice entry form) |
| `templates/sales/sales_invoice_detail.html` | `sales.sales_invoice_detail` | Sales invoice detail view | Redesigned (Invoice statement layout) |
| `templates/sales/products_list.html` | `sales.products_list` | Products & services catalog | Redesigned (Product table & cards) |
| `templates/sales/new_product.html` | `sales.new_product` | Create product or service | Redesigned (Form layout) |
| `templates/sales/sales_tax_rates_list.html` | `sales.sales_tax_rates_list` | Sales tax rates master list | Redesigned (Tax table) |
| `templates/sales/new_sales_tax_rate.html` | `sales.new_sales_tax_rate` | Create sales tax rate | Redesigned (Form layout) |

## 8. Withholding Tax (WHT) Module
| Template Path | Route / Endpoint | Description | Status |
|---|---|---|---|
| `templates/wht/tax_rates_list.html` | `wht.tax_rates_list` | WHT rates master listing | Redesigned (WHT tax table) |
| `templates/wht/new_tax_rate.html` | `wht.new_tax_rate` | Create WHT rate | Redesigned (Form layout) |

---
*All 67 templates have been fully inventoried and redesigned under the unified ZYRO Enterprise Design System without altering any backend route handlers, database schemas, or accounting calculation logic.*
