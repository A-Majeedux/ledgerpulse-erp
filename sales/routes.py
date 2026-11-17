from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash
from core.db import get_conn
from core.context import current_organization_id, require_organization
from core.docmenu import build_menu
from core.searchbar import read_search, search_filters, and_clauses, describe, suggest
from auth.services import require_permission, current_user_id
from accounting.services import create_journal_entry, post_journal_entry, UnbalancedEntryError
from sales.schema import UOM_CHOICES
from sales.services import (clean_date, delete_draft_invoice, duplicate_invoice,
                            give_invoice_a_number, replace_invoice, reset_invoice_to_draft)
from core.money import to_decimal, money_round
from customers.services import credit_status, format_amount

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
    """The products screen as cards, with the search box on top and the two
    figures his own screen shows on every card: Price and On hand."""
    org_id = current_organization_id()
    q = (request.args.get("q") or "").strip()
    conn = get_conn()
    cursor = conn.cursor()
    sql = """
        SELECT p.product_id, p.name, p.unit, coa.code, coa.name, p.is_active,
               COALESCE(p.sales_price, 0), COALESCE(p.on_hand, 0), p.last_landed_cost
        FROM products p
        LEFT JOIN chart_of_accounts coa ON coa.account_id = p.default_account_id
        WHERE p.organization_id = %s
    """
    params = [org_id]
    if q:
        sql += """ AND (p.name ILIKE %s OR p.unit ILIKE %s
                        OR coa.code ILIKE %s OR coa.name ILIKE %s)"""
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY p.name"
    cursor.execute(sql, tuple(params))
    products = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM products WHERE organization_id = %s", (org_id,))
    total_products = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return render_template(
        "sales/products_list.html",
        products=products,
        q=q,
        total_products=total_products,
    )


def _product_price_and_stock(form):
    """Read the Price and On hand boxes. A figure that is not a number is
    refused before anything is written, with a message saying which one."""
    from decimal import InvalidOperation
    values = {}
    for field, label in (("sales_price", "Price"), ("on_hand", "On hand")):
        raw = (form.get(field) or "").strip()
        if raw == "":
            values[field] = Decimal("0")
            continue
        try:
            number = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label} must be a number (for example 1250.50).")
        if number < 0:
            raise ValueError(f"{label} cannot be less than zero.")
        values[field] = number
    return values


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@require_organization
def edit_product(product_id):
    """Price, On hand, name, unit, revenue account and Active - all of it on
    the product itself. Nothing about an invoice already written changes."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT product_id, name, unit, default_account_id, is_active,
               COALESCE(sales_price, 0), COALESCE(on_hand, 0)
        FROM products
        WHERE product_id = %s AND organization_id = %s
    """, (product_id, org_id))
    product = cursor.fetchone()
    if not product:
        cursor.close()
        conn.close()
        abort(404)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            cursor.close()
            conn.close()
            return render_template(
                "sales/edit_product.html",
                product=product,
                uom_choices=UOM_CHOICES,
                accounts=_get_revenue_accounts(org_id),
                error="The product needs a name.",
            )
        try:
            figures = _product_price_and_stock(request.form)
        except ValueError as error:
            cursor.close()
            conn.close()
            return render_template(
                "sales/edit_product.html",
                product=product,
                uom_choices=UOM_CHOICES,
                accounts=_get_revenue_accounts(org_id),
                error=str(error),
            )
        default_acc = request.form.get("default_account_id") or None
        if default_acc:
            cursor.execute(
                "SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s",
                (int(default_acc), org_id))
            if not cursor.fetchone():
                cursor.close()
                conn.close()
                abort(400)
        cursor.execute("""
            UPDATE products
            SET name = %s, unit = %s, default_account_id = %s,
                sales_price = %s, on_hand = %s, is_active = %s
            WHERE product_id = %s AND organization_id = %s
        """, (
            name,
            request.form.get("unit", "Units"),
            int(default_acc) if default_acc else None,
            figures["sales_price"],
            figures["on_hand"],
            request.form.get("is_active") == "on",
            product_id,
            org_id,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("sales.products_list"))

    cursor.close()
    conn.close()
    return render_template(
        "sales/edit_product.html",
        product=product,
        uom_choices=UOM_CHOICES,
        accounts=_get_revenue_accounts(org_id),
        error=None,
    )


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

        try:
            figures = _product_price_and_stock(request.form)
        except ValueError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "sales/new_product.html",
                uom_choices=UOM_CHOICES,
                accounts=_get_revenue_accounts(org_id),
                error=str(error),
            )

        cursor.execute("""
            INSERT INTO products (organization_id, name, unit, default_account_id,
                                  sales_price, on_hand)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["name"].strip(),
            request.form.get("unit", "Units"),
            default_acc,
            figures["sales_price"],
            figures["on_hand"],
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("sales.products_list"))

    return render_template(
        "sales/new_product.html",
        uom_choices=UOM_CHOICES,
        accounts=_get_revenue_accounts(org_id),
        error=None,
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
    # The search bar: a number, a customer, a label on a line, the letter of
    # credit reference, a status, a month, or a range of dates.
    search = read_search(request)
    clauses, params = search_filters(
        search,
        text_columns=["si.invoice_no", "si.lc_reference", "c.name", "sil.label"],
        status_column="si.status",
        date_column="si.invoice_date",
        partner_column="c.name",
        account_column="sil.account_id",
    )
    # Accounts are needed twice: to filter by expense head, and to name the head
    # the list is filtered to in the sentence under the bar.
    accounts = _filter_accounts(cursor, org_id)
    search.description = describe(search, accounts)
    # Suggested from what is already in the books: the numbers, the customers and
    # the labels this company has used.
    suggestions = {
        "q": suggest(cursor, [
            ("SELECT invoice_no FROM sales_invoices WHERE organization_id = %s AND invoice_no IS NOT NULL "
             "ORDER BY sales_invoice_id DESC LIMIT 40", (org_id,)),
            ("SELECT name FROM customers WHERE organization_id = %s ORDER BY customer_id DESC LIMIT 40", (org_id,)),
            ("SELECT DISTINCT sil.label FROM sales_invoice_lines sil "
             "JOIN sales_invoices si ON si.sales_invoice_id = sil.sales_invoice_id "
             "WHERE si.organization_id = %s AND sil.label IS NOT NULL LIMIT 40", (org_id,)),
        ]),
        "partner": suggest(cursor, [
            ("SELECT name FROM customers WHERE organization_id = %s ORDER BY name LIMIT 60", (org_id,)),
        ]),
    }
    cursor.execute("""
        SELECT si.sales_invoice_id, c.name, si.invoice_date, si.due_date, si.status,
               COALESCE(SUM(sil.total), 0) AS total, si.invoice_no, si.lc_reference,
               COALESCE(si.paid_amount, 0) AS paid
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        LEFT JOIN sales_invoice_lines sil ON sil.sales_invoice_id = si.sales_invoice_id
        WHERE si.organization_id = %s
    """ + and_clauses(clauses) + """
        GROUP BY si.sales_invoice_id, c.name, si.invoice_no, si.lc_reference, si.paid_amount
        ORDER BY si.invoice_date DESC, si.sales_invoice_id DESC
    """, [org_id] + params)
    invoices = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("sales/sales_invoices_list.html", invoices=invoices,
                           search=search, accounts=accounts, suggestions=suggestions)


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

        # The Delivery Date box. A date that is not a date stops the invoice
        # before it is saved, and the message says what to correct.
        # (A letter of credit is a purchase reference - imports - so a sales
        # invoice neither asks for one nor stores one. The column stays in the
        # table so invoices entered earlier keep their history.)
        try:
            delivery_date = clean_date(request.form.get("delivery_date"))
        except ValueError as error:
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

        # The due date the customer was given. If nobody typed one, his card's
        # payment terms fill it in here as well as on the screen, so an invoice
        # saved with the warning turned off in the browser still has a due date.
        due_date = request.form.get("due_date") or None
        payment_terms = request.form.get("payment_terms", "").strip() or None
        if due_date is None:
            cursor.execute("SELECT payment_terms_days FROM customers WHERE customer_id = %s AND organization_id = %s",
                           (customer_id, org_id))
            terms_row = cursor.fetchone()
            days = terms_row[0] if terms_row else None
            if days:
                due_date = (date.fromisoformat(request.form["invoice_date"]) + timedelta(days=int(days))).isoformat()
                payment_terms = payment_terms or f"{int(days)} days"

        cursor.execute("""
            INSERT INTO sales_invoices
                (organization_id, customer_id, invoice_date, due_date, payment_terms, journal_name,
                 delivery_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING sales_invoice_id
        """, (
            org_id,
            customer_id,
            request.form["invoice_date"],
            due_date,
            payment_terms,
            request.form.get("journal_name", "Customer Invoices").strip(),
            delivery_date,
        ))
        sales_invoice_id = cursor.fetchone()[0]
        # The invoice's own number: given here, and never changed afterwards
        # (sales/services.py).
        invoice_no = give_invoice_a_number(cursor, org_id, sales_invoice_id, request.form["invoice_date"])

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
            "label": f"Customer Invoice {invoice_no} - {customer_name}",
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
                reference=f"Invoice {invoice_no} - {customer_name}",
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


def _invoice_trail(org_id, customer_name, customer_id, journal_header_id, invoice_no, receipts_applied):
    """The chips under an invoice's title: who it is for, its journal entry, and
    every receipt that has been matched to it."""
    chips = [{"icon": "i-customers", "label": customer_name, "meta": "Customer",
              "url": url_for("customers.customer_view", customer_id=customer_id)},
             {"icon": "i-ledger", "label": "Statement", "meta": "account",
              "url": url_for("customers.customer_statement", customer_id=customer_id)}]
    if journal_header_id:
        chips.append({"icon": "i-journal", "label": "Journal entry", "meta": "#%s" % journal_header_id,
                      "url": url_for("accounting.journal_entry_detail",
                                     journal_header_id=journal_header_id)})
    for receipt in receipts_applied:
        chips.append({"icon": "i-receipt", "label": receipt["voucher_no"] or ("Receipt #%s" % receipt["id"]),
                      "meta": "receipt · {:,.2f}".format(to_decimal(receipt["amount"] or 0)),
                      "url": url_for("customers.customer_receipt_detail",
                                     customer_receipt_id=receipt["id"])})
    return chips


def _filter_accounts(cursor, org_id):
    """The accounts a list may be filtered to: (id, 'code · name')."""
    cursor.execute("""
        SELECT account_id, code || ' · ' || name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE ORDER BY code
    """, (org_id,))
    return cursor.fetchall()


@bp.route("/invoices/<int:sales_invoice_id>")
@require_organization
def sales_invoice_detail(sales_invoice_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT si.sales_invoice_id, c.name, si.invoice_date, si.due_date,
               si.status, si.journal_header_id, si.payment_terms,
               si.invoice_no, si.lc_reference, si.delivery_date, c.ntn, c.address, c.city,
               si.journal_name, header.accounting_date, COALESCE(si.paid_amount, 0)
        FROM sales_invoices si
        JOIN customers c ON c.customer_id = si.customer_id
        LEFT JOIN journal_headers header ON header.journal_header_id = si.journal_header_id
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

    # What the customer receipts have already been applied to this invoice
    # (customers/services.py keeps the allocation rows; paid_amount is their
    # total on the invoice itself).
    paid_amount = money_round(to_decimal(invoice[15] or 0))
    still_due = money_round(grand_total - paid_amount)

    # On a draft, what the credit limit says about it - the page then offers the
    # "save anyway" tick instead of a Post button that quietly does nothing.
    credit = None
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT customer_id, invoice_no, journal_header_id FROM sales_invoices "
                       "WHERE sales_invoice_id = %s AND organization_id = %s", (sales_invoice_id, org_id))
        customer_id, invoice_no, journal_header_id = cursor.fetchone()
        if invoice[4] != "Posted":
            credit = credit_status(cursor, org_id, customer_id, extra=grand_total)

        # The money already matched to this invoice, each one a link to the
        # receipt itself - so "which receipt paid this?" is one click, not a
        # walk through a list.
        cursor.execute("""
            SELECT receipt.customer_receipt_id, receipt.voucher_no, receipt.receipt_date,
                   allocation.allocated_amount
            FROM customer_receipt_allocations allocation
            JOIN customer_receipts receipt ON receipt.customer_receipt_id = allocation.customer_receipt_id
            WHERE allocation.sales_invoice_id = %s
            ORDER BY receipt.receipt_date, receipt.customer_receipt_id
        """, (sales_invoice_id,))
        receipts_applied = [{"id": row[0], "voucher_no": row[1], "date": row[2], "amount": row[3]}
                            for row in cursor.fetchall()]

        # And the receipts of this customer that still have money unapplied, so
        # one of them can be matched to this invoice from here.
        cursor.execute("""
            SELECT receipt.customer_receipt_id, receipt.voucher_no, receipt.receipt_date, receipt.amount,
                   COALESCE((SELECT SUM(a.allocated_amount) FROM customer_receipt_allocations a
                             WHERE a.customer_receipt_id = receipt.customer_receipt_id), 0) AS applied
            FROM customer_receipts receipt
            WHERE receipt.organization_id = %s AND receipt.customer_id = %s
            ORDER BY receipt.receipt_date, receipt.customer_receipt_id
        """, (org_id, customer_id))
        unapplied_receipts = []
        for row in cursor.fetchall():
            left = money_round(to_decimal(row[3] or 0) - to_decimal(row[4] or 0))
            if left > 0:
                unapplied_receipts.append({"id": row[0], "voucher_no": row[1], "date": row[2], "left": left})
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "sales/sales_invoice_detail.html",
        invoice=invoice,
        lines=lines,
        total_gross=total_gross,
        total_discount=total_discount,
        total_tax=total_tax,
        grand_total=grand_total,
        paid_amount=paid_amount,
        still_due=still_due,
        credit=credit,
        over_limit=(credit["over"] if credit else False),
        refused_over_limit=bool(request.args.get("over_limit")),
        receipts_applied=receipts_applied,
        unapplied_receipts=unapplied_receipts,
        menu=build_menu("sales_invoice", sales_invoice_id, status=invoice[4],
                        user_id=current_user_id(), organization_id=org_id),
        trail=_invoice_trail(org_id, invoice[1], customer_id, journal_header_id, invoice_no, receipts_applied),
    )


@bp.route("/invoices/<int:sales_invoice_id>/apply-receipt", methods=["POST"])
@require_organization
@require_permission("sales.create")
def apply_receipt_to_invoice(sales_invoice_id):
    """Match an existing receipt of this customer to this invoice.

    The same check the receipt screen makes runs here (customers.services), so
    an amount bigger than the invoice still owes, or bigger than the receipt has
    left, is refused with the reason instead of saved.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT customer_id, status FROM sales_invoices "
                       "WHERE sales_invoice_id = %s AND organization_id = %s", (sales_invoice_id, org_id))
        row = cursor.fetchone()
        if not row:
            abort(404)
        customer_id, status = row
        if (status or "").lower() != "posted":
            flash("A draft invoice cannot take money yet - post it first.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))
        try:
            receipt_id = int(request.form.get("customer_receipt_id") or 0)
            amount = to_decimal(request.form.get("amount") or 0)
        except (TypeError, ValueError):
            flash("Choose a receipt and type an amount.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))
        if not receipt_id or amount <= 0:
            flash("Choose a receipt and type an amount bigger than zero.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

        cursor.execute("SELECT customer_id, amount, voucher_no FROM customer_receipts "
                       "WHERE customer_receipt_id = %s AND organization_id = %s", (receipt_id, org_id))
        receipt = cursor.fetchone()
        if not receipt:
            abort(404)
        if int(receipt[0]) != int(customer_id):
            flash("That receipt belongs to another customer.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

        from customers.services import apply_receipt_to_invoices
        try:
            apply_receipt_to_invoices(cursor, org_id, receipt_id, customer_id, receipt[1],
                                      [(sales_invoice_id, amount)])
        except ValueError as error:
            conn.rollback()
            flash(str(error), "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))
        conn.commit()
        flash(f"{format_amount(amount)} of {(receipt[2] or 'the receipt')} is now matched to this invoice.", "success")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))


@bp.route("/invoices/<int:sales_invoice_id>/edit", methods=["GET", "POST"])
@require_organization
@require_permission("sales.create")
def edit_sales_invoice(sales_invoice_id):
    """Correct a draft invoice, in the same form that writes a new one.

    The invoice keeps its number; its lines, its wording and its draft journal
    entry are written again from what is on the form.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT invoice_no, status FROM sales_invoices "
                       "WHERE sales_invoice_id = %s AND organization_id = %s", (sales_invoice_id, org_id))
        found = cursor.fetchone()
        if not found:
            abort(404)
        invoice_no, status = found

        def page(**kwargs):
            return render_template(
                "sales/new_sales_invoice.html",
                customers=_get_active_customers(org_id),
                products=_get_active_products(org_id),
                accounts=_get_revenue_accounts(org_id),
                tax_rates=_get_active_sales_tax_rates(org_id),
                uom_choices=UOM_CHOICES, today=date.today().isoformat(),
                draft={"id": sales_invoice_id, "invoice_no": invoice_no}, **kwargs)

        # Only a draft is corrected here; a posted invoice is reset to a draft
        # first, on purpose, from its own page.
        if (status or "").lower() != "draft":
            flash(f"{invoice_no or 'That invoice'} is posted. Reset it to a draft before changing it.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))
        cursor.execute("SELECT COUNT(*) FROM customer_receipt_allocations WHERE sales_invoice_id = %s",
                       (sales_invoice_id,))
        if cursor.fetchone()[0]:
            flash("A receipt is matched to this invoice, so it cannot be changed. Unmatch it first.", "error")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

        if request.method == "POST":
            product_ids = request.form.getlist("product_id[]")
            labels = request.form.getlist("label[]")
            account_ids = request.form.getlist("account_id[]")
            units = request.form.getlist("unit[]")
            quantities = request.form.getlist("quantity[]")
            rates = request.form.getlist("rate[]")
            discounts = request.form.getlist("discount_percent[]")
            tax_ids = request.form.getlist("sales_tax_rate_id[]")

            lines = []
            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue
                lines.append({
                    "product_id": int(product_id),
                    "label": (labels[index] or "").strip() or None if index < len(labels) else None,
                    "account_id": int(account_ids[index]),
                    "unit": units[index] if index < len(units) else None,
                    "quantity": quantities[index] if index < len(quantities) else 1,
                    "rate": rates[index] if index < len(rates) else 0,
                    "discount_percent": discounts[index] if index < len(discounts) else 0,
                    "sales_tax_rate_id": int(tax_ids[index]) if index < len(tax_ids) and tax_ids[index] else None,
                })
            if not lines:
                return page(error="An invoice needs at least one line.")

            try:
                delivery_date = clean_date(request.form.get("delivery_date"))
            except ValueError as error:
                return page(error=str(error))

            due_date = request.form.get("due_date") or None
            payment_terms = request.form.get("payment_terms", "").strip() or None
            if due_date is None:
                cursor.execute("SELECT payment_terms_days FROM customers "
                               "WHERE customer_id = %s AND organization_id = %s",
                               (int(request.form["customer_id"]), org_id))
                terms_row = cursor.fetchone()
                days = terms_row[0] if terms_row else None
                if days:
                    due_date = (date.fromisoformat(request.form["invoice_date"])
                                + timedelta(days=int(days))).isoformat()
                    payment_terms = payment_terms or f"{int(days)} days"

            try:
                replace_invoice(cursor, org_id, sales_invoice_id, header={
                    "customer_id": int(request.form["customer_id"]),
                    "invoice_date": request.form["invoice_date"],
                    "due_date": due_date,
                    "payment_terms": payment_terms,
                    "journal_name": request.form.get("journal_name", "Customer Invoices").strip(),
                    "delivery_date": delivery_date,
                }, raw_lines=lines)
                conn.commit()
            except (UnbalancedEntryError, ValueError) as error:
                conn.rollback()
                return page(error=str(error))
            flash(f"{invoice_no} is corrected. Post it when you are ready.", "success")
            return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

        # ---- the form, filled with what the invoice says today --------------
        cursor.execute("""
            SELECT customer_id, invoice_date, due_date, payment_terms, journal_name, delivery_date
            FROM sales_invoices WHERE sales_invoice_id = %s
        """, (sales_invoice_id,))
        customer_id, invoice_date, due_date, payment_terms, journal_name, delivery_date = cursor.fetchone()
        cursor.execute("""
            SELECT product_id, label, account_id, unit, quantity, rate, discount_percent,
                   sales_tax_rate_id
            FROM sales_invoice_lines WHERE sales_invoice_id = %s ORDER BY line_order
        """, (sales_invoice_id,))
        draft_lines = [{
            "product_id": row[0], "label": row[1] or "", "account_id": row[2], "unit": row[3],
            "quantity": str(row[4]), "rate": str(row[5]), "discount_percent": str(row[6] or 0),
            "sales_tax_rate_id": row[7],
        } for row in cursor.fetchall()]
        header = {
            "customer_id": customer_id,
            "invoice_date": invoice_date.isoformat() if invoice_date else "",
            "due_date": due_date.isoformat() if due_date else "",
            "payment_terms": payment_terms or "",
            "journal_name": journal_name or "Customer Invoices",
            "delivery_date": delivery_date.isoformat() if delivery_date else "",
        }
        return page(draft_header=header, draft_lines=draft_lines)
    finally:
        cursor.close()
        conn.close()


@bp.route("/invoices/<int:sales_invoice_id>/reset-to-draft", methods=["POST"])
@require_organization
@require_permission("sales.post")
def reset_invoice_to_draft_route(sales_invoice_id):
    """Take a posted invoice back to a draft so it can be corrected.

    Its journal entry goes back to Draft with it, so the books stop carrying an
    invoice that is about to change. Refused while a receipt is matched to it.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        invoice_no = reset_invoice_to_draft(cursor, org_id, sales_invoice_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        flash(str(error), "error")
    else:
        flash(f"{invoice_no or 'The invoice'} is a draft again. Correct it, then post it.", "success")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))


@bp.route("/invoices/<int:sales_invoice_id>/duplicate", methods=["POST"])
@require_organization
@require_permission("sales.create")
def duplicate_sales_invoice(sales_invoice_id):
    """Copy an invoice into a new draft (the gear menu on the invoice page)."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        new_id, new_number = duplicate_invoice(cursor, org_id, sales_invoice_id)
    except ValueError as error:
        conn.rollback()
        abort(400, description=str(error))
    conn.commit()
    cursor.close()
    conn.close()
    flash(f"{new_number} is a copy of the invoice, saved as a draft. Change what you need, then post it.",
          "success")
    return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=new_id))


@bp.route("/invoices/<int:sales_invoice_id>/delete", methods=["POST"])
@require_organization
@require_permission("sales.create")
def delete_sales_invoice(sales_invoice_id):
    """Delete a DRAFT invoice. A posted one is refused with the reason."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        number = delete_draft_invoice(cursor, org_id, sales_invoice_id)
    except ValueError as error:
        conn.rollback()
        cursor.close()
        conn.close()
        flash(str(error), "error")
        return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash(f"{number or 'The draft invoice'} was deleted.", "success")
    return redirect(url_for("sales.sales_invoices_list"))


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

    # Credit control. An invoice that would take the customer past the limit he
    # was given is NOT posted until someone says so on the invoice itself
    # ("save anyway"). A draft is always allowed - it changes no balance.
    override = str(request.form.get("override_credit_limit") or "").strip().lower() in ("1", "on", "true", "yes")
    cursor.execute("SELECT customer_id FROM sales_invoices WHERE sales_invoice_id = %s AND organization_id = %s",
                   (sales_invoice_id, org_id))
    customer_id = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM sales_invoice_lines WHERE sales_invoice_id = %s",
                   (sales_invoice_id,))
    invoice_total = to_decimal(cursor.fetchone()[0] or 0)
    credit = credit_status(cursor, org_id, customer_id, extra=invoice_total)
    if credit["over"] and not override:
        cursor.close()
        conn.close()
        return redirect(url_for("sales.sales_invoice_detail",
                                sales_invoice_id=sales_invoice_id, over_limit=1))

    try:
        post_journal_entry(cursor, journal_header_id)
        cursor.execute("UPDATE sales_invoices SET status = 'Posted' WHERE sales_invoice_id = %s", (sales_invoice_id,))
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("sales.sales_invoice_detail", sales_invoice_id=sales_invoice_id))

# ---------------------------------------------------------------------
# The print page of one sales invoice
#
# The same frame as the vendor bill print (templates/vendors/print/
# _print_base.html), so both kinds of document come out of the printer
# looking like the same paperwork.
# ---------------------------------------------------------------------

@bp.route("/invoices/<int:sales_invoice_id>/print")
@require_organization
def sales_invoice_print(sales_invoice_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT si.sales_invoice_id, si.invoice_no, c.name, c.address, c.city, c.ntn,
               si.invoice_date, si.due_date, si.delivery_date, si.status, si.payment_terms,
               si.lc_reference, si.journal_name
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
        SELECT p.name, sil.label, coa.code, coa.name, sil.unit, sil.quantity, sil.rate,
               sil.discount_percent, sil.discount_amount, str.label, sil.tax_amount, sil.total
        FROM sales_invoice_lines sil
        LEFT JOIN products p ON p.product_id = sil.product_id
        LEFT JOIN chart_of_accounts coa ON coa.account_id = sil.account_id
        LEFT JOIN sales_tax_rates str ON str.sales_tax_rate_id = sil.sales_tax_rate_id
        WHERE sil.sales_invoice_id = %s
        ORDER BY sil.line_order, sil.sales_invoice_line_id
    """, (sales_invoice_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total_gross = sum((to_decimal(line[5]) * to_decimal(line[6]) for line in lines), to_decimal(0))
    total_discount = sum((to_decimal(line[8]) for line in lines), to_decimal(0))
    total_tax = sum((to_decimal(line[10]) for line in lines), to_decimal(0))
    grand_total = sum((to_decimal(line[11]) for line in lines), to_decimal(0))

    return render_template(
        "sales/print/sales_invoice_print.html",
        invoice=invoice,
        lines=lines,
        total_gross=total_gross,
        total_discount=total_discount,
        total_tax=total_tax,
        grand_total=grand_total,
    )
