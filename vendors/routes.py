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
import os
import uuid
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, current_app
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, post_journal_entry, UnbalancedEntryError
from wht.routes import get_active_tax_rates
from core.money import to_decimal, money_round
from vendors.landed_cost import (BASIS_LABELS, landed_unit_cost_by_product, normalise_basis,
                                 split_landed_cost)
from vendors.services import (duplicate_vendor_bill, replace_vendor_bill,
                             reset_vendor_bill_to_draft)
from core.docmenu import build_menu
from core.searchbar import read_search, search_filters, and_clauses, describe, suggest
from auth.services import require_permission, current_user_id

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


def _trade_payables_account_id(cursor, org_id):
    """The company's Trade Payables account, whatever it is numbered.

    The default chart numbers it 2000 Trade Creditors, but a chart carried over
    from the owner's own books numbers it 2100 Trade Payables - and a bill
    posted against nothing is not a bill. So: the usual codes first, then any
    liability that calls itself payables or creditors.
    """
    for code in ("2000", "2100"):
        account_id = _get_account_id_by_code(cursor, org_id, code)
        if account_id:
            return account_id
    cursor.execute("""
        SELECT account_id FROM chart_of_accounts
        WHERE organization_id = %s
          AND (name ILIKE '%%trade payable%%' OR name ILIKE '%%trade creditor%%'
               OR name ILIKE '%%accounts payable%%' OR name ILIKE '%%sundry creditor%%')
        ORDER BY account_id
        LIMIT 1
    """, (org_id,))
    row = cursor.fetchone()
    if row:
        return row[0]
    raise ValueError(
        "This company has no Trade Payables account, so the entry cannot be posted. "
        "Add one in Chart of Accounts (2000 Trade Creditors, or 2100 Trade Payables), "
        "then save again."
    )


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
        WHERE organization_id = %s AND is_active = TRUE AND (account_type = 'Bank' OR lower(name) LIKE '%%bank%%' OR lower(name) LIKE '%%cash%%')
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return accounts


def _get_products_for_bill(org_id):
    """The products a purchase line can point at, for the landed-cost maths."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT product_id, name, unit FROM products
        WHERE organization_id = %s AND COALESCE(is_active, TRUE) = TRUE ORDER BY name
    """, (org_id,))
    products = cursor.fetchall()
    cursor.close()
    conn.close()
    return products


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
@require_permission("vendors.view")
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
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, credit_limit, phone, city, is_active, logo_path
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
@require_permission("vendors.create")
def new_vendor():
    org_id = current_organization_id()
    form_data = {}
    if request.method == "POST":
        form_data = request.form.to_dict()
        entity_type = request.form.get("entity_type")
        name = request.form.get("name", "").strip()
        nature_of_purchase = request.form.get("nature_of_purchase", "").strip()
        account_type = request.form.get("account_type", "Trade Creditors").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        contact_person = request.form.get("contact_person", "").strip()
        address = request.form.get("address", "").strip()
        ntn = request.form.get("ntn", "").strip() or None
        strn = request.form.get("strn", "").strip() or None
        credit_limit_raw = request.form.get("credit_limit", "0")

        error = None
        if not entity_type or entity_type not in ("company", "person"):
            error = "Please select Company or Person (Individual)."
        elif not name:
            error = "Vendor name is required."
        elif not nature_of_purchase or nature_of_purchase not in ("Supplies", "Services"):
            error = "Please select Supplies or Services."
        elif email and ("@" not in email or "." not in email):
            error = "Please enter a valid email address."
        else:
            try:
                credit_limit = to_decimal(credit_limit_raw)
                if credit_limit < 0:
                    error = "Credit limit cannot be negative."
            except Exception:
                error = "Invalid credit limit value."

        logo_path = None
        if not error:
            file = request.files.get("logo")
            if file and file.filename:
                filename_lower = file.filename.lower()
                allowed_exts = (".png", ".jpg", ".jpeg", ".webp")
                if not filename_lower.endswith(allowed_exts):
                    error = "Invalid file type. Only PNG, JPG, JPEG, and WEBP files are allowed."
                else:
                    file_bytes = file.read()
                    if len(file_bytes) > 2 * 1024 * 1024:
                        error = "File size exceeds 2 MB limit."
                    else:
                        upload_dir = os.path.join(current_app.root_path, "static", "uploads", "vendor_logos", str(org_id))
                        os.makedirs(upload_dir, exist_ok=True)
                        ext = os.path.splitext(file.filename)[1].lower()
                        safe_name = f"{uuid.uuid4().hex}{ext}"
                        file_path = os.path.join(upload_dir, safe_name)
                        with open(file_path, "wb") as f:
                            f.write(file_bytes)
                        logo_path = f"vendor_logos/{org_id}/{safe_name}"

        if error:
            flash(error, "error")
            return render_template("vendors/new_vendor.html", nature_options=NATURE_OF_PURCHASE_OPTIONS, form_data=form_data, vendor=None), 400

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM vendors 
            WHERE organization_id = %s AND lower(name) = lower(%s)
        """, (org_id, name))
        if cursor.fetchone():
            flash("Warning: Another vendor with the same name already exists in this organization.", "warning")
        elif ntn:
            cursor.execute("""
                SELECT 1 FROM vendors 
                WHERE organization_id = %s AND ntn = %s
            """, (org_id, ntn))
            if cursor.fetchone():
                flash("Warning: Another vendor with the same NTN already exists in this organization.", "warning")

        is_company = (entity_type == "company")
        cursor.execute("""
            INSERT INTO vendors
                (organization_id, name, nature_of_purchase, ntn, strn, address, credit_limit,
                 is_company, email, phone, contact_person, logo_path, account_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING vendor_id
        """, (
            org_id, name, nature_of_purchase, ntn, strn, address or None, credit_limit,
            is_company, email or None, phone or None, contact_person or None, logo_path, account_type
        ))
        new_vid = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_detail", vendor_id=new_vid))

    return render_template("vendors/new_vendor.html", nature_options=NATURE_OF_PURCHASE_OPTIONS, form_data=None, vendor=None)


@bp.route("/<int:vendor_id>/edit", methods=["GET", "POST"])
@require_organization
@require_permission("vendors.create")
def edit_vendor(vendor_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, address, credit_limit,
               is_company, email, phone, contact_person, logo_path, account_type
        FROM vendors WHERE vendor_id = %s AND organization_id = %s
    """, (vendor_id, org_id))
    vendor_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not vendor_row:
        abort(404)

    vendor = {
        "vendor_id": vendor_row[0],
        "name": vendor_row[1],
        "nature_of_purchase": vendor_row[2],
        "ntn": vendor_row[3],
        "strn": vendor_row[4],
        "address": vendor_row[5],
        "credit_limit": vendor_row[6],
        "is_company": vendor_row[7],
        "email": vendor_row[8],
        "phone": vendor_row[9],
        "contact_person": vendor_row[10],
        "logo_path": vendor_row[11],
        "account_type": vendor_row[12]
    }

    if request.method == "POST":
        form_data = request.form.to_dict()
        entity_type = request.form.get("entity_type")
        name = request.form.get("name", "").strip()
        nature_of_purchase = request.form.get("nature_of_purchase", "").strip()
        account_type = request.form.get("account_type", "Trade Creditors").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        contact_person = request.form.get("contact_person", "").strip()
        address = request.form.get("address", "").strip()
        ntn = request.form.get("ntn", "").strip() or None
        strn = request.form.get("strn", "").strip() or None
        credit_limit_raw = request.form.get("credit_limit", "0")

        error = None
        if not entity_type or entity_type not in ("company", "person"):
            error = "Please select Company or Person (Individual)."
        elif not name:
            error = "Vendor name is required."
        elif not nature_of_purchase or nature_of_purchase not in ("Supplies", "Services"):
            error = "Please select Supplies or Services."
        elif email and ("@" not in email or "." not in email):
            error = "Please enter a valid email address."
        else:
            try:
                credit_limit = to_decimal(credit_limit_raw)
                if credit_limit < 0:
                    error = "Credit limit cannot be negative."
            except Exception:
                error = "Invalid credit limit value."

        logo_path = vendor["logo_path"]
        if not error:
            file = request.files.get("logo")
            if file and file.filename:
                filename_lower = file.filename.lower()
                allowed_exts = (".png", ".jpg", ".jpeg", ".webp")
                if not filename_lower.endswith(allowed_exts):
                    error = "Invalid file type. Only PNG, JPG, JPEG, and WEBP files are allowed."
                else:
                    file_bytes = file.read()
                    if len(file_bytes) > 2 * 1024 * 1024:
                        error = "File size exceeds 2 MB limit."
                    else:
                        upload_dir = os.path.join(current_app.root_path, "static", "uploads", "vendor_logos", str(org_id))
                        os.makedirs(upload_dir, exist_ok=True)
                        ext = os.path.splitext(file.filename)[1].lower()
                        safe_name = f"{uuid.uuid4().hex}{ext}"
                        file_path = os.path.join(upload_dir, safe_name)
                        with open(file_path, "wb") as f:
                            f.write(file_bytes)
                        logo_path = f"vendor_logos/{org_id}/{safe_name}"

        if error:
            flash(error, "error")
            return render_template("vendors/edit_vendor.html", vendor=vendor, nature_options=NATURE_OF_PURCHASE_OPTIONS, form_data=form_data), 400

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM vendors 
            WHERE organization_id = %s AND lower(name) = lower(%s) AND vendor_id != %s
        """, (org_id, name, vendor_id))
        if cursor.fetchone():
            flash("Warning: Another vendor with the same name already exists in this organization.", "warning")
        elif ntn:
            cursor.execute("""
                SELECT 1 FROM vendors 
                WHERE organization_id = %s AND ntn = %s AND vendor_id != %s
            """, (org_id, ntn, vendor_id))
            if cursor.fetchone():
                flash("Warning: Another vendor with the same NTN already exists in this organization.", "warning")

        is_company = (entity_type == "company")
        cursor.execute("""
            UPDATE vendors
            SET name = %s, nature_of_purchase = %s, ntn = %s, strn = %s, address = %s,
                credit_limit = %s, is_company = %s, email = %s, phone = %s,
                contact_person = %s, logo_path = %s, account_type = %s
            WHERE vendor_id = %s AND organization_id = %s
        """, (
            name, nature_of_purchase, ntn, strn, address or None, credit_limit,
            is_company, email or None, phone or None, contact_person or None,
            logo_path, account_type, vendor_id, org_id
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("vendors.vendor_detail", vendor_id=vendor_id))

    return render_template("vendors/edit_vendor.html", vendor=vendor, nature_options=NATURE_OF_PURCHASE_OPTIONS, form_data=None)


@bp.route("/<int:vendor_id>")
@require_organization
@require_permission("vendors.view")
def vendor_detail(vendor_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_id, name, nature_of_purchase, ntn, strn, address, credit_limit,
               is_company, email, phone, city, state, postal_code, country, cnic, website,
               category, tags, region, territory, is_customer, payment_terms, default_payment_method, notes, is_active,
               contact_person, logo_path, account_type
        FROM vendors WHERE vendor_id = %s AND organization_id = %s
    """, (vendor_id, org_id))
    vendor = cursor.fetchone()
    if not vendor:
        cursor.close()
        conn.close()
        abort(404)

    # Smart-button counts & bills/payments lists
    cursor.execute("SELECT COUNT(*) FROM vendor_bills WHERE vendor_id = %s", (vendor_id,))
    bill_count = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COALESCE(SUM(vbl.total), 0) FROM vendor_bill_lines vbl
        JOIN vendor_bills vb ON vb.vendor_bill_id = vbl.vendor_bill_id
        WHERE vb.vendor_id = %s
    """, (vendor_id,))
    total_billed = cursor.fetchone()[0]

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
@require_permission("vendors.view")
def vendor_bills_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()

    # The search bar (the same one as on every other list): the vendor's bill
    # number, the vendor, the payment reference, a label on a bill line, the
    # purchase account, a status, a month or a range of dates.
    search = read_search(request)
    clauses, params = search_filters(
        search,
        text_columns=["vb.bill_number", "v.name", "vb.payment_reference", "vb.lc_reference"],
        status_column="vb.status",
        date_column="vb.bill_date",
        partner_column="v.name",
    )
    if search.q:
        pattern = "%" + search.q + "%"
        clauses.append("EXISTS (SELECT 1 FROM vendor_bill_lines qbl WHERE qbl.vendor_bill_id = vb.vendor_bill_id "
                       "AND COALESCE(qbl.label, '') ILIKE %s)")
        params.append(pattern)
    if search.account:
        clauses.append("EXISTS (SELECT 1 FROM vendor_bill_lines abl WHERE abl.vendor_bill_id = vb.vendor_bill_id "
                       "AND abl.account_id::text = %s)")
        params.append(search.account)
    accounts = _filter_accounts(cursor, org_id)
    search.description = describe(search, accounts)

    cursor.execute("""
        SELECT vb.vendor_bill_id, v.name, vb.bill_date, vb.due_date, vb.status,
               COALESCE((SELECT SUM(vbl.total) FROM vendor_bill_lines vbl WHERE vbl.vendor_bill_id = vb.vendor_bill_id), 0) AS total,
               vb.bill_number
        FROM vendor_bills vb
        JOIN vendors v ON v.vendor_id = vb.vendor_id
        WHERE vb.organization_id = %s
    """ + and_clauses(clauses) + """
        ORDER BY vb.bill_date DESC, vb.vendor_bill_id DESC
    """, [org_id] + params)
    bills = cursor.fetchall()
    suggestions = {
        "q": suggest(cursor, [
            ("SELECT bill_number FROM vendor_bills WHERE organization_id = %s AND bill_number IS NOT NULL "
             "ORDER BY vendor_bill_id DESC LIMIT 40", (org_id,)),
            ("SELECT name FROM vendors WHERE organization_id = %s ORDER BY vendor_id DESC LIMIT 40", (org_id,)),
        ]),
        "partner": suggest(cursor, [
            ("SELECT name FROM vendors WHERE organization_id = %s ORDER BY name LIMIT 60", (org_id,)),
        ]),
    }
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_bills_list.html", bills=bills, search=search,
                           accounts=accounts, suggestions=suggestions)


def _filter_accounts(cursor, org_id):
    """The accounts a list may be filtered to: (id, 'code · name')."""
    cursor.execute("""
        SELECT account_id, code || ' · ' || name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE ORDER BY code
    """, (org_id,))
    return cursor.fetchall()


@bp.route("/bills/new", methods=["GET", "POST"])
@require_organization
@require_permission("vendors.create")
def new_vendor_bill():
    org_id = current_organization_id()

    if request.method == "POST":
        vendor_id = int(request.form["vendor_id"])
        labels = request.form.getlist("label[]")
        account_ids = request.form.getlist("account_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        totals = request.form.getlist("total[]")
        # A line either brings goods in (and can name the product) or is one of
        # the LC's own charges, which are spread over the goods afterwards.
        line_products = request.form.getlist("product_id[]")
        # One value per row, always sent (the tick box writes into it), so the
        # kinds stay lined up with the lines even when only some are ticked.
        line_kinds = request.form.getlist("line_kind[]")
        landed_cost_basis = normalise_basis(request.form.get("landed_cost_basis", "value"))

        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM vendors WHERE vendor_id = %s AND organization_id = %s", (vendor_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)

        # The company's Trade Payables account, asked for before anything is
        # written: a chart without one is then reported on the form instead of
        # leaving a half-saved bill behind.
        try:
            trade_creditors_id = _trade_payables_account_id(cursor, org_id)
        except ValueError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_bill.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                products=_get_products_for_bill(org_id), basis_options=BASIS_LABELS,
                error=str(error), today=date.today().isoformat(),
            )

        bill_number = request.form.get("bill_number", "").strip() or _generate_bill_number(cursor, org_id)

        cursor.execute("""
            INSERT INTO vendor_bills
                (organization_id, vendor_id, bill_number, bill_date, due_date, receipt_date, accounting_date,
                 payment_terms, payment_reference, recipient_bank, lc_reference, journal_name, status, amount_due,
                 landed_cost_basis)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Draft', 0, %s)
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
            landed_cost_basis,
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
                    (vendor_bill_id, label, account_id, quantity, rate, total, line_order,
                     product_id, line_kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                vendor_bill_id, labels[i].strip() or None, int(account_ids[i]),
                qty, rate, total, i,
                int(line_products[i]) if i < len(line_products) and line_products[i] else None,
                "Landed Cost" if (i < len(line_kinds) and line_kinds[i] == "Landed Cost") else "Goods",
            ))
            je_lines.append({
                "account_id": int(account_ids[i]),
                "label": labels[i].strip() or None,
                "debit": total,
                "credit": to_decimal(0),
            })

        cursor.execute("SELECT name FROM vendors WHERE vendor_id = %s", (vendor_id,))
        vendor_name = cursor.fetchone()[0]
        # (trade_creditors_id was found before the bill was written.)
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
        except (UnbalancedEntryError, ValueError) as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_bill.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                products=_get_products_for_bill(org_id), basis_options=BASIS_LABELS,
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
        products=_get_products_for_bill(org_id), basis_options=BASIS_LABELS,
        auto_number=auto_num, error=None, today=date.today().isoformat(),
    )


@bp.route("/bills/<int:vendor_bill_id>")
@require_organization
@require_permission("vendors.view")
def vendor_bill_detail(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, vb.bill_number, v.name, vb.bill_date, vb.due_date, vb.receipt_date,
               vb.accounting_date, vb.status, vb.journal_header_id, vb.amount_due, vb.paid_amount,
               vb.payment_reference, vb.recipient_bank, vb.lc_reference, vb.journal_name, v.vendor_id,
               vb.landed_cost_basis
        FROM vendor_bills vb JOIN vendors v ON v.vendor_id = vb.vendor_id
        WHERE vb.vendor_bill_id = %s AND vb.organization_id = %s
    """, (vendor_bill_id, org_id))
    bill = cursor.fetchone()
    if not bill:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT vbl.label, coa.code, coa.name, vbl.quantity, vbl.rate, vbl.total,
               COALESCE(vbl.line_kind, 'Goods'), product.name, vbl.product_id
        FROM vendor_bill_lines vbl
        JOIN chart_of_accounts coa ON coa.account_id = vbl.account_id
        LEFT JOIN products product ON product.product_id = vbl.product_id
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

    # The landed cost: the LC's own lines spread over the goods lines, so the
    # bill shows what each unit really cost once freight, duty and clearing are
    # on it. Every figure is already on the bill - nothing new is posted.
    goods_lines, landed_total = [], to_decimal(0)
    for label, code, account_name, quantity, rate, line_total, kind, product_name, product_id in lines:
        if kind == "Landed Cost":
            landed_total += to_decimal(line_total)
        else:
            goods_lines.append({
                "label": label or account_name or "",
                "account": f"{code} {account_name}" if code else (account_name or ""),
                "product_name": product_name or "",
                "product_id": product_id,
                "quantity": quantity,
                "total": line_total,
            })
    basis = normalise_basis(bill[16]) if len(bill) > 16 else "value"
    landed_rows, landed_summary = split_landed_cost(goods_lines, landed_total, basis)

    # The bill's own journal items - the "Journal Items" tab, and the proof that
    # this bill and the ledger say the same thing.
    journal_lines = []
    if bill[8]:
        conn2 = get_conn()
        cursor2 = conn2.cursor()
        cursor2.execute("""
            SELECT coa.code, coa.name, jl.partner_name, jl.label, jl.debit, jl.credit
            FROM journal_lines jl
            JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
            WHERE jl.journal_header_id = %s
            ORDER BY jl.line_order, jl.journal_line_id
        """, (bill[8],))
        journal_lines = cursor2.fetchall()
        cursor2.close()
        conn2.close()

    return render_template(
        "vendors/vendor_bill_detail.html",
        bill=bill, lines=lines, total=total, allocations=allocations,
        journal_lines=journal_lines,
        landed_rows=landed_rows, landed_summary=landed_summary,
        landed_basis_options=BASIS_LABELS,
        menu=build_menu("vendor_bill", vendor_bill_id, status=bill[7],
                        user_id=current_user_id(), organization_id=org_id),
    )


@bp.route("/bills/<int:vendor_bill_id>/print")
@require_organization
@require_permission("vendors.view")
def vendor_bill_print(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vb.vendor_bill_id, vb.bill_number, v.name, v.address, v.ntn, v.strn,
               vb.bill_date, vb.due_date, vb.status, vb.amount_due, vb.paid_amount,
               vb.payment_reference, vb.recipient_bank, vb.lc_reference, org.name, org.default_bill_terms
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
    return render_template("vendors/print/vendor_bill_print.html", bill=bill, lines=lines, total=total)


@bp.route("/bills/<int:vendor_bill_id>/journal-print")
@require_organization
@require_permission("vendors.view")
def vendor_bill_journal_print(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s", (vendor_bill_id, org_id))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row or not row[0]:
        abort(404)
    return render_journal_print(row[0], url_for('vendors.vendor_bill_detail', vendor_bill_id=vendor_bill_id))


@bp.route("/bills/<int:vendor_bill_id>/post", methods=["POST"])
@require_organization
@require_permission("vendors.post")
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
        _remember_landed_cost(cursor, org_id, vendor_bill_id)
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))


def _remember_landed_cost(cursor, org_id, vendor_bill_id):
    """Write each product's landed unit cost after its bill is posted.

    A reference figure only: it shows on the product card so the owner can see
    what the goods really cost once the LC charges are on them. Nothing is
    posted, and On hand is untouched.
    """
    cursor.execute("SELECT COALESCE(landed_cost_basis, 'value') FROM vendor_bills "
                   "WHERE vendor_bill_id = %s AND organization_id = %s", (vendor_bill_id, org_id))
    row = cursor.fetchone()
    if not row:
        return
    basis = normalise_basis(row[0])

    cursor.execute("""
        SELECT COALESCE(line_kind, 'Goods'), product_id, quantity, total
        FROM vendor_bill_lines WHERE vendor_bill_id = %s ORDER BY line_order
    """, (vendor_bill_id,))
    goods, charges = [], to_decimal(0)
    for kind, product_id, quantity, line_total in cursor.fetchall():
        if kind == "Landed Cost":
            charges += to_decimal(line_total)
        else:
            goods.append({"product_id": product_id, "quantity": quantity, "total": line_total})

    for product_id, unit_cost in landed_unit_cost_by_product(goods, charges, basis).items():
        cursor.execute("UPDATE products SET last_landed_cost = %s "
                       "WHERE product_id = %s AND organization_id = %s",
                       (unit_cost, product_id, org_id))


@bp.route("/bills/<int:vendor_bill_id>/edit", methods=["GET", "POST"])
@require_organization
@require_permission("vendors.create")
def edit_vendor_bill(vendor_bill_id):
    """Correct a draft bill, in the same form that writes a new one."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT bill_number, status FROM vendor_bills "
                       "WHERE vendor_bill_id = %s AND organization_id = %s", (vendor_bill_id, org_id))
        found = cursor.fetchone()
        if not found:
            abort(404)
        bill_number, status = found

        def page(**kwargs):
            return render_template(
                "vendors/new_vendor_bill.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                products=_get_products_for_bill(org_id), basis_options=BASIS_LABELS,
                today=date.today().isoformat(),
                draft={"id": vendor_bill_id, "bill_number": bill_number}, **kwargs)

        if (status or "").lower() != "draft":
            flash(f"Bill {bill_number or ''} is posted. Reset it to a draft before changing it.", "error")
            return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))
        cursor.execute("SELECT COUNT(*) FROM vendor_payment_allocations WHERE vendor_bill_id = %s",
                       (vendor_bill_id,))
        if cursor.fetchone()[0]:
            flash("A payment is matched to this bill, so it cannot be changed. Remove the match first.", "error")
            return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))

        if request.method == "POST":
            account_ids = request.form.getlist("account_id[]")
            labels = request.form.getlist("label[]")
            quantities = request.form.getlist("quantity[]")
            rates = request.form.getlist("rate[]")
            totals = request.form.getlist("total[]")
            products = request.form.getlist("product_id[]")
            kinds = request.form.getlist("line_kind[]")

            lines = []
            for index, account_id in enumerate(account_ids):
                if not account_id:
                    continue
                lines.append({
                    "account_id": int(account_id),
                    "label": (labels[index] or "").strip() or None if index < len(labels) else None,
                    "quantity": quantities[index] if index < len(quantities) else 1,
                    "rate": rates[index] if index < len(rates) else 0,
                    "total": totals[index] if index < len(totals) else None,
                    "product_id": int(products[index]) if index < len(products) and products[index] else None,
                    "line_kind": "Landed Cost" if (index < len(kinds) and kinds[index] == "Landed Cost") else "Goods",
                })
            if not lines:
                return page(error="A bill needs at least one line.")

            try:
                trade_payables_id = _trade_payables_account_id(cursor, org_id)
            except ValueError as error:
                return page(error=str(error))

            try:
                replace_vendor_bill(cursor, org_id, vendor_bill_id, header={
                    "vendor_id": int(request.form["vendor_id"]),
                    "bill_date": request.form["bill_date"],
                    "due_date": request.form.get("due_date") or None,
                    "payment_terms": request.form.get("payment_terms", "").strip() or None,
                    "lc_reference": request.form.get("lc_reference", "").strip() or None,
                    "journal_name": request.form.get("journal_name", "Expenses").strip(),
                    "landed_cost_basis": normalise_basis(request.form.get("landed_cost_basis", "value")),
                    "accounting_date": request.form.get("accounting_date") or request.form["bill_date"],
                }, raw_lines=lines, trade_payables_id=trade_payables_id)
                conn.commit()
            except (UnbalancedEntryError, ValueError) as error:
                conn.rollback()
                return page(error=str(error))
            flash(f"Bill {bill_number or ''} is corrected. Post it when you are ready.", "success")
            return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))

        # ---- the form, filled with what the bill says today ------------------
        cursor.execute("""
            SELECT vendor_id, bill_date, due_date, payment_terms, lc_reference, journal_name,
                   landed_cost_basis, accounting_date
            FROM vendor_bills WHERE vendor_bill_id = %s
        """, (vendor_bill_id,))
        (vendor_id, bill_date, due_date, payment_terms, lc_reference, journal_name,
         landed_basis, accounting_date) = cursor.fetchone()
        cursor.execute("""
            SELECT label, product_id, account_id, quantity, rate, total, line_kind
            FROM vendor_bill_lines WHERE vendor_bill_id = %s ORDER BY line_order
        """, (vendor_bill_id,))
        draft_lines = [{
            "label": row[0] or "", "product_id": row[1], "account_id": row[2],
            "quantity": str(row[3]), "rate": str(row[4]), "total": str(row[5]),
            "line_kind": row[6] or "Goods",
        } for row in cursor.fetchall()]
        header = {
            "vendor_id": vendor_id,
            "bill_date": bill_date.isoformat() if bill_date else "",
            "due_date": due_date.isoformat() if due_date else "",
            "payment_terms": payment_terms or "",
            "lc_reference": lc_reference or "",
            "journal_name": journal_name or "Expenses",
            "landed_cost_basis": landed_basis or "value",
            "accounting_date": accounting_date.isoformat() if accounting_date else "",
        }
        return page(draft_header=header, draft_lines=draft_lines)
    finally:
        cursor.close()
        conn.close()


@bp.route("/bills/<int:vendor_bill_id>/reset-to-draft", methods=["POST"])
@require_organization
@require_permission("vendors.post")
def reset_vendor_bill_to_draft_route(vendor_bill_id):
    """Take a posted bill back to a draft so it can be corrected."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        bill_number = reset_vendor_bill_to_draft(cursor, org_id, vendor_bill_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        flash(str(error), "error")
    else:
        flash(f"Bill {bill_number or ''} is a draft again. Correct it, then post it.", "success")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=vendor_bill_id))


@bp.route("/bills/<int:vendor_bill_id>/duplicate", methods=["POST"])
@require_organization
@require_permission("vendors.create")
def duplicate_vendor_bill_route(vendor_bill_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        new_id = duplicate_vendor_bill(cursor, org_id, vendor_bill_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        abort(400, description=str(error))
    finally:
        cursor.close()
        conn.close()
    flash("A copy of the bill is saved as a draft. Give it the vendor's own bill number, then post it.",
          "success")
    return redirect(url_for("vendors.vendor_bill_detail", vendor_bill_id=new_id))


@bp.route("/bills/<int:vendor_bill_id>/delete", methods=["POST"])
@require_organization
@require_permission("vendors.create")
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
@require_permission("vendors.view")
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
@require_permission("vendors.create")
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

        try:
            trade_creditors_id = _trade_payables_account_id(cursor, org_id)
        except ValueError as err:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_credit_note.html",
                vendors=_get_vendors(org_id), accounts=_get_accounts(org_id),
                error=str(err), today=date.today().isoformat(),
            )

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
        except (UnbalancedEntryError, ValueError) as err:
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
@require_permission("vendors.view")
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
@require_permission("vendors.post")
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
@require_permission("vendors.view")
def vendor_payments_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    search = read_search(request)
    clauses, params = search_filters(
        search,
        text_columns=["vp.voucher_number", "vp.memo", "v.name", "vp.payment_method",
                      "vp.cheque_number", "vp.lc_reference"],
        status_column="vp.status",
        date_column="vp.payment_date",
        partner_column="v.name",
    )
    search.description = describe(search)
    cursor.execute("""
        SELECT vp.vendor_payment_id, v.name, vp.amount, vp.payment_date,
               vp.payment_method, vp.has_wht, vp.status, vp.voucher_number
        FROM vendor_payments vp JOIN vendors v ON v.vendor_id = vp.vendor_id
        WHERE vp.organization_id = %s
    """ + and_clauses(clauses) + """
        ORDER BY vp.payment_date DESC, vp.vendor_payment_id DESC
    """, [org_id] + params)
    payments = cursor.fetchall()
    suggestions = {
        "q": suggest(cursor, [
            ("SELECT voucher_number FROM vendor_payments WHERE organization_id = %s AND voucher_number IS NOT NULL "
             "ORDER BY vendor_payment_id DESC LIMIT 40", (org_id,)),
            ("SELECT name FROM vendors WHERE organization_id = %s ORDER BY vendor_id DESC LIMIT 40", (org_id,)),
        ]),
        "partner": suggest(cursor, [
            ("SELECT name FROM vendors WHERE organization_id = %s ORDER BY name LIMIT 60", (org_id,)),
        ]),
    }
    cursor.close()
    conn.close()
    return render_template("vendors/vendor_payments_list.html", payments=payments, search=search,
                           suggestions=suggestions)


@bp.route("/payments/new", methods=["GET", "POST"])
@require_organization
@require_permission("vendors.post")
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

        try:
            trade_creditors_id = _trade_payables_account_id(cursor, org_id)
        except ValueError as err:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "vendors/new_vendor_payment.html",
                vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(err), today=date.today().isoformat(),
            )

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
                        # A bill cannot be paid past what it still owes - the rest
                        # would be money handed over with nothing behind it.
                        cursor.execute("""
                            SELECT bill_number, amount_due - COALESCE(paid_amount, 0)
                            FROM vendor_bills WHERE vendor_bill_id = %s AND organization_id = %s
                            FOR UPDATE
                        """, (b_id, org_id))
                        owing = cursor.fetchone()
                        if not owing:
                            raise ValueError("That bill is not one of this company's.")
                        if a_amt > to_decimal(owing[1]):
                            raise ValueError(
                                f"Bill {owing[0]} still owes {money_round(owing[1])}; "
                                f"a payment of {money_round(a_amt)} cannot be matched to it.")
                        # Compared against the gross: withholding tax is still
                        # money that settles the bill - it goes to the tax office
                        # instead of to the vendor, not nowhere.
                        if a_amt > base_amount:
                            raise ValueError(
                                f"A payment of {money_round(base_amount)} cannot settle {money_round(a_amt)} "
                                "of a bill - raise the amount first.")
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

    # ?bill_id= comes from a bill's own "Pay" button: the vendor, the amount and
    # the match are filled in from the bill, so paying it is one step and the two
    # documents are tied together as they are created.
    paying_bill = None
    bill_id = request.args.get("bill_id", type=int)
    if bill_id:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vb.vendor_bill_id, vb.bill_number, vb.vendor_id, v.name,
                   vb.amount_due, COALESCE(vb.paid_amount, 0), vb.status
            FROM vendor_bills vb JOIN vendors v ON v.vendor_id = vb.vendor_id
            WHERE vb.vendor_bill_id = %s AND vb.organization_id = %s
        """, (bill_id, org_id))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row and (row[6] or "").lower() != "draft":
            paying_bill = {
                "id": row[0], "bill_number": row[1], "vendor_id": row[2], "vendor_name": row[3],
                "open_amount": to_decimal(row[4] or 0), "paid": to_decimal(row[5] or 0),
            }

    conn = get_conn()
    cursor = conn.cursor()
    auto_bpv = _generate_voucher_number(cursor, org_id, True)
    cursor.close()
    conn.close()

    return render_template(
        "vendors/new_vendor_payment.html",
        vendors=_get_vendors(org_id), accounts=_get_bank_accounts(org_id),
        tax_rates=get_active_tax_rates(org_id), paying_bill=paying_bill,
        auto_voucher=auto_bpv, error=None, today=date.today().isoformat(),
    )


@bp.route("/payments/<int:vendor_payment_id>")
@require_organization
@require_permission("vendors.view")
def vendor_payment_detail(vendor_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vp.vendor_payment_id, vp.voucher_number, v.name, vp.amount, vp.payment_date, vp.memo,
               coa.name, vp.payment_method, vp.cheque_number, vp.cheque_date,
               vp.has_wht, vp.wht_rate, vp.wht_amount, vp.journal_header_id, vp.status,
               vp.lc_reference, vp.vendor_bank_account, vp.vendor_id
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

    trail = []
    if payment:
        trail = [{"icon": "i-vendor", "label": payment[2], "meta": "Vendor",
                  "url": url_for("vendors.vendor_detail", vendor_id=payment[17])}]
        if payment[13]:
            trail.append({"icon": "i-journal", "label": "Journal entry", "meta": "#%s" % payment[13],
                          "url": url_for("accounting.journal_entry_detail",
                                         journal_header_id=payment[13])})
        for row in allocations:
            trail.append({"icon": "i-bill", "label": row[1] or ("Bill #%s" % row[0]),
                          "meta": "matched · {:,.2f}".format(to_decimal(row[3] or 0)),
                          "url": url_for("vendors.vendor_bill_detail", vendor_bill_id=row[0])})
    cursor.close()
    conn.close()
    if not payment:
        abort(404)
    return render_template("vendors/vendor_payment_detail.html", payment=payment, allocations=allocations,
                           trail=trail,
                           menu=build_menu("vendor_payment", vendor_payment_id, status=payment[14],
                                           user_id=current_user_id(), organization_id=org_id,
                                           extra={"has_allocations": bool(allocations)}))


@bp.route("/payments/<int:vendor_payment_id>/delete", methods=["POST"])
@require_organization
@require_permission("vendors.post")
def delete_vendor_payment(vendor_payment_id):
    """Delete a payment that settles no bill (a payment matched to a bill keeps it)."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT voucher_number, journal_header_id FROM vendor_payments "
                       "WHERE vendor_payment_id = %s AND organization_id = %s", (vendor_payment_id, org_id))
        row = cursor.fetchone()
        if not row:
            abort(404)
        voucher_number, journal_header_id = row
        cursor.execute("SELECT COUNT(*) FROM vendor_payment_allocations WHERE vendor_payment_id = %s",
                       (vendor_payment_id,))
        if cursor.fetchone()[0]:
            conn.rollback()
            flash(f"{voucher_number or 'That payment'} is matched to a bill, so it cannot be deleted.", "error")
            return redirect(url_for("vendors.vendor_payment_detail", vendor_payment_id=vendor_payment_id))
        if journal_header_id:
            cursor.execute("UPDATE vendor_payments SET journal_header_id = NULL WHERE vendor_payment_id = %s",
                           (vendor_payment_id,))
            cursor.execute("DELETE FROM journal_lines WHERE journal_header_id = %s", (journal_header_id,))
            cursor.execute("DELETE FROM journal_headers WHERE journal_header_id = %s", (journal_header_id,))
        cursor.execute("DELETE FROM vendor_payments WHERE vendor_payment_id = %s", (vendor_payment_id,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    flash(f"{voucher_number or 'The payment'} was deleted, and its journal entry with it.", "success")
    return redirect(url_for("vendors.vendor_payments_list"))


def render_journal_print(journal_header_id, back_url):
    """The entry a vendor document wrote, on one page, in the reference system's shape.

    The columns are the ones this ledger actually keeps: a reference, a date and
    a journal name on the header (there is no voucher number or memo of its own -
    the entry IS the voucher), and code, name, partner, label, debit, credit on
    each line. Anything the ledger does not hold is not printed as though it did.
    """
    from accounting.services import _source_words
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT journal_header_id, reference, accounting_date, journal_name, status, source_type
        FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s
    """, (journal_header_id, org_id))
    header = cursor.fetchone()
    if not header:
        cursor.close()
        conn.close()
        abort(404)
    je = (header[0], header[1], header[2], header[3], header[4],
          _source_words(header[5]).title() if header[5] else "Journal entry")
    cursor.execute("""
        SELECT coa.code, coa.name, jl.partner_name, jl.label, jl.debit, jl.credit
        FROM journal_lines jl
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jl.journal_header_id = %s
        ORDER BY jl.line_order, jl.journal_line_id
    """, (journal_header_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total_debit = sum((to_decimal(l[4]) for l in lines), to_decimal(0))
    total_credit = sum((to_decimal(l[5]) for l in lines), to_decimal(0))
    return render_template(
        "vendors/print/journal_entry_print.html",
        je=je, lines=lines, total_debit=total_debit, total_credit=total_credit, back_url=back_url
    )


@bp.route("/payments/<int:vendor_payment_id>/print")
@require_organization
@require_permission("vendors.view")
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
        SELECT vb.vendor_bill_id, vb.bill_number, vb.bill_date, vpa.allocated_amount
        FROM vendor_payment_allocations vpa
        JOIN vendor_bills vb ON vb.vendor_bill_id = vpa.vendor_bill_id
        WHERE vpa.vendor_payment_id = %s
    """, (vendor_payment_id,))
    allocations = cursor.fetchall()
    cursor.close()
    conn.close()

    net_paid = to_decimal(payment[5]) - to_decimal(payment[14] or 0)
    return render_template("vendors/print/vendor_payment_print.html", payment=payment, allocations=allocations, net_paid=net_paid)


@bp.route("/payments/<int:vendor_payment_id>/journal-print")
@require_organization
@require_permission("vendors.view")
def vendor_payment_journal_print(vendor_payment_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM vendor_payments WHERE vendor_payment_id = %s AND organization_id = %s", (vendor_payment_id, org_id))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row or not row[0]:
        abort(404)
    return render_journal_print(row[0], url_for('vendors.vendor_payment_detail', vendor_payment_id=vendor_payment_id))


@bp.route("/credit-notes/<int:vendor_credit_note_id>/print")
@require_organization
@require_permission("vendors.view")
def vendor_credit_note_print(vendor_credit_note_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cn.vendor_credit_note_id, cn.credit_note_number, v.name, v.address,
               cn.status, cn.credit_date, vb.bill_number
        FROM vendor_credit_notes cn
        JOIN vendors v ON v.vendor_id = cn.vendor_id
        LEFT JOIN vendor_bills vb ON vb.vendor_bill_id = cn.original_bill_id
        WHERE cn.vendor_credit_note_id = %s AND cn.organization_id = %s
    """, (vendor_credit_note_id, org_id))
    cn = cursor.fetchone()
    if not cn:
        cursor.close()
        conn.close()
        abort(404)
    cursor.execute("""
        SELECT cnl.label, coa.code, coa.name, cnl.total
        FROM vendor_credit_note_lines cnl
        JOIN chart_of_accounts coa ON coa.account_id = cnl.account_id
        WHERE cnl.vendor_credit_note_id = %s ORDER BY cnl.line_order
    """, (vendor_credit_note_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()
    total = sum((to_decimal(l[3]) for l in lines), to_decimal(0))
    return render_template("vendors/print/vendor_credit_note_print.html", cn=cn, lines=lines, total=total)


@bp.route("/credit-notes/<int:vendor_credit_note_id>/journal-print")
@require_organization
@require_permission("vendors.view")
def vendor_credit_note_journal_print(vendor_credit_note_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT journal_header_id FROM vendor_credit_notes WHERE vendor_credit_note_id = %s AND organization_id = %s", (vendor_credit_note_id, org_id))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row or not row[0]:
        abort(404)
    return render_journal_print(row[0], url_for('vendors.vendor_credit_note_detail', vendor_credit_note_id=vendor_credit_note_id))


@bp.route("/batch-payments/<int:batch_payment_id>/print")
@require_organization
@require_permission("vendors.view")
def batch_vendor_payment_print(batch_payment_id):
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
        SELECT v.name, bpl.memo, vp.voucher_number, bpl.amount
        FROM batch_vendor_payment_lines bpl
        JOIN vendors v ON v.vendor_id = bpl.vendor_id
        LEFT JOIN vendor_payments vp ON vp.vendor_payment_id = bpl.vendor_payment_id
        WHERE bpl.batch_payment_id = %s
    """, (batch_payment_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("vendors/print/batch_vendor_payment_print.html", batch=batch, lines=lines)


# ---------------------------------------------------------------------
# Phase 5: Batch Vendor Payments
# ---------------------------------------------------------------------

@bp.route("/batch-payments")
@require_organization
@require_permission("vendors.view")
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
@require_permission("vendors.post")
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

            try:
                trade_creditors_id = _trade_payables_account_id(cursor, org_id)
                je_lines = [
                    {"account_id": trade_creditors_id, "partner_name": v_name, "label": f"Batch payment {batch_number} to {v_name}", "debit": amt, "credit": 0},
                    {"account_id": paid_from_account_id, "partner_name": v_name, "label": f"Payment via batch {batch_number}", "debit": 0, "credit": amt},
                ]
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
@require_permission("vendors.view")
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
@require_permission("vendors.view")
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
@require_permission("vendors.view")
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
@require_permission("vendors.view")
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
