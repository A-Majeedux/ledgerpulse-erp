from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, UnbalancedEntryError
from wht.routes import get_active_tax_rates
from core.money import to_decimal, money_round

bp = Blueprint("vendors", __name__, url_prefix="/vendors")

NATURE_OF_PURCHASE_OPTIONS = ["Supplies", "Services"]
PAYMENT_METHODS = ["Manual", "Cheque"]


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


def _get_vendors(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_id, name, nature_of_purchase FROM vendors
        WHERE organization_id = %s AND is_active = TRUE ORDER BY name
    """, (org_id,))
    vendors = cursor.fetchall()
    cursor.close()
    conn.close()
    return vendors


# ---------------------------------------------------------------------
# Vendor master
# ---------------------------------------------------------------------

@bp.route("/")
@require_organization
def vendors_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, credit_limit
        FROM vendors WHERE organization_id = %s ORDER BY name
    """, (org_id,))
    vendors = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/vendors_list.html", vendors=vendors)


@bp.route("/new", methods=["GET", "POST"])
@require_organization
def new_vendor():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendors
                (organization_id, name, nature_of_purchase, ntn, strn, address, credit_limit)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["name"].strip(),
            request.form.get("nature_of_purchase", "Supplies"),
            request.form.get("ntn", "").strip() or None,
            request.form.get("strn", "").strip() or None,
            request.form.get("address", "").strip() or None,
            to_decimal(request.form.get("credit_limit") or 0),
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendors_list"))
    return render_template("vendors/new_vendor.html", nature_options=NATURE_OF_PURCHASE_OPTIONS)


# ---------------------------------------------------------------------
# Vendor Bills (direct/non-PO)
# ---------------------------------------------------------------------

@bp.route("/bills")
@require_organization
def vendor_bills_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, v.name, vb.bill_date, vb.due_date, vb.status,
               COALESCE(SUM(vbl.total), 0) AS total
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        LEFT JOIN vendor_bill_lines vbl ON vbl.vendor_bill_id = vb.vendor_bill_id
        WHERE vb.organization_id = %s
        GROUP BY vb.vendor_bill_id, v.name
        ORDER BY vb.bill_date DESC, vb.vendor_bill_id DESC
    """, (org_id,))
    bills = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_bills_list.html", bills=bills)


@bp.route("/bills/new", methods=["GET", "POST"])
@require_organization
def new_vendor_bill():
    org_id = current_organization_id()

    if request.method == "POST":
        vendor_id = int(request.form["vendor_id"])
        labels = request.form.getlist("label[]")
        account_ids = request.form.getlist("account_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        totals = request.form.getlist("total[]")

        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM vendors WHERE vendor_id = %s AND organization_id = %s", (vendor_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        for acc_id in account_ids:
            if acc_id:
                cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (int(acc_id), org_id))
                if not cursor.fetchone():
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    abort(400)

        cursor.execute("""
            INSERT INTO vendor_bills
                (organization_id, vendor_id, bill_date, due_date, payment_terms, journal_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING vendor_bill_id
        """, (
            org_id,
            vendor_id,
            request.form["bill_date"],
            request.form.get("due_date") or None,
            request.form.get("payment_terms", "").strip() or None,
            request.form.get("journal_name", "Expenses").strip(),
        ))
        vendor_bill_id = cursor.fetchone()[0]

        je_lines = []
        line_total_sum = to_decimal(0)
        for i in range(len(account_ids)):
            if not account_ids[i]:
                continue
            qty = to_decimal(quantities[i] if i < len(quantities) else 1)
            rate = to_decimal(rates[i] if i < len(rates) else 0)
            raw_tot = totals[i] if i < len(totals) else None
            total = money_round(raw_tot) if raw_tot else to_decimal(0)
            line_total_sum += total
            cursor.execute("""
                INSERT INTO vendor_bill_lines
                    (vendor_bill_id, label, account_id, quantity, rate, total, line_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                vendor_bill_id, labels[i].strip() or None, int(account_ids[i]),
                qty, rate, total, i,
            ))
            je_lines.append({
                "account_id": int(account_ids[i]),
                "label": labels[i].strip() or None,
                "debit": total,
                "credit": to_decimal(0),
            })

        cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s", (vendor_id,))
        vendor_name = cursor.fetchone()[0]
        trade_creditors_id = _get_account_id_by_code(cursor, org_id, "2000")
        je_lines.append({
            "account_id": trade_creditors_id,
            "partner_name": vendor_name,
            "label": f"Vendor Bill - {vendor_name}",
            "debit": 0,
            "credit": line_total_sum,
        })

        try:
            journal_header_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Vendor Bill - {vendor_name}",
                accounting_date=request.form["bill_date"],
                journal_name=request.form.get("journal_name", "Expenses").strip(),
                lines=je_lines, source_type="VendorBill", source_id=vendor_bill_id,
                auto_post=False,
            )
            cursor.execute(
                "UPDATE vendor_bills SET journal_header_id = %s WHERE vendor_bill_id = %s",
                (journal_header_id, vendor_bill_id)
            )
            conn.commit()
        except UnbalancedEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_bill.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))

    return render_template(
        "vendors/new_vendor_bill.html",
        vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
        error=None, today=date.today().isoformat(),
    )


@bp.route("/bills/<int:vendor_bill_id>")
@require_organization
def vendor_bill_detail(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, v.name, vb.bill_date, vb.due_date, vb.status, vb.journal_header_id
        FROM vendor_bills vb JOIN vendors v ON v.vendor_id = vb.vendor_id
        WHERE vb.vendor_bill_id = %s AND vb.organization_id = %s
    """, (vendor_bill_id, org_id))
    bill = cursor.fetchone()
    if not bill:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT vbl.label, coa.code, coa.name, vbl.quantity, vbl.rate, vbl.total
        FROM vendor_bill_lines vbl JOIN chart_of_accounts coa ON coa.account_id = vbl.account_id
        WHERE vbl.vendor_bill_id = %s ORDER BY vbl.line_order
    """, (vendor_bill_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total = sum((to_decimal(line[5]) for line in lines), to_decimal(0))
    return render_template("vendors/vendor_bill_detail.html", bill=bill, lines=lines, total=total)


@bp.route("/bills/<int:vendor_bill_id>/post", methods=["POST"])
@require_organization
def post_vendor_bill(vendor_bill_id):
    org_id = current_organization_id()
    from accounting.services import post_journal_entry
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s", (vendor_bill_id, org_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        abort(404)
    journal_header_id = row[0]
    try:
        post_journal_entry(cursor, journal_header_id)
        cursor.execute("UPDATE vendor_bills SET status = 'Posted' WHERE vendor_bill_id = %s", (vendor_bill_id,))
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))


# ---------------------------------------------------------------------
# Vendor Payments (with WHT)
# ---------------------------------------------------------------------

@bp.route("/payments")
@require_organization
def vendor_payments_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, v.name, vp.amount, vp.payment_date,
               vp.payment_method, vp.has_wht
        FROM vendor_payments vp JOIN vendors v ON v.vendor_id = vp.vendor_id
        WHERE vp.organization_id = %s
        ORDER BY vp.payment_date DESC, vp.vendor_payment_id DESC
    """, (org_id,))
    payments = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_payments_list.html", payments=payments)


@bp.route("/payments/new", methods=["GET", "POST"])
@require_organization
def new_vendor_payment():
    org_id = current_organization_id()

    if request.method == "POST":
        vendor_id = int(request.form["vendor_id"])
        paid_from_account_id = int(request.form["paid_from_account_id"])

        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM vendors WHERE vendor_id = %s AND organization_id = %s", (vendor_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (paid_from_account_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)

        base_amount = to_decimal(request.form["amount"])
        has_wht = "has_wht" in request.form
        wht_rate = to_decimal(request.form.get("wht_rate") or 0) if has_wht else None
        wht_amount = money_round(base_amount * (wht_rate / Decimal("100"))) if has_wht and wht_rate else to_decimal(0)
        net_amount = base_amount - wht_amount
        payment_method = request.form.get("payment_method", "Manual")
        cheque_number = request.form.get("cheque_number", "").strip() or None
        cheque_date = request.form.get("cheque_date") or None

        cursor.execute("SELECT name, nature_of_purchase FROM vendors WHERE vendor_id = %s", (vendor_id,))
        vendor_name, nature_of_purchase = cursor.fetchone()
        trade_creditors_id = _get_account_id_by_code(cursor, org_id, "2000")

        je_lines = [{
            "account_id": trade_creditors_id, "partner_name": vendor_name,
            "label": f"Vendor Payment - {vendor_name}", "debit": base_amount, "credit": 0,
        }]
        if has_wht and wht_amount:
            wht_code = "2100" if nature_of_purchase == "Supplies" else "2110"
            wht_account_id = _get_account_id_by_code(cursor, org_id, wht_code)
            je_lines.append({
                "account_id": wht_account_id, "partner_name": vendor_name,
                "label": f"WHT deducted @ {wht_rate}% on {vendor_name}",
                "debit": 0, "credit": wht_amount,
            })
        je_lines.append({
            "account_id": paid_from_account_id, "partner_name": vendor_name,
            "label": f"Payment to {vendor_name}", "debit": 0, "credit": net_amount,
        })

        try:
            # Cheque-number-reuse is also caught at the database level (see
            # the unique index in vendors/schema.py) - this check just gives
            # a friendlier error message instead of a raw database error.
            if cheque_number:
                cursor.execute("""
                    SELECT 1 FROM vendor_payments
                    WHERE paid_from_account_id = %s AND cheque_number = %s
                """, (paid_from_account_id, cheque_number))
                if cursor.fetchone():
                    raise ValueError(f"Cheque number {cheque_number} has already been used for this bank account.")

            journal_header_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Vendor Payment - {vendor_name}",
                accounting_date=request.form["payment_date"],
                journal_name="Vendor Payments",
                lines=je_lines, source_type="VendorPayment", auto_post=True,
            )

            cursor.execute("""
                INSERT INTO vendor_payments
                    (organization_id, vendor_id, amount, payment_date, memo,
                     paid_from_account_id, payment_method, cheque_number, cheque_date,
                     has_wht, wht_rate, wht_amount, journal_header_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING vendor_payment_id
            """, (
                org_id, vendor_id, base_amount, request.form["payment_date"],
                request.form.get("memo", "").strip() or None, paid_from_account_id,
                payment_method, cheque_number, cheque_date, has_wht, wht_rate,
                wht_amount, journal_header_id,
            ))
            vendor_payment_id = cursor.fetchone()[0]
            conn.commit()
        except (UnbalancedEntryError, ValueError) as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_payment.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_payment_detail", vendor_payment_id=vendor_payment_id))

    return render_template(
        "vendors/new_vendor_payment.html",
        vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
        tax_rates=get_active_tax_rates(org_id),
        error=None, today=date.today().isoformat(),
    )


@bp.route("/payments/<int:vendor_payment_id>")
@require_organization
def vendor_payment_detail(vendor_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, v.name, vp.amount, vp.payment_date, vp.memo,
               coa.name, vp.payment_method, vp.cheque_number, vp.cheque_date,
               vp.has_wht, vp.wht_rate, vp.wht_amount, vp.journal_header_id
        FROM vendor_payments vp
        JOIN vendors v ON v.vendor_id = vp.vendor_id
        JOIN chart_of_accounts coa ON coa.account_id = vp.paid_from_account_id
        WHERE vp.vendor_payment_id = %s AND vp.organization_id = %s
    """, (vendor_payment_id, org_id))
    payment = cursor.fetchone()
    cursor.close()
    conn.close()
    if not payment:
        abort(404)
    return render_template("vendors/vendor_payment_detail.html", payment=payment)
