"""
vendors/routes.py

Modular Flask Blueprint for LedgerPulse Vendors & Purchases:
- Vendor Master (List, Card, Search, Pagination, Smart-buttons)
- Vendor Bills (Direct Bills, Auto Bill Numbers, Status, Print, Journal)
- Vendor Credit Notes (Refunds & Bill Applications)
- Vendor Payments (BPV/CPV auto numbering, Manual/Cheque, Unique Cheque check, WHT, Allocation)
- Batch Vendor Payments
- Own Cheques Report
- Vendor Ledger (Partner Ledger) & Vendor Aging Reports
"""

from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, post_journal_entry, UnbalancedEntryError
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


def _get_bank_accounts(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE AND (account_type = 'Bank' OR lower(name) LIKE '%bank%' OR lower(name) LIKE '%cash%')
        ORDER BY code
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


def _generate_bill_number(cursor, org_id):
    year = date.today().year
    month = date.today().month
    cursor.execute("""
        SELECT COUNT(*) FROM vendor_bills
        WHERE organization_id = %s AND EXTRACT(YEAR FROM bill_date) = %s AND EXTRACT(MONTH FROM bill_date) = %s
    """, (org_id, year, month))
    count = cursor.fetchone()[0] + 1
    return f"BILL/{year}/{month:02d}/{count:04d}"


def _generate_voucher_number(cursor, org_id, is_bank):
    prefix = "BPV" if is_bank else "CPV"
    cursor.execute("""
        SELECT COUNT(*) FROM vendor_payments
        WHERE organization_id = %s AND voucher_number LIKE %s
    """, (org_id, f"{prefix}-%"))
    count = cursor.fetchone()[0] + 1
    return f"{prefix}-{count:05d}"


def _generate_credit_note_number(cursor, org_id):
    year = date.today().year
    month = date.today().month
    cursor.execute("""
        SELECT COUNT(*) FROM vendor_credit_notes
        WHERE organization_id = %s AND EXTRACT(YEAR FROM credit_date) = %s AND EXTRACT(MONTH FROM credit_date) = %s
    """, (org_id, year, month))
    count = cursor.fetchone()[0] + 1
    return f"BCN/{year}/{month:02d}/{count:04d}"


def _generate_batch_number(cursor, org_id):
    year = date.today().year
    cursor.execute("""
        SELECT COUNT(*) FROM batch_vendor_payments
        WHERE organization_id = %s AND EXTRACT(YEAR FROM payment_date) = %s
    """, (org_id, year))
    count = cursor.fetchone()[0] + 1
    return f"BVP/{year}/{count:04d}"


# ---------------------------------------------------------------------
# Phase 1: Vendors Master (List + Card + Search & Pagination)
# ---------------------------------------------------------------------

@bp.route("/")
@require_organization
def vendors_list():
    org_id = current_organization_id()
    search = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    per_page = 80
    offset = (page - 1) * per_page

    conn = get_conn()
    cursor = conn.cursor()

    where_clause = "organization_id = %s"
    params = [org_id]
    if search:
        where_clause += " AND (name ILIKE %s OR phone ILIKE %s OR ntn ILIKE %s OR strn ILIKE %s OR cnic ILIKE %s)"
        s_val = f"%{search}%"
        params.extend([s_val, s_val, s_val, s_val, s_val])

    cursor.execute(f"SELECT COUNT(*) FROM vendors WHERE {where_clause}", params)
    total_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, credit_limit, phone, city, is_active
        FROM vendors WHERE {where_clause}
        ORDER BY name LIMIT %s OFFSET %s
    """, params + [per_page, offset])
    vendors = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    return render_template(
        "vendors/vendors_list.html",
        vendors=vendors, search=search, page=page, total_pages=total_pages
    )


@bp.route("/new", methods=["GET", "POST"])
@require_organization
def new_vendor():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendors
                (organization_id, name, nature_of_purchase, ntn, strn, address, credit_limit,
                 is_company, email, phone, city, state, postal_code, country, cnic, website,
                 category, tags, region, territory, is_customer, payment_terms, default_payment_method, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["name"].strip(),
            request.form.get("nature_of_purchase", "Supplies"),
            request.form.get("ntn", "").strip() or None,
            request.form.get("strn", "").strip() or None,
            request.form.get("address", "").strip() or None,
            to_decimal(request.form.get("credit_limit") or 0),
            "is_company" in request.form,
            request.form.get("email", "").strip() or None,
            request.form.get("phone", "").strip() or None,
            request.form.get("city", "").strip() or None,
            request.form.get("state", "").strip() or None,
            request.form.get("postal_code", "").strip() or None,
            request.form.get("country", "Pakistan").strip() or "Pakistan",
            request.form.get("cnic", "").strip() or None,
            request.form.get("website", "").strip() or None,
            request.form.get("category", "").strip() or None,
            request.form.get("tags", "").strip() or None,
            request.form.get("region", "").strip() or None,
            request.form.get("territory", "").strip() or None,
            "is_customer" in request.form,
            request.form.get("payment_terms", "").strip() or None,
            request.form.get("default_payment_method", "Manual").strip() or "Manual",
            request.form.get("notes", "").strip() or None,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendors_list"))
    return render_template("vendors/new_vendor.html", nature_options=NATURE_OF_PURCHASE_OPTIONS, payment_methods=PAYMENT_METHODS)


@bp.route("/<int:vendor_id>")
@require_organization
def vendor_detail(vendor_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, address, credit_limit,
               is_company, email, phone, city, state, postal_code, country, cnic, website,
               category, tags, region, territory, is_customer, payment_terms, default_payment_method, notes, is_active
        FROM vendors WHERE vendor_id = %s AND organization_id = %s
    """, (vendor_id, org_id))
    vendor = cursor.fetchone()
    if not vendor:
        cursor.close()
        conn.close()
        abort(404)

    # Smart-button counts & bills/payments lists
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM vendor_bills WHERE vendor_id = %s", (vendor_id,))
    bill_count, total_billed = cursor.fetchone()

    cursor.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM vendor_payments WHERE vendor_id = %s", (vendor_id,))
    payment_count, total_paid = cursor.fetchone()

    cursor.execute("""
        SELECT vendor_bill_id, bill_number, bill_date, due_date, status, COALESCE(
            (SELECT SUM(vbl.total) FROM vendor_bill_lines vbl WHERE vbl.vendor_bill_id = vendor_bills.vendor_bill_id), 0
        ) as total
        FROM vendor_bills WHERE vendor_id = %s ORDER BY bill_date DESC LIMIT 10
    """, (vendor_id,))
    bills = cursor.fetchall()

    cursor.execute("""
        SELECT vendor_payment_id, voucher_number, payment_date, amount, payment_method, status
        FROM vendor_payments WHERE vendor_id = %s ORDER BY payment_date DESC LIMIT 10
    """, (vendor_id,))
    payments = cursor.fetchall()

    cursor.close()
    conn.close()

    balance = total_billed - total_paid

    return render_template(
        "vendors/vendor_detail.html",
        vendor=vendor, bill_count=bill_count, payment_count=payment_count,
        total_billed=total_billed, total_paid=total_paid, balance=balance,
        bills=bills, payments=payments
    )


# ---------------------------------------------------------------------
# Phase 2: Vendor Bills (Direct Bills + Auto Numbering + Print + Status)
# ---------------------------------------------------------------------

@bp.route("/bills")
@require_organization
def vendor_bills_list():
    org_id = current_organization_id()
    status_filter = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()

    conn = get_conn()
    cursor = conn.cursor()

    query = """
        SELECT vb.vendor_bill_id, vb.bill_number, v.name, vb.bill_date, vb.due_date, vb.status,
               COALESCE((SELECT SUM(vbl.total) FROM vendor_bill_lines vbl WHERE vbl.vendor_bill_id = vb.vendor_bill_id), 0) AS total
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        WHERE vb.organization_id = %s
    """
    params = [org_id]
    if status_filter:
        query += " AND vb.status = %s"
        params.append(status_filter)
    if search:
        query += " AND (vb.bill_number ILIKE %s OR v.name ILIKE %s OR vb.payment_reference ILIKE %s)"
        s_val = f"%{search}%"
        params.extend([s_val, s_val, s_val])

    query += " ORDER BY vb.bill_date DESC, vb.vendor_bill_id DESC"
    cursor.execute(query, params)
    bills = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_bills_list.html", bills=bills, status_filter=status_filter, search=search)


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

        bill_number = request.form.get("bill_number", "").strip() or _generate_bill_number(cursor, org_id)

        cursor.execute("""
            INSERT INTO vendor_bills
                (organization_id, vendor_id, bill_number, bill_date, due_date, receipt_date, accounting_date,
                 payment_terms, payment_reference, recipient_bank, lc_reference, journal_name, status, amount_due)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Draft', 0)
            RETURNING vendor_bill_id
        """, (
            org_id,
            vendor_id,
            bill_number,
            request.form["bill_date"],
            request.form.get("due_date") or None,
            request.form.get("receipt_date") or None,
            request.form.get("accounting_date") or request.form["bill_date"],
            request.form.get("payment_terms", "").strip() or None,
            request.form.get("payment_reference", "").strip() or None,
            request.form.get("recipient_bank", "").strip() or None,
            request.form.get("lc_reference", "").strip() or None,
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
            total = money_round(raw_tot) if raw_tot else money_round(qty * rate)
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
            "label": f"Vendor Bill - {vendor_name} ({bill_number})",
            "debit": 0,
            "credit": line_total_sum,
        })

        cursor.execute("UPDATE vendor_bills SET amount_due = %s WHERE vendor_bill_id = %s", (line_total_sum, vendor_bill_id))

        try:
            journal_header_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Vendor Bill {bill_number} - {vendor_name}",
                accounting_date=request.form.get("accounting_date") or request.form["bill_date"],
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

    conn = get_conn()
    cursor = conn.cursor()
    auto_num = _generate_bill_number(cursor, org_id)
    cursor.close()
    conn.close()

    return render_template(
        "vendors/new_vendor_bill.html",
        vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
        auto_number=auto_num, error=None, today=date.today().isoformat(),
    )


@bp.route("/bills/<int:vendor_bill_id>")
@require_organization
def vendor_bill_detail(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, vb.bill_number, v.name, vb.bill_date, vb.due_date, vb.receipt_date,
               vb.accounting_date, vb.status, vb.journal_header_id, vb.amount_due, vb.paid_amount,
               vb.payment_reference, vb.recipient_bank, vb.lc_reference, vb.journal_name, v.vendor_id
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

    # Allocations / payments against this bill
    cursor.execute("""
        SELECT vp.vendor_payment_id, vp.voucher_number, vp.payment_date, vpa.allocated_amount
        FROM vendor_payment_allocations vpa
        JOIN vendor_payments vp ON vp.vendor_payment_id = vpa.vendor_payment_id
        WHERE vpa.vendor_bill_id = %s
    """, (vendor_bill_id,))
    allocations = cursor.fetchall()

    cursor.close()
    conn.close()

    total = sum((to_decimal(line[5]) for line in lines), to_decimal(0))
    return render_template(
        "vendors/vendor_bill_detail.html",
        bill=bill, lines=lines, total=total, allocations=allocations
    )


@bp.route("/bills/<int:vendor_bill_id>/print")
@require_organization
def vendor_bill_print(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, vb.bill_number, v.name, v.address, v.ntn, v.strn,
               vb.bill_date, vb.due_date, vb.status, vb.amount_due, vb.paid_amount,
               vb.payment_reference, vb.recipient_bank, vb.lc_reference, org.name
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        JOIN organizations org ON org.organization_id = vb.organization_id
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
    return render_template("vendors/vendor_bill_print.html", bill=bill, lines=lines, total=total)


@bp.route("/bills/<int:vendor_bill_id>/post", methods=["POST"])
@require_organization
def post_vendor_bill(vendor_bill_id):
    org_id = current_organization_id()
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


@bp.route("/bills/<int:vendor_bill_id>/delete", methods=["POST"])
@require_organization
def delete_vendor_bill(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT status, journal_header_id FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s", (vendor_bill_id, org_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        abort(404)
    status, jh_id = row
    if status != 'Draft':
        conn.close()
        abort(400, description="Only Draft vendor bills can be deleted.")

    cursor.execute("DELETE FROM vendor_bill_lines WHERE vendor_bill_id = %s", (vendor_bill_id,))
    cursor.execute("DELETE FROM vendor_bills WHERE vendor_bill_id = %s", (vendor_bill_id,))
    if jh_id:
        cursor.execute("DELETE FROM journal_lines WHERE journal_header_id = %s", (jh_id,))
        cursor.execute("DELETE FROM journal_headers WHERE journal_header_id = %s", (jh_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for("vendors.vendor_bills_list"))


# ---------------------------------------------------------------------
# Phase 3: Vendor Credit Notes (Refunds)
# ---------------------------------------------------------------------

@bp.route("/credit-notes")
@require_organization
def vendor_credit_notes_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cn.vendor_credit_note_id, cn.credit_note_number, v.name, cn.credit_date, cn.status,
               COALESCE((SELECT SUM(cnl.total) FROM vendor_credit_note_lines cnl WHERE cnl.vendor_credit_note_id = cn.vendor_credit_note_id), 0) as total
        FROM vendor_credit_notes cn
        JOIN vendors v ON v.vendor_id = cn.vendor_id
        WHERE cn.organization_id = %s
        ORDER BY cn.credit_date DESC, cn.vendor_credit_note_id DESC
    """, (org_id,))
    credit_notes = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_credit_notes_list.html", credit_notes=credit_notes)


@bp.route("/credit-notes/new", methods=["GET", "POST"])
@require_organization
def new_vendor_credit_note():
    org_id = current_organization_id()
    original_bill_id = request.args.get("bill_id", type=int)

    if request.method == "POST":
        vendor_id = int(request.form["vendor_id"])
        orig_bill = request.form.get("original_bill_id", type=int)
        labels = request.form.getlist("label[]")
        account_ids = request.form.getlist("account_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        totals = request.form.getlist("total[]")

        conn = get_conn()
        cursor = conn.cursor()
        cn_number = _generate_credit_note_number(cursor, org_id)

        cursor.execute("""
            INSERT INTO vendor_credit_notes
                (organization_id, vendor_id, original_bill_id, credit_note_number, credit_date, status)
            VALUES (%s, %s, %s, %s, %s, 'Draft')
            RETURNING vendor_credit_note_id
        """, (
            org_id, vendor_id, orig_bill or None, cn_number, request.form["credit_date"]
        ))
        cn_id = cursor.fetchone()[0]

        je_lines = []
        total_sum = to_decimal(0)
        for i in range(len(account_ids)):
            if not account_ids[i]:
                continue
            qty = to_decimal(quantities[i] if i < len(quantities) else 1)
            rate = to_decimal(rates[i] if i < len(rates) else 0)
            raw_tot = totals[i] if i < len(totals) else None
            tot = money_round(raw_tot) if raw_tot else money_round(qty * rate)
            total_sum += tot
            cursor.execute("""
                INSERT INTO vendor_credit_note_lines
                    (vendor_credit_note_id, label, account_id, quantity, rate, total, line_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (cn_id, labels[i].strip() or None, int(account_ids[i]), qty, rate, tot, i))
            # Credit note reverses bill: Credit expense/asset account
            je_lines.append({
                "account_id": int(account_ids[i]),
                "label": labels[i].strip() or None,
                "debit": to_decimal(0),
                "credit": tot,
            })

        cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s", (vendor_id,))
        vendor_name = cursor.fetchone()[0]
        trade_creditors_id = _get_account_id_by_code(cursor, org_id, "2000")
        # Debit Trade Creditors
        je_lines.append({
            "account_id": trade_creditors_id,
            "partner_name": vendor_name,
            "label": f"Vendor Credit Note - {vendor_name} ({cn_number})",
            "debit": total_sum,
            "credit": to_decimal(0),
        })

        try:
            jh_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Vendor Credit Note {cn_number} - {vendor_name}",
                accounting_date=request.form["credit_date"],
                journal_name="Vendor Credit Notes",
                lines=je_lines, source_type="VendorCreditNote", source_id=cn_id,
                auto_post=False,
            )
            cursor.execute("UPDATE vendor_credit_notes SET journal_header_id = %s WHERE vendor_credit_note_id = %s", (jh_id, cn_id))
            conn.commit()
        except UnbalancedEntryError as err:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_credit_note.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                error=str(err), today=date.today().isoformat()
            )

        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_credit_note_detail", vendor_credit_note_id=cn_id))

    conn = get_conn()
    cursor = conn.cursor()
    auto_num = _generate_credit_note_number(cursor, org_id)
    orig_bill_data = None
    if original_bill_id:
        cursor.execute("SELECT vendor_bill_id, bill_number, vendor_id FROM vendor_bills WHERE vendor_bill_id = %s", (original_bill_id,))
        orig_bill_data = cursor.fetchone()
    cursor.close()
    conn.close()

    return render_template(
        "vendors/new_vendor_credit_note.html",
        vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
        auto_number=auto_num, original_bill_id=original_bill_id, orig_bill_data=orig_bill_data,
        error=None, today=date.today().isoformat()
    )


@bp.route("/credit-notes/<int:vendor_credit_note_id>")
@require_organization
def vendor_credit_note_detail(vendor_credit_note_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cn.vendor_credit_note_id, cn.credit_note_number, v.name, cn.credit_date, cn.status,
               cn.journal_header_id, cn.original_bill_id
        FROM vendor_credit_notes cn JOIN vendors v ON v.vendor_id = cn.vendor_id
        WHERE cn.vendor_credit_note_id = %s AND cn.organization_id = %s
    """, (vendor_credit_note_id, org_id))
    cn = cursor.fetchone()
    if not cn:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT cnl.label, coa.code, coa.name, cnl.quantity, cnl.rate, cnl.total
        FROM vendor_credit_note_lines cnl JOIN chart_of_accounts coa ON coa.account_id = cnl.account_id
        WHERE cnl.vendor_credit_note_id = %s ORDER BY cnl.line_order
    """, (vendor_credit_note_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total = sum((to_decimal(l[5]) for l in lines), to_decimal(0))
    return render_template("vendors/vendor_credit_note_detail.html", cn=cn, lines=lines, total=total)


@bp.route("/credit-notes/<int:vendor_credit_note_id>/post", methods=["POST"])
@require_organization
def post_vendor_credit_note(vendor_credit_note_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM vendor_credit_notes WHERE vendor_credit_note_id = %s AND organization_id = %s", (vendor_credit_note_id, org_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        abort(404)
    jh_id = row[0]
    try:
        post_journal_entry(cursor, jh_id)
        cursor.execute("UPDATE vendor_credit_notes SET status = 'Posted' WHERE vendor_credit_note_id = %s", (vendor_credit_note_id,))
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("vendors.vendor_credit_note_detail", vendor_credit_note_id=vendor_credit_note_id))


# ---------------------------------------------------------------------
# Phase 4: Vendor Payments (BPV/CPV, Unique Cheque, WHT, Allocation)
# ---------------------------------------------------------------------

@bp.route("/payments")
@require_organization
def vendor_payments_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, vp.voucher_number, v.name, vp.amount, vp.payment_date,
               vp.payment_method, vp.has_wht, vp.status
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
        cursor.execute("SELECT account_type, name FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (paid_from_account_id, org_id))
        acc_row = cursor.fetchone()
        if not acc_row:
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        is_bank = (acc_row[0] == 'Bank' or 'bank' in acc_row[1].lower())

        base_amount = to_decimal(request.form["amount"])
        has_wht = "has_wht" in request.form
        wht_rate = to_decimal(request.form.get("wht_rate") or 0) if has_wht else None
        wht_amount = money_round(base_amount * (wht_rate / Decimal("100"))) if has_wht and wht_rate else to_decimal(0)
        net_amount = base_amount - wht_amount
        payment_method = request.form.get("payment_method", "Manual")
        cheque_number = request.form.get("cheque_number", "").strip() or None
        cheque_date = request.form.get("cheque_date") or None
        voucher_number = request.form.get("voucher_number", "").strip() or _generate_voucher_number(cursor, org_id, is_bank)

        cursor.execute("SELECT name, nature_of_purchase FROM vendors WHERE vendor_id = %s", (vendor_id,))
        vendor_name, nature_of_purchase = cursor.fetchone()
        trade_creditors_id = _get_account_id_by_code(cursor, org_id, "2000")

        je_lines = [{
            "account_id": trade_creditors_id, "partner_name": vendor_name,
            "label": f"Vendor Payment {voucher_number} - {vendor_name}", "debit": base_amount, "credit": 0,
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
            "label": f"Payment to {vendor_name} ({voucher_number})", "debit": 0, "credit": net_amount,
        })

        try:
            if cheque_number:
                cursor.execute("""
                    SELECT 1 FROM vendor_payments
                    WHERE paid_from_account_id = %s AND cheque_number = %s
                """, (paid_from_account_id, cheque_number))
                if cursor.fetchone():
                    raise ValueError(f"Cheque number {cheque_number} has already been used for this bank account.")

            jh_id = create_journal_entry(
                cursor, organization_id=org_id,
                reference=f"Vendor Payment {voucher_number} - {vendor_name}",
                accounting_date=request.form["payment_date"],
                journal_name="Vendor Payments",
                lines=je_lines, source_type="VendorPayment", auto_post=True,
            )

            cursor.execute("""
                INSERT INTO vendor_payments
                    (organization_id, vendor_id, amount, payment_date, voucher_number, memo,
                     paid_from_account_id, payment_method, cheque_number, cheque_date,
                     lc_reference, vendor_bank_account, has_wht, wht_rate, wht_amount,
                     status, journal_header_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Paid', %s)
                RETURNING vendor_payment_id
            """, (
                org_id, vendor_id, base_amount, request.form["payment_date"], voucher_number,
                request.form.get("memo", "").strip() or None, paid_from_account_id,
                payment_method, cheque_number, cheque_date,
                request.form.get("lc_reference", "").strip() or None,
                request.form.get("vendor_bank_account", "").strip() or None,
                has_wht, wht_rate, wht_amount, jh_id,
            ))
            payment_id = cursor.fetchone()[0]

            # Optional allocation against bills if bill_id(s) submitted
            bill_ids = request.form.getlist("bill_id[]")
            alloc_amounts = request.form.getlist("alloc_amount[]")
            for i in range(len(bill_ids)):
                if bill_ids[i] and alloc_amounts[i]:
                    b_id = int(bill_ids[i])
                    a_amt = to_decimal(alloc_amounts[i])
                    if a_amt > 0:
                        cursor.execute("""
                            INSERT INTO vendor_payment_allocations (vendor_payment_id, vendor_bill_id, allocated_amount)
                            VALUES (%s, %s, %s)
                        """, (payment_id, b_id, a_amt))
                        cursor.execute("""
                            UPDATE vendor_bills SET paid_amount = COALESCE(paid_amount, 0) + %s,
                            status = CASE WHEN COALESCE(paid_amount, 0) + %s >= amount_due THEN 'Paid' ELSE status END
                            WHERE vendor_bill_id = %s
                        """, (a_amt, a_amt, b_id))

            conn.commit()
        except (UnbalancedEntryError, ValueError) as err:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_payment.html",
                vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(err), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_payment_detail", vendor_payment_id=payment_id))

    conn = get_conn()
    cursor = conn.cursor()
    auto_bpv = _generate_voucher_number(cursor, org_id, True)
    cursor.close()
    conn.close()

    return render_template(
        "vendors/new_vendor_payment.html",
        vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
        tax_rates=get_active_tax_rates(org_id),
        auto_voucher=auto_bpv, error=None, today=date.today().isoformat(),
    )


@bp.route("/payments/<int:vendor_payment_id>")
@require_organization
def vendor_payment_detail(vendor_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, vp.voucher_number, v.name, vp.amount, vp.payment_date, vp.memo,
               coa.name, vp.payment_method, vp.cheque_number, vp.cheque_date,
               vp.has_wht, vp.wht_rate, vp.wht_amount, vp.journal_header_id, vp.status,
               vp.lc_reference, vp.vendor_bank_account
        FROM vendor_payments vp
        JOIN vendors v ON v.vendor_id = vp.vendor_id
        JOIN chart_of_accounts coa ON coa.account_id = vp.paid_from_account_id
        WHERE vp.vendor_payment_id = %s AND vp.organization_id = %s
    """, (vendor_payment_id, org_id))
    payment = cursor.fetchone()

    cursor.execute("""
        SELECT vb.vendor_bill_id, vb.bill_number, vb.bill_date, vpa.allocated_amount
        FROM vendor_payment_allocations vpa
        JOIN vendor_bills vb ON vb.vendor_bill_id = vpa.vendor_bill_id
        WHERE vpa.vendor_payment_id = %s
    """, (vendor_payment_id,))
    allocations = cursor.fetchall()

    cursor.close()
    conn.close()
    if not payment:
        abort(404)
    return render_template("vendors/vendor_payment_detail.html", payment=payment, allocations=allocations)


@bp.route("/payments/<int:vendor_payment_id>/print")
@require_organization
def vendor_payment_print(vendor_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, vp.voucher_number, v.name, v.address, v.ntn,
               vp.amount, vp.payment_date, vp.memo, coa.name, vp.payment_method,
               vp.cheque_number, vp.cheque_date, vp.has_wht, vp.wht_rate, vp.wht_amount,
               org.name, vp.lc_reference, vp.vendor_bank_account
        FROM vendor_payments vp
        JOIN vendors v ON v.vendor_id = vp.vendor_id
        JOIN chart_of_accounts coa ON coa.account_id = vp.paid_from_account_id
        JOIN organizations org ON org.organization_id = vp.organization_id
        WHERE vp.vendor_payment_id = %s AND vp.organization_id = %s
    """, (vendor_payment_id, org_id))
    payment = cursor.fetchone()
    if not payment:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT vb.bill_number, vb.bill_date, vpa.allocated_amount
        FROM vendor_payment_allocations vpa
        JOIN vendor_bills vb ON vb.vendor_bill_id = vpa.vendor_bill_id
        WHERE vpa.vendor_payment_id = %s
    """, (vendor_payment_id,))
    allocations = cursor.fetchall()
    cursor.close()
    conn.close()

    net_paid = payment[5] - (payment[14] or 0)
    return render_template("vendors/vendor_payment_print.html", payment=payment, allocations=allocations, net_paid=net_paid)


# ---------------------------------------------------------------------
# Phase 5: Batch Vendor Payments
# ---------------------------------------------------------------------

@bp.route("/batch-payments")
@require_organization
def batch_vendor_payments_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bp.batch_payment_id, bp.batch_number, bp.payment_date, coa.name, bp.total_amount, bp.status
        FROM batch_vendor_payments bp
        JOIN chart_of_accounts coa ON coa.account_id = bp.paid_from_account_id
        WHERE bp.organization_id = %s
        ORDER BY bp.payment_date DESC, bp.batch_payment_id DESC
    """, (org_id,))
    batches = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/batch_vendor_payments_list.html", batches=batches)


@bp.route("/batch-payments/new", methods=["GET", "POST"])
@require_organization
def new_batch_vendor_payment():
    org_id = current_organization_id()

    if request.method == "POST":
        paid_from_account_id = int(request.form["paid_from_account_id"])
        vendor_ids = request.form.getlist("vendor_id[]")
        amounts = request.form.getlist("amount[]")
        memos = request.form.getlist("memo[]")

        conn = get_conn()
        cursor = conn.cursor()
        batch_number = _generate_batch_number(cursor, org_id)

        total_sum = to_decimal(0)
        parsed_lines = []
        for i in range(len(vendor_ids)):
            if not vendor_ids[i] or not amounts[i]:
                continue
            v_id = int(vendor_ids[i])
            amt = to_decimal(amounts[i])
            if amt <= 0:
                continue
            total_sum += amt
            parsed_lines.append((v_id, amt, memos[i].strip() if i < len(memos) and memos[i] else ""))

        if not parsed_lines:
            conn.rollback()
            cursor.close()
            conn.close()
            return redirect(url_for("vendors.new_batch_vendor_payment"))

        cursor.execute("""
            INSERT INTO batch_vendor_payments
                (organization_id, batch_number, payment_date, paid_from_account_id, payment_method, total_amount, status)
            VALUES (%s, %s, %s, %s, 'Manual', %s, 'Confirmed')
            RETURNING batch_payment_id
        """, (org_id, batch_number, request.form["payment_date"], paid_from_account_id, total_sum))
        batch_id = cursor.fetchone()[0]

        # Generate individual payments for each line
        for v_id, amt, memo in parsed_lines:
            v_num = _generate_voucher_number(cursor, org_id, True)
            cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s", (v_id,))
            v_name = cursor.fetchone()[0]
            trade_creditors_id = _get_account_id_by_code(cursor, org_id, "2000")

            je_lines = [
                {"account_id": trade_creditors_id, "partner_name": v_name, "label": f"Batch payment {batch_number} to {v_name}", "debit": amt, "credit": 0},
                {"account_id": paid_from_account_id, "partner_name": v_name, "label": f"Payment via batch {batch_number}", "debit": 0, "credit": amt},
            ]
            try:
                jh_id = create_journal_entry(
                    cursor, organization_id=org_id,
                    reference=f"Batch Payment {batch_number} - {v_name}",
                    accounting_date=request.form["payment_date"],
                    journal_name="Vendor Payments",
                    lines=je_lines, source_type="VendorPayment", auto_post=True,
                )
                cursor.execute("""
                    INSERT INTO vendor_payments
                        (organization_id, vendor_id, amount, payment_date, voucher_number, memo,
                         paid_from_account_id, payment_method, status, journal_header_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'Manual', 'Paid', %s)
                    RETURNING vendor_payment_id
                """, (org_id, v_id, amt, request.form["payment_date"], v_num, memo or f"Batch {batch_number}", paid_from_account_id, jh_id))
                vp_id = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO batch_vendor_payment_lines (batch_payment_id, vendor_id, amount, memo, vendor_payment_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (batch_id, v_id, amt, memo, vp_id))
            except Exception as e:
                conn.rollback()
                cursor.close()
                conn.close()
                return render_template(
                    "vendors/new_batch_vendor_payment.html",
                    vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
                    error=str(e), today=date.today().isoformat()
                )

        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("vendors.batch_vendor_payment_detail", batch_payment_id=batch_id))

    conn = get_conn()
    cursor = conn.cursor()
    auto_batch = _generate_batch_number(cursor, org_id)
    cursor.close()
    conn.close()

    return render_template(
        "vendors/new_batch_vendor_payment.html",
        vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
        auto_batch=auto_batch, error=None, today=date.today().isoformat()
    )


@bp.route("/batch-payments/<int:batch_payment_id>")
@require_organization
def batch_vendor_payment_detail(batch_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bp.batch_payment_id, bp.batch_number, bp.payment_date, coa.name, bp.total_amount, bp.status
        FROM batch_vendor_payments bp
        JOIN chart_of_accounts coa ON coa.account_id = bp.paid_from_account_id
        WHERE bp.batch_payment_id = %s AND bp.organization_id = %s
    """, (batch_payment_id, org_id))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT v.name, bpl.amount, bpl.memo, bpl.vendor_payment_id, vp.voucher_number
        FROM batch_vendor_payment_lines bpl
        JOIN vendors v ON v.vendor_id = bpl.vendor_id
        LEFT JOIN vendor_payments vp ON vp.vendor_payment_id = bpl.vendor_payment_id
        WHERE bpl.batch_payment_id = %s
    """, (batch_payment_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/batch_vendor_payment_detail.html", batch=batch, lines=lines)


# ---------------------------------------------------------------------
# Phase 6: Own Cheques Report
# ---------------------------------------------------------------------

@bp.route("/reports/own-cheques")
@require_organization
def own_cheques_report():
    org_id = current_organization_id()
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    vendor_id = request.args.get("vendor_id", type=int)

    conn = get_conn()
    cursor = conn.cursor()
    query = """
        SELECT vp.payment_date, vp.cheque_number, coa.name, v.name, vp.amount, vp.status
        FROM vendor_payments vp
        JOIN vendors v ON v.vendor_id = vp.vendor_id
        JOIN chart_of_accounts coa ON coa.account_id = vp.paid_from_account_id
        WHERE vp.organization_id = %s AND vp.payment_method = 'Cheque'
    """
    params = [org_id]
    if start_date:
        query += " AND vp.payment_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND vp.payment_date <= %s"
        params.append(end_date)
    if vendor_id:
        query += " AND vp.vendor_id = %s"
        params.append(vendor_id)

    query += " ORDER BY vp.payment_date DESC, vp.cheque_number DESC"
    cursor.execute(query, params)
    cheques = cursor.fetchall()
    vendors = _get_vendors(org_id)
    cursor.close()
    conn.close()

    total_amount = sum((to_decimal(c[4]) for c in cheques), to_decimal(0))
    return render_template(
        "vendors/own_cheques_report.html",
        cheques=cheques, vendors=vendors, start_date=start_date, end_date=end_date,
        selected_vendor=vendor_id, total_amount=total_amount
    )


# ---------------------------------------------------------------------
# Phase 7: Vendor Ledger (Partner Ledger) & Vendor Aging Reports
# ---------------------------------------------------------------------

@bp.route("/reports/ledger")
@require_organization
def vendor_ledger():
    org_id = current_organization_id()
    vendor_id = request.args.get("vendor_id", type=int)
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    vendors = _get_vendors(org_id)
    transactions = []
    vendor_name = ""
    opening_balance = to_decimal(0)
    closing_balance = to_decimal(0)

    if vendor_id:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s AND organization_id = %s", (vendor_id, org_id))
        v_row = cursor.fetchone()
        if v_row:
            vendor_name = v_row[0]

        # Gather bills and payments for this vendor
        query_bills = """
            SELECT bill_date as tx_date, bill_number as ref, 'Bill' as type, amount_due as debit, 0 as credit
            FROM vendor_bills WHERE vendor_id = %s AND organization_id = %s AND status = 'Posted'
        """
        query_payments = """
            SELECT payment_date as tx_date, voucher_number as ref, 'Payment' as type, 0 as debit, amount as credit
            FROM vendor_payments WHERE vendor_id = %s AND organization_id = %s AND status = 'Paid'
        """
        # Combine UNION ALL ordered by date
        combined_query = f"""
            SELECT * FROM ({query_bills} UNION ALL {query_payments}) t
        """
        params = [vendor_id, org_id, vendor_id, org_id]
        if start_date:
            combined_query += " WHERE tx_date >= %s"
            params.append(start_date)
        if end_date:
            combined_query += " AND tx_date <= %s"
            params.append(end_date)
        combined_query += " ORDER BY tx_date ASC"

        cursor.execute(combined_query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        running = opening_balance
        for r in rows:
            dt, ref, typ, dr, cr = r
            dr_dec = to_decimal(dr)
            cr_dec = to_decimal(cr)
            running = running + dr_dec - cr_dec
            transactions.append((dt, ref, typ, dr_dec, cr_dec, running))
        closing_balance = running

    return render_template(
        "vendors/vendor_ledger.html",
        vendors=vendors, selected_vendor=vendor_id, vendor_name=vendor_name,
        transactions=transactions, opening_balance=opening_balance, closing_balance=closing_balance,
        start_date=start_date, end_date=end_date
    )


@bp.route("/reports/aging")
@require_organization
def vendor_aging():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT v.vendor_id, v.name,
               COALESCE(SUM(vb.amount_due - COALESCE(vb.paid_amount, 0)), 0) as total_due
        FROM vendors v
        LEFT JOIN vendor_bills vb ON vb.vendor_id = v.vendor_id AND vb.status = 'Posted' AND vb.amount_due > COALESCE(vb.paid_amount, 0)
        WHERE v.organization_id = %s
        GROUP BY v.vendor_id, v.name
        HAVING COALESCE(SUM(vb.amount_due - COALESCE(vb.paid_amount, 0)), 0) > 0
        ORDER BY total_due DESC
    """, (org_id,))
    aging_data = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("vendors/vendor_aging.html", aging_data=aging_data)
