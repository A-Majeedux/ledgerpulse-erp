from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, UnbalancedEntryError
from wht.routes import get_active_tax_rates
from core.money import to_decimal, money_round

bp = Blueprint("customers", __name__, url_prefix="/customers")

NATURE_OF_SUPPLY_OPTIONS = ["Supplies", "Services"]


def _get_account_id_by_code(cursor, org_id, code):
    cursor.execute(
        "SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
        (org_id, code)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _get_accounts(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return accounts


def _get_customers(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customer_id, name, nature_of_supply FROM customers
        WHERE organization_id = %s AND is_active = TRUE ORDER BY name
    """, (org_id,))
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    return customers


# ---------------------------------------------------------------------
# Customer master
# ---------------------------------------------------------------------

@bp.route("/")
@require_organization
def customers_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customer_id, name, nature_of_supply, ntn, strn, credit_limit
        FROM customers WHERE organization_id = %s ORDER BY name
    """, (org_id,))
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("customers/customers_list.html", customers=customers)


@bp.route("/new", methods=["GET", "POST"])
@require_organization
def new_customer():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO customers
                (organization_id, name, nature_of_supply, ntn, strn, cnic, address, credit_limit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["name"].strip(),
            request.form.get("nature_of_supply", "Supplies"),
            request.form.get("ntn", "").strip() or None,
            request.form.get("strn", "").strip() or None,
            request.form.get("cnic", "").strip() or None,
            request.form.get("address", "").strip() or None,
            to_decimal(request.form.get("credit_limit") or 0),
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("customers.customers_list"))
    return render_template("customers/new_customer.html", nature_options=NATURE_OF_SUPPLY_OPTIONS)


# ---------------------------------------------------------------------
# Customer Receipts (on-account only for now - see module docstring)
# ---------------------------------------------------------------------

@bp.route("/receipts")
@require_organization
def customer_receipts_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cr.customer_receipt_id, c.name, cr.amount, cr.receipt_date, cr.has_wht
        FROM customer_receipts cr JOIN customers c ON c.customer_id = cr.customer_id
        WHERE cr.organization_id = %s
        ORDER BY cr.receipt_date DESC, cr.customer_receipt_id DESC
    """, (org_id,))
    receipts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("customers/customer_receipts_list.html", receipts=receipts)


@bp.route("/receipts/new", methods=["GET", "POST"])
@require_organization
def new_customer_receipt():
    org_id = current_organization_id()

    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()

        customer_id = int(request.form["customer_id"])
        gross_amount = to_decimal(request.form["amount"])
        has_wht = "has_wht" in request.form
        wht_rate = to_decimal(request.form.get("wht_rate") or 0) if has_wht else None
        wht_amount = money_round(gross_amount * (wht_rate / Decimal("100"))) if has_wht and wht_rate else to_decimal(0)
        net_received = gross_amount - wht_amount
        deposit_to_account_id = int(request.form["deposit_to_account_id"])

        cursor.execute("SELECT 1 FROM customers WHERE customer_id = %s AND organization_id = %s", (customer_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (deposit_to_account_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)

        cursor.execute("SELECT name, nature_of_supply FROM customers WHERE customer_id = %s", (customer_id,))
        customer_name, nature_of_supply = cursor.fetchone()
        trade_receivables_id = _get_account_id_by_code(cursor, org_id, "1100")

        je_lines = [{
            "account_id": deposit_to_account_id, "partner_name": customer_name,
            "label": f"Receipt from {customer_name}", "debit": net_received, "credit": 0,
        }]
        if has_wht and wht_amount:
            wht_code = "1150" if nature_of_supply == "Supplies" else "1160"
            wht_account_id = _get_account_id_by_code(cursor, org_id, wht_code)
            je_lines.append({
                "account_id": wht_account_id, "partner_name": customer_name,
                "label": f"WHT deducted by {customer_name} @ {wht_rate}%",
                "debit": wht_amount, "credit": 0,
            })
        je_lines.append({
            "account_id": trade_receivables_id, "partner_name": customer_name,
            "label": f"Receipt from {customer_name}", "debit": 0, "credit": gross_amount,
        })

        try:
            journal_header_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Customer Receipt - {customer_name}",
                accounting_date=request.form["receipt_date"],
                journal_name="Customer Receipts",
                lines=je_lines, source_type="CustomerReceipt", auto_post=True,
            )

            cursor.execute("""
                INSERT INTO customer_receipts
                    (organization_id, customer_id, amount, receipt_date, memo,
                     deposit_to_account_id, has_wht, wht_rate, wht_amount, journal_header_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING customer_receipt_id
            """, (
                org_id, customer_id, gross_amount, request.form["receipt_date"],
                request.form.get("memo", "").strip() or None, deposit_to_account_id,
                has_wht, wht_rate, wht_amount, journal_header_id,
            ))
            customer_receipt_id = cursor.fetchone()[0]
            conn.commit()
        except UnbalancedEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "customers/new_customer_receipt.html",
                customers=_get_customers(org_id), accounts=_get_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("customers.customer_receipt_detail", customer_receipt_id=customer_receipt_id))

    return render_template(
        "customers/new_customer_receipt.html",
        customers=_get_customers(org_id), accounts=_get_accounts(org_id),
        tax_rates=get_active_tax_rates(org_id),
        error=None, today=date.today().isoformat(),
    )


@bp.route("/receipts/<int:customer_receipt_id>")
@require_organization
def customer_receipt_detail(customer_receipt_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cr.customer_receipt_id, c.name, cr.amount, cr.receipt_date, cr.memo,
               coa.name, cr.has_wht, cr.wht_rate, cr.wht_amount, cr.journal_header_id
        FROM customer_receipts cr
        JOIN customers c ON c.customer_id = cr.customer_id
        JOIN chart_of_accounts coa ON coa.account_id = cr.deposit_to_account_id
        WHERE cr.customer_receipt_id = %s AND cr.organization_id = %s
    """, (customer_receipt_id, org_id))
    receipt = cursor.fetchone()
    cursor.close()
    conn.close()
    if not receipt:
        abort(404)
    return render_template("customers/customer_receipt_detail.html", receipt=receipt)
