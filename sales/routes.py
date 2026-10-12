from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, post_journal_entry, UnbalancedEntryError
from sales.schema import UOM_CHOICES
from core.money import to_decimal, money_round

bp = Blueprint("sales", __name__, url_prefix="/sales")


def _get_account_id_by_code(cursor, org_id, code):
    cursor.execute(
        "SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
        (org_id, code)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _get_revenue_accounts(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND account_type = 'Revenue' AND is_active = TRUE
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return accounts


def _get_active_products(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.product_id, p.name, p.unit, p.default_account_id, coa.name as account_name
        FROM products p
        LEFT JOIN chart_of_accounts coa ON coa.account_id = p.default_account_id
        WHERE p.organization_id = %s AND p.is_active = TRUE
        ORDER BY p.name
    """, (org_id,))
    products = cursor.fetchall()
    cursor.close()
    conn.close()
    return products


def _get_active_sales_tax_rates(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_tax_rate_id, label, rate_percent FROM sales_tax_rates
        WHERE organization_id = %s AND is_active = TRUE
        ORDER BY rate_percent DESC
    """, (org_id,))
    rates = cursor.fetchall()
    cursor.close()
    conn.close()
    return rates


def _get_active_customers(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customer_id, name, nature_of_supply FROM customers
        WHERE organization_id = %s AND is_active = TRUE
        ORDER BY name
    """, (org_id,))
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    return customers


# ---------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------
@bp.route("/products")
@require_organization
def products_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.product_id, p.name, p.unit, coa.code, coa.name, p.is_active
        FROM products p
        LEFT JOIN chart_of_accounts coa ON coa.account_id = p.default_account_id
        WHERE p.organization_id = %s
        ORDER BY p.name
    """, (org_id,))
    products = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("sales/products_list.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@require_organization
def new_product():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        default_acc = request.form.get("default_account_id") or None
        if default_acc:
            cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (int(default_acc), org_id))
            if not cursor.fetchone():
                conn.rollback()
                cursor.close()
                conn.close()
                abort(400)
        else:
            default_acc = _get_account_id_by_code(cursor, org_id, "4000")

        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, default_account_id)
            VALUES (%s, %s, %s, %s)
        """, (
            org_id,
            request.form["name"].strip(),
            request.form.get("unit", "Units"),
            default_acc,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("sales.products_list"))

    return render_template(
        "sales/new_product.html",
        uom_choices=UOM_CHOICES,
        accounts=_get_revenue_accounts(org_id),
    )


# ---------------------------------------------------------------------
# Sales Tax Rates Master
# ---------------------------------------------------------------------
@bp.route("/tax-rates")
@require_organization
def sales_tax_rates_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_tax_rate_id, label, rate_percent, is_active
        FROM sales_tax_rates
        WHERE organization_id = %s
        ORDER BY rate_percent DESC
    """, (org_id,))
    rates = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("sales/sales_tax_rates_list.html", rates=rates)


@bp.route("/tax-rates/new", methods=["GET", "POST"])
@require_organization
def new_sales_tax_rate():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_tax_rates (organization_id, label, rate_percent)
            VALUES (%s, %s, %s)
        """, (
            org_id,
            request.form["label"].strip(),
            to_decimal(request.form["rate_percent"]),
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("sales.sales_tax_rates_list"))

    return render_template("sales/new_sales_tax_rate.html")


@bp.route("/tax-rates/<int:tax_rate_id>/toggle", methods=["POST"])
@require_organization
def toggle_sales_tax_rate(tax_rate_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM sales_tax_rates WHERE sales_tax_rate_id = %s AND organization_id = %s",
        (tax_rate_id, org_id)
    )
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        UPDATE sales_tax_rates SET is_active = NOT is_active
        WHERE sales_tax_rate_id = %s AND organization_id = %s
    """, (tax_rate_id, org_id))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for("sales.sales_tax_rates_list"))


# ---------------------------------------------------------------------
# Sales Invoices
# ---------------------------------------------------------------------
@bp.route("/invoices")
@require_organization
def sales_invoices_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT si.sales_invoice_id, c.name, si.invoice_date, si.due_date, si.status,
               COALESCE(SUM(sil.total), 0) AS total
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        LEFT JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s
        GROUP BY si.sales_invoice_id, c.name
        ORDER BY si.invoice_date DESC, si.sales_invoice_id DESC
    """, (org_id,))
    invoices = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("sales/sales_invoices_list.html", invoices=invoices)


@bp.route("/invoices/new", methods=["GET", "POST"])
@require_organization
def new_sales_invoice():
    org_id = current_organization_id()
    if request.method == "POST":
        customer_id = int(request.form["customer_id"])
        product_ids = request.form.getlist("product_id[]")
        labels = request.form.getlist("label[]")
        account_ids = request.form.getlist("account_id[]")
        units = request.form.getlist("unit[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discount_percents = request.form.getlist("discount_percent[]")
        tax_rate_ids = request.form.getlist("sales_tax_rate_id[]")

        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM customers WHERE customer_id = %s AND organization_id = %s", (customer_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        for p_id in product_ids:
            if p_id:
                cursor.execute("SELECT 1 FROM products WHERE product_id = %s AND organization_id = %s", (int(p_id), org_id))
                if not cursor.fetchone():
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    abort(400)
        for a_id in account_ids:
            if a_id:
                cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (int(a_id), org_id))
                if not cursor.fetchone():
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    abort(400)
        for t_id in tax_rate_ids:
            if t_id:
                cursor.execute("SELECT 1 FROM sales_tax_rates WHERE sales_tax_rate_id = %s AND organization_id = %s", (int(t_id), org_id))
                if not cursor.fetchone():
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    abort(400)

        cursor.execute("""
            INSERT INTO sales_invoices
                (organization_id, customer_id, invoice_date, due_date, payment_terms, journal_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING sales_invoice_id
        """, (
            org_id,
            customer_id,
            request.form["invoice_date"],
            request.form.get("due_date") or None,
            request.form.get("payment_terms", "").strip() or None,
            request.form.get("journal_name", "Customer Invoices").strip(),
        ))
        sales_invoice_id = cursor.fetchone()[0]

        # Fetch tax rates for lookups
        cursor.execute("SELECT sales_tax_rate_id, rate_percent FROM sales_tax_rates WHERE organization_id = %s", (org_id,))
        tax_map = dict(cursor.fetchall())

        net_revenue_by_account = {}
        total_tax = to_decimal(0)
        invoice_grand_total = to_decimal(0)

        for i in range(len(product_ids)):
            if not product_ids[i]:
                continue

            pid = int(product_ids[i])
            qty = to_decimal(quantities[i] or 1)
            rate = to_decimal(rates[i] or 0)
            disc_pct = to_decimal(discount_percents[i] or 0)
            gross = qty * rate
            disc_amt = money_round(gross * (disc_pct / Decimal("100")))
            net = money_round(gross - disc_amt)

            tax_id = int(tax_rate_ids[i]) if tax_rate_ids[i] else None
            tax_pct = to_decimal(tax_map.get(tax_id, 0)) if tax_id else to_decimal(0)
            tax_amt = money_round(net * (tax_pct / Decimal("100")))
            line_tot = money_round(net + tax_amt)

            acc_id = int(account_ids[i])
            net_revenue_by_account[acc_id] = net_revenue_by_account.get(acc_id, to_decimal(0)) + net
            total_tax += tax_amt
            invoice_grand_total += line_tot

            cursor.execute("""
                INSERT INTO sales_invoice_lines
                    (sales_invoice_id, product_id, label, account_id, unit,
                     quantity, rate, discount_percent, discount_amount,
                     sales_tax_rate_id, tax_amount, total, line_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                sales_invoice_id, pid, labels[i].strip() or None, acc_id,
                units[i], qty, rate, disc_pct, disc_amt,
                tax_id, tax_amt, line_tot, i,
            ))

        cursor.execute("SELECT name FROM customers WHERE customer_id = %s", (customer_id,))
        customer_name = cursor.fetchone()[0]
        trade_receivables_id = _get_account_id_by_code(cursor, org_id, "1100")
        output_tax_id = _get_account_id_by_code(cursor, org_id, "2200")

        # Double-entry posting setup:
        # Dr Trade Receivables (Grand Total)
        # Cr Sales Revenue per account (Net of discount)
        # Cr Output Tax Payable (Sales Tax)
        je_lines = [{
            "account_id": trade_receivables_id,
            "partner_name": customer_name,
            "label": f"Customer Invoice - {customer_name}",
            "debit": invoice_grand_total,
            "credit": 0,
        }]

        for acc_id, rev_amt in net_revenue_by_account.items():
            if rev_amt > 0:
                je_lines.append({
                    "account_id": acc_id,
                    "partner_name": customer_name,
                    "label": f"Sales - {customer_name}",
                    "debit": 0,
                    "credit": rev_amt,
                })

        if total_tax > 0:
            je_lines.append({
                "account_id": output_tax_id,
                "partner_name": customer_name,
                "label": f"Output Sales Tax - {customer_name}",
                "debit": 0,
                "credit": total_tax,
            })

        try:
            journal_header_id = create_journal_entry(
                cursor,
                organization_id=org_id,
                reference=f"Invoice - {customer_name}",
                accounting_date=request.form["invoice_date"],
                journal_name=request.form.get("journal_name", "Customer Invoices").strip(),
                lines=je_lines,
                source_type="SalesInvoice",
                source_id=sales_invoice_id,
                auto_post=False,
            )
            cursor.execute(
                "UPDATE sales_invoices SET journal_header_id = %s WHERE sales_invoice_id = %s",
                (journal_header_id, sales_invoice_id)
            )
            conn.commit()
        except UnbalancedEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "sales/new_sales_invoice.html",
                customers=_get_active_customers(org_id),
                products=_get_active_products(org_id),
                accounts=_get_revenue_accounts(org_id),
                tax_rates=_get_active_sales_tax_rates(org_id),
                uom_choices=UOM_CHOICES,
                error=str(error),
                today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

    return render_template(
        "sales/new_sales_invoice.html",
        customers=_get_active_customers(org_id),
        products=_get_active_products(org_id),
        accounts=_get_revenue_accounts(org_id),
        tax_rates=_get_active_sales_tax_rates(org_id),
        uom_choices=UOM_CHOICES,
        error=None,
        today=date.today().isoformat(),
    )


@bp.route("/invoices/<int:sales_invoice_id>")
@require_organization
def sales_invoice_detail(sales_invoice_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT si.sales_invoice_id, c.name, si.invoice_date, si.due_date,
               si.status, si.journal_header_id, si.payment_terms
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        WHERE si.sales_invoice_id = %s AND si.organization_id = %s
    """, (sales_invoice_id, org_id))
    invoice = cursor.fetchone()
    if not invoice:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT p.name, sil.label, coa.code, coa.name, sil.unit,
               sil.quantity, sil.rate, sil.discount_percent, sil.discount_amount,
               str.label, sil.tax_amount, sil.total
        FROM sales_invoice_lines sil
        JOIN products p ON p.product_id = sil.product_id
        JOIN chart_of_accounts coa ON coa.account_id = sil.account_id
        LEFT JOIN sales_tax_rates str ON str.sales_tax_rate_id = sil.sales_tax_rate_id
        WHERE sil.sales_invoice_id = %s
        ORDER BY sil.line_order
    """, (sales_invoice_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total_gross = sum((to_decimal(l[5]) * to_decimal(l[6]) for l in lines), to_decimal(0))
    total_discount = sum((to_decimal(l[8]) for l in lines), to_decimal(0))
    total_tax = sum((to_decimal(l[10]) for l in lines), to_decimal(0))
    grand_total = sum((to_decimal(l[11]) for l in lines), to_decimal(0))

    return render_template(
        "sales/sales_invoice_detail.html",
        invoice=invoice,
        lines=lines,
        total_gross=total_gross,
        total_discount=total_discount,
        total_tax=total_tax,
        grand_total=grand_total,
    )


@bp.route("/invoices/<int:sales_invoice_id>/post", methods=["POST"])
@require_organization
def post_sales_invoice(sales_invoice_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM sales_invoices WHERE sales_invoice_id = %s AND organization_id = %s", (sales_invoice_id, org_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        abort(404)
    journal_header_id = row[0]
    try:
        post_journal_entry(cursor, journal_header_id)
        cursor.execute("UPDATE sales_invoices SET status = 'Posted' WHERE sales_invoice_id = %s", (sales_invoice_id,))
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))