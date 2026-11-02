from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from flask import Blueprint, render_template, request, jsonify, session
from core.context import require_organization, current_organization_id
from core.db import get_conn
from auth.services import current_user_id, current_user_name, is_platform_admin, has_permission

bp = Blueprint("dashboard", __name__)


def _is_manager(user_id, org_id):
    if is_platform_admin():
        return True
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.name FROM organization_memberships om
            JOIN roles r ON r.role_id = om.role_id
            WHERE om.user_id = %s AND om.organization_id = %s
        """, (user_id, org_id))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for r in rows:
            if r[0] in ("Admin", "Manager", "Accounting Leader"):
                return True
        if has_permission(user_id, org_id, "organizations.manage") or has_permission(user_id, org_id, "accounting.manage"):
            return True
    except Exception:
        pass
    return False


def _build_date_cond(date_range, date_col):
    today = date.today()
    if date_range == "this_month":
        return f"DATE_PART('year', {date_col}) = DATE_PART('year', CURRENT_DATE) AND DATE_PART('month', {date_col}) = DATE_PART('month', CURRENT_DATE)"
    elif date_range == "last_month":
        first_this_month = today.replace(day=1)
        lm = first_this_month - timedelta(days=1)
        return f"DATE_PART('year', {date_col}) = {lm.year} AND DATE_PART('month', {date_col}) = {lm.month}"
    elif date_range == "last_3_months":
        tma = today - timedelta(days=90)
        return f"{date_col} >= '{tma.isoformat()}'"
    elif date_range == "last_6_months":
        sma = today - timedelta(days=180)
        return f"{date_col} >= '{sma.isoformat()}'"
    elif date_range == "fy":
        fy_year = today.year if today.month >= 7 else today.year - 1
        return f"{date_col} >= '{fy_year}-07-01'"
    return "1=1"


def _get_grouping_format(grouping):
    if grouping == "daily":
        return "YYYY-MM-DD"
    elif grouping == "weekly":
        return "IYYY-IW"
    return "YYYY-MM"


@bp.route("/")
@require_organization
def home():
    org_id = current_organization_id()
    uid = current_user_id()
    user_name = current_user_name() or "User"
    is_manager = _is_manager(uid, org_id)

    conn = get_conn()
    cursor = conn.cursor()

    # Organization info
    cursor.execute("SELECT name, currency FROM organizations WHERE organization_id = %s", (org_id,))
    org_row = cursor.fetchone()
    org_name = org_row[0] if org_row else "Organization"
    currency = org_row[1] if org_row and org_row[1] else "PKR"

    # Fetch customers and vendors for filters
    cursor.execute("SELECT customer_id, name FROM customers WHERE organization_id = %s ORDER BY name", (org_id,))
    all_customers = cursor.fetchall()
    
    cursor.execute("SELECT vendor_id, name FROM vendors WHERE organization_id = %s ORDER BY name", (org_id,))
    all_vendors = cursor.fetchall()

    # Bank/Cash accounts for filters
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND (account_type = 'Asset' AND (name ILIKE '%%bank%%' OR name ILIKE '%%cash%%' OR code IN ('1000', '1010')))
        ORDER BY code
    """, (org_id,))
    bank_accounts = cursor.fetchall()
    if not bank_accounts:
        cursor.execute("""
            SELECT account_id, code, name FROM chart_of_accounts
            WHERE organization_id = %s AND account_type = 'Asset'
            ORDER BY code
        """, (org_id,))
        bank_accounts = cursor.fetchall()

    # Independent parameters per card / chart / filter
    sales_range = request.args.get("sales_range", "this_month")
    np_range = request.args.get("np_range", "this_month")
    cash_range = request.args.get("cash_range", "this_month")
    ar_range = request.args.get("ar_range", "this_month")
    ap_range = request.args.get("ap_range", "this_month")

    sales_grouping = request.args.get("sales_grouping", "monthly")
    pnl_grouping = request.args.get("pnl_grouping", "monthly")
    cash_grouping = request.args.get("cash_grouping", "monthly")
    purchases_grouping = request.args.get("purchases_grouping", "monthly")
    ar_grouping = request.args.get("ar_grouping", "monthly")
    ap_grouping = request.args.get("ap_grouping", "monthly")

    customer_id_filter = request.args.get("customer_id", "")
    vendor_id_filter = request.args.get("vendor_id", "")
    status_filter = request.args.get("status", "")

    # 1. Total Sales / Revenue
    date_cond_sales = _build_date_cond(sales_range, "si.invoice_date")
    sales_sql = f"""
        SELECT COALESCE(SUM(sil.total), 0)
        FROM sales_invoices si
        JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s AND si.status = 'Posted' AND {date_cond_sales}
    """
    sales_params = [org_id]
    if customer_id_filter:
        sales_sql += " AND si.customer_id = %s"
        sales_params.append(int(customer_id_filter))
    cursor.execute(sales_sql, tuple(sales_params))
    sales_period = cursor.fetchone()[0] or Decimal('0')

    # Purchases Outlay
    date_cond_purch = _build_date_cond(sales_range, "vb.bill_date")
    purchases_sql = f"""
        SELECT COALESCE(SUM(vbl.total), 0)
        FROM vendor_bills vb
        JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted' AND {date_cond_purch}
    """
    purchases_params = [org_id]
    if vendor_id_filter:
        purchases_sql += " AND vb.vendor_id = %s"
        purchases_params.append(int(vendor_id_filter))
    cursor.execute(purchases_sql, tuple(purchases_params))
    purchases_period = cursor.fetchone()[0] or Decimal('0')

    # 2. Net Profit or Loss
    date_cond_np = _build_date_cond(np_range, "jh.accounting_date")
    cursor.execute(f"""
        SELECT 
            COALESCE(SUM(CASE WHEN coa.account_type = 'Revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) AS total_rev,
            COALESCE(SUM(CASE WHEN coa.account_type = 'Expense' THEN jl.debit - jl.credit ELSE 0 END), 0) AS total_exp
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jh.organization_id = %s AND jh.status = 'Posted' AND {date_cond_np}
    """, (org_id,))
    np_row = cursor.fetchone()
    rev_sum = np_row[0] if np_row else Decimal('0')
    exp_sum = np_row[1] if np_row else Decimal('0')
    net_profit = rev_sum - exp_sum

    # 3. Cash and Bank Balances (As of date / treasury)
    cursor.execute("""
        SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jh.journal_header_id
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jh.organization_id = %s AND jh.status = 'Posted' AND coa.account_type = 'Asset'
          AND (coa.name ILIKE '%%bank%%' OR coa.name ILIKE '%%cash%%' OR coa.code IN ('1000', '1010') OR coa.allow_reconciliation = TRUE)
    """, (org_id,))
    cash_bank_balance = cursor.fetchone()[0] or Decimal('0')

    # 4. Customer Outstanding Receivables
    cursor.execute("""
        SELECT COALESCE(SUM(sil.total), 0)
        FROM sales_invoices si
        JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s AND si.status = 'Posted'
    """, (org_id,))
    total_invoiced = cursor.fetchone()[0] or Decimal('0')

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM customer_receipts
        WHERE organization_id = %s
    """, (org_id,))
    total_received = cursor.fetchone()[0] or Decimal('0')
    receivables_outstanding = max(Decimal('0'), total_invoiced - total_received)

    # 5. Vendor Outstanding Payables
    cursor.execute("""
        SELECT COALESCE(SUM(vbl.total), 0)
        FROM vendor_bills vb
        JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted'
    """, (org_id,))
    total_billed = cursor.fetchone()[0] or Decimal('0')

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM vendor_payments
        WHERE organization_id = %s
    """, (org_id,))
    total_paid = cursor.fetchone()[0] or Decimal('0')
    payables_outstanding = max(Decimal('0'), total_billed - total_paid)

    # 6. Unposted Voucher Count (Manager only)
    unposted_voucher_count = 0
    if is_manager:
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM sales_invoices WHERE organization_id = %s AND status = 'Draft') +
                (SELECT COUNT(*) FROM vendor_bills WHERE organization_id = %s AND status = 'Draft') +
                (SELECT COUNT(*) FROM journal_headers WHERE organization_id = %s AND status = 'Draft')
        """, (org_id, org_id, org_id))
        unposted_row = cursor.fetchone()
        unposted_voucher_count = unposted_row[0] if unposted_row else 0

    cursor.execute("""
        SELECT COUNT(*)
        FROM journal_headers
        WHERE organization_id = %s AND status = 'Posted'
    """, (org_id,))
    posted_journal_count = cursor.fetchone()[0] or 0

    # -------------------------------------------------------------
    # 6 Recent Activity Sections (Latest 15 each)
    # -------------------------------------------------------------
    # 1. Sales Invoices
    invoices_query = f"""
        SELECT si.sales_invoice_id, c.name, si.invoice_date, si.status,
               COALESCE(SUM(sil.total), 0) AS total
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        LEFT JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s
    """
    inv_params = [org_id]
    if customer_id_filter:
        invoices_query += " AND si.customer_id = %s"
        inv_params.append(int(customer_id_filter))
    if status_filter:
        invoices_query += " AND si.status = %s"
        inv_params.append(status_filter)
    invoices_query += " GROUP BY si.sales_invoice_id, c.name ORDER BY si.invoice_date DESC, si.sales_invoice_id DESC LIMIT 15"
    cursor.execute(invoices_query, tuple(inv_params))
    recent_invoices = cursor.fetchall()

    # 2. Customer Receipts
    cursor.execute("""
        SELECT cr.customer_receipt_id, c.name, cr.receipt_date, cr.amount, '—' AS payment_method
        FROM customer_receipts cr
        JOIN customers c ON c.customer_id = cr.customer_id
        WHERE cr.organization_id = %s
        ORDER BY cr.receipt_date DESC, cr.customer_receipt_id DESC
        LIMIT 15
    """, (org_id,))
    recent_receipts = cursor.fetchall()

    # 3. Vendor Bills
    bills_query = f"""
        SELECT vb.vendor_bill_id, v.name, vb.bill_date, vb.status,
               COALESCE(SUM(vbl.total), 0) AS total
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        LEFT JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s
    """
    bill_params = [org_id]
    if vendor_id_filter:
        bills_query += " AND vb.vendor_id = %s"
        bill_params.append(int(vendor_id_filter))
    if status_filter:
        bills_query += " AND vb.status = %s"
        bill_params.append(status_filter)
    bills_query += " GROUP BY vb.vendor_bill_id, v.name ORDER BY vb.bill_date DESC, vb.vendor_bill_id DESC LIMIT 15"
    cursor.execute(bills_query, tuple(bill_params))
    recent_bills = cursor.fetchall()

    # 4. Vendor Payments
    cursor.execute("""
        SELECT vp.vendor_payment_id, v.name, vp.payment_date, vp.amount, vp.payment_method
        FROM vendor_payments vp
        JOIN vendors v ON v.vendor_id = vp.vendor_id
        WHERE vp.organization_id = %s
        ORDER BY vp.payment_date DESC, vp.vendor_payment_id DESC
        LIMIT 15
    """, (org_id,))
    recent_payments = cursor.fetchall()

    # 5. Journal Entries
    cursor.execute("""
        SELECT
            jh.journal_header_id, jh.accounting_date, jh.reference, jh.journal_name, jh.status,
            COALESCE(SUM(jl.debit), 0) AS debit_total,
            COALESCE(SUM(jl.credit), 0) AS credit_total
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE jh.organization_id = %s
        GROUP BY jh.journal_header_id
        ORDER BY jh.accounting_date DESC, jh.journal_header_id DESC
        LIMIT 15
    """, (org_id,))
    recent_journals = cursor.fetchall()

    # 6. Bank Internal Transfers
    cursor.execute("""
        SELECT bt.bank_transfer_id, bt.transfer_date, sa.name, da.name, bt.amount, bt.currency
        FROM bank_transfers bt
        JOIN chart_of_accounts sa ON sa.account_id = bt.source_account_id
        JOIN chart_of_accounts da ON da.account_id = bt.destination_account_id
        WHERE bt.organization_id = %s
        ORDER BY bt.transfer_date DESC, bt.bank_transfer_id DESC
        LIMIT 15
    """, (org_id,))
    recent_transfers = cursor.fetchall()

    # -------------------------------------------------------------
    # 6 Actual Charts with Grouping (Sales, P&L, Cash, Purchases, Receivables, Payables)
    # -------------------------------------------------------------
    fmt_sales = _get_grouping_format(sales_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(si.invoice_date, '{fmt_sales}') as label, COALESCE(SUM(sil.total), 0) as total
        FROM sales_invoices si
        JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s AND si.status = 'Posted'
        GROUP BY TO_CHAR(si.invoice_date, '{fmt_sales}')
        ORDER BY TO_CHAR(si.invoice_date, '{fmt_sales}') DESC
        LIMIT 6
    """, (org_id,))
    sales_trend = list(reversed(cursor.fetchall()))

    fmt_purch = _get_grouping_format(purchases_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(vb.bill_date, '{fmt_purch}') as label, COALESCE(SUM(vbl.total), 0) as total
        FROM vendor_bills vb
        JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted'
        GROUP BY TO_CHAR(vb.bill_date, '{fmt_purch}')
        ORDER BY TO_CHAR(vb.bill_date, '{fmt_purch}') DESC
        LIMIT 6
    """, (org_id,))
    purchases_trend = list(reversed(cursor.fetchall()))

    fmt_pnl = _get_grouping_format(pnl_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(jh.accounting_date, '{fmt_pnl}') as label,
               COALESCE(SUM(CASE WHEN coa.account_type = 'Revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) -
               COALESCE(SUM(CASE WHEN coa.account_type = 'Expense' THEN jl.debit - jl.credit ELSE 0 END), 0) as net_pnl
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jh.organization_id = %s AND jh.status = 'Posted' AND coa.account_type IN ('Revenue', 'Expense')
        GROUP BY TO_CHAR(jh.accounting_date, '{fmt_pnl}')
        ORDER BY TO_CHAR(jh.accounting_date, '{fmt_pnl}') DESC
        LIMIT 6
    """, (org_id,))
    pnl_trend = list(reversed(cursor.fetchall()))

    fmt_cash = _get_grouping_format(cash_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(jh.accounting_date, '{fmt_cash}') as label,
               COALESCE(SUM(jl.debit - jl.credit), 0) as cash_flow
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jh.organization_id = %s AND jh.status = 'Posted' AND coa.account_type = 'Asset'
          AND (coa.name ILIKE '%%bank%%' OR coa.name ILIKE '%%cash%%' OR coa.code IN ('1000', '1010'))
        GROUP BY TO_CHAR(jh.accounting_date, '{fmt_cash}')
        ORDER BY TO_CHAR(jh.accounting_date, '{fmt_cash}') DESC
        LIMIT 6
    """, (org_id,))
    cash_trend = list(reversed(cursor.fetchall()))

    # Chart 5: Receivables Analysis (Customer Aging / Outstanding Trend or Top Customers by Period)
    fmt_ar = _get_grouping_format(ar_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(si.invoice_date, '{fmt_ar}') as label, COALESCE(SUM(sil.total), 0) as total
        FROM sales_invoices si
        JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s AND si.status = 'Posted'
        GROUP BY TO_CHAR(si.invoice_date, '{fmt_ar}')
        ORDER BY TO_CHAR(si.invoice_date, '{fmt_ar}') DESC
        LIMIT 6
    """, (org_id,))
    receivables_trend = list(reversed(cursor.fetchall()))

    # Chart 6: Payables Analysis (Vendor Outlay / Outstanding Trend)
    fmt_ap = _get_grouping_format(ap_grouping)
    cursor.execute(f"""
        SELECT TO_CHAR(vb.bill_date, '{fmt_ap}') as label, COALESCE(SUM(vbl.total), 0) as total
        FROM vendor_bills vb
        JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted'
        GROUP BY TO_CHAR(vb.bill_date, '{fmt_ap}')
        ORDER BY TO_CHAR(vb.bill_date, '{fmt_ap}') DESC
        LIMIT 6
    """, (org_id,))
    payables_trend = list(reversed(cursor.fetchall()))

    # Top breakdowns for reference
    cursor.execute("""
        SELECT c.name, COALESCE(SUM(sil.total), 0) as total
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s AND si.status = 'Posted'
        GROUP BY c.customer_id, c.name
        ORDER BY total DESC
        LIMIT 5
    """, (org_id,))
    receivables_analysis = cursor.fetchall()

    cursor.execute("""
        SELECT v.name, COALESCE(SUM(vbl.total), 0) as total
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted'
        GROUP BY v.vendor_id, v.name
        ORDER BY total DESC
        LIMIT 5
    """, (org_id,))
    payables_analysis = cursor.fetchall()

    # -------------------------------------------------------------
    # Section-Embedded Alerts Calculation
    # -------------------------------------------------------------
    receivables_alerts = []
    payables_alerts = []
    cash_alerts = []
    accounting_alerts = []

    # 1. Overdue customer receivables (>30 days)
    cursor.execute("""
        SELECT si.sales_invoice_id, c.name, si.invoice_date
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        WHERE si.organization_id = %s AND si.status = 'Posted'
          AND si.invoice_date < CURRENT_DATE - INTERVAL '30 days'
        ORDER BY si.invoice_date ASC
        LIMIT 1
    """, (org_id,))
    overdue_inv = cursor.fetchone()
    if overdue_inv:
        cursor.execute("SELECT COUNT(*) FROM sales_invoices WHERE organization_id = %s AND status = 'Posted' AND invoice_date < CURRENT_DATE - INTERVAL '30 days'", (org_id,))
        count_od_inv = cursor.fetchone()[0]
        receivables_alerts.append({
            "category": "Overdue Receivables",
            "severity": "Warning",
            "message": f"{count_od_inv} customer invoice(s) outstanding for over 30 days. Oldest: #{overdue_inv[0]} ({overdue_inv[1]}).",
            "link_url": f"/sales/invoices/{overdue_inv[0]}"
        })

    # 2. Overdue vendor payables (>30 days)
    cursor.execute("""
        SELECT vb.vendor_bill_id, v.name, vb.bill_date
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        WHERE vb.organization_id = %s AND vb.status = 'Posted'
          AND vb.bill_date < CURRENT_DATE - INTERVAL '30 days'
        ORDER BY vb.bill_date ASC
        LIMIT 1
    """, (org_id,))
    overdue_bill = cursor.fetchone()
    if overdue_bill:
        cursor.execute("SELECT COUNT(*) FROM vendor_bills WHERE organization_id = %s AND status = 'Posted' AND bill_date < CURRENT_DATE - INTERVAL '30 days'", (org_id,))
        count_od_bill = cursor.fetchone()[0]
        payables_alerts.append({
            "category": "Overdue Payables",
            "severity": "Warning",
            "message": f"{count_od_bill} vendor bill(s) unpaid for over 30 days. Oldest: #{overdue_bill[0]} ({overdue_bill[1]}).",
            "link_url": f"/vendors/bills/{overdue_bill[0]}"
        })

    # 3. Negative cash/bank balances
    if cash_bank_balance < 0:
        cash_alerts.append({
            "category": "Negative Treasury",
            "severity": "Critical",
            "message": f"Critical alert: Net cash and bank balance is negative ({currency} {cash_bank_balance:,.2f}).",
            "link_url": "/accounting/chart-of-accounts"
        })

    # 4. Unbalanced or invalid draft journals
    cursor.execute("""
        SELECT jh.journal_header_id, jh.reference
        FROM journal_headers jh
        WHERE jh.organization_id = %s AND jh.status = 'Draft'
          AND (
              SELECT COALESCE(SUM(debit), 0) != COALESCE(SUM(credit), 0)
              FROM journal_lines WHERE journal_header_id = jh.journal_header_id
          )
        ORDER BY jh.accounting_date ASC
        LIMIT 1
    """, (org_id,))
    unbalanced_draft = cursor.fetchone()
    if unbalanced_draft:
        cursor.execute("""
            SELECT COUNT(*) FROM journal_headers jh
            WHERE jh.organization_id = %s AND jh.status = 'Draft'
              AND (SELECT COALESCE(SUM(debit), 0) != COALESCE(SUM(credit), 0) FROM journal_lines WHERE journal_header_id = jh.journal_header_id)
        """, (org_id,))
        count_unbal = cursor.fetchone()[0]
        accounting_alerts.append({
            "category": "Invalid Draft Journals",
            "severity": "Critical",
            "message": f"{count_unbal} draft journal entry(ies) do not balance (debits != credits). Oldest: #{unbalanced_draft[0]}.",
            "link_url": f"/accounting/journal-entries/{unbalanced_draft[0]}"
        })

    # Load saved layout preferences for user
    cursor.execute("""
        SELECT layout_data FROM user_dashboard_preferences
        WHERE user_id = %s AND organization_id = %s
    """, (uid, org_id))
    pref_row = cursor.fetchone()
    saved_layout = pref_row[0] if pref_row else None

    cursor.close()
    conn.close()

    return render_template(
        "dashboard/home.html",
        org_name=org_name,
        currency=currency,
        sales_period=sales_period,
        purchases_period=purchases_period,
        net_profit=net_profit,
        cash_bank_balance=cash_bank_balance,
        receivables_outstanding=receivables_outstanding,
        payables_outstanding=payables_outstanding,
        posted_journal_count=posted_journal_count,
        unposted_voucher_count=unposted_voucher_count,
        is_manager=is_manager,
        user_name=user_name,
        recent_invoices=recent_invoices,
        recent_receipts=recent_receipts,
        recent_bills=recent_bills,
        recent_payments=recent_payments,
        recent_journals=recent_journals,
        recent_transfers=recent_transfers,
        sales_trend=sales_trend,
        purchases_trend=purchases_trend,
        pnl_trend=pnl_trend,
        cash_trend=cash_trend,
        receivables_trend=receivables_trend,
        payables_trend=payables_trend,
        receivables_analysis=receivables_analysis,
        payables_analysis=payables_analysis,
        all_customers=all_customers,
        all_vendors=all_vendors,
        bank_accounts=bank_accounts,
        sales_range=sales_range,
        np_range=np_range,
        cash_range=cash_range,
        ar_range=ar_range,
        ap_range=ap_range,
        sales_grouping=sales_grouping,
        pnl_grouping=pnl_grouping,
        cash_grouping=cash_grouping,
        purchases_grouping=purchases_grouping,
        ar_grouping=ar_grouping,
        ap_grouping=ap_grouping,
        customer_id_filter=customer_id_filter,
        vendor_id_filter=vendor_id_filter,
        status_filter=status_filter,
        receivables_alerts=receivables_alerts,
        payables_alerts=payables_alerts,
        cash_alerts=cash_alerts,
        accounting_alerts=accounting_alerts,
        saved_layout=saved_layout,
    )


@bp.route("/preferences", methods=["POST"])
@require_organization
def save_preferences():
    org_id = current_organization_id()
    uid = current_user_id()
    layout_data = request.json.get("layout_data", "") if request.is_json else request.form.get("layout_data", "")

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_dashboard_preferences (user_id, organization_id, layout_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, organization_id)
        DO UPDATE SET layout_data = EXCLUDED.layout_data
    """, (uid, org_id, layout_data))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})
