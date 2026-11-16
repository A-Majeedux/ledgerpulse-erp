from datetime import date
from decimal import Decimal
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import create_journal_entry, UnbalancedEntryError
from wht.routes import get_active_tax_rates
from core.money import to_decimal, money_round
from customers.services import (
    AGEING_BUCKETS, PROVINCE_OPTIONS, REGISTRATION_OPTIONS,
    account_statement, apply_receipt_to_invoices, bucket_totals, credit_status,
    customer_ageing, customer_open_invoices as open_invoices_for_customer,
    open_invoices, receipt_allocations,
    CONTACT_TEXT_FIELDS, LIST_COLUMNS, NAME_MAX_LENGTH, NATURE_OF_SUPPLY_OPTIONS, TEXT_FIELDS,
    ValidationError, blank_contact_form, blank_form, clean_contact_form, clean_customer_form,
    clean_search, contact_form_as_typed, contact_form_from_contact, count_customers, create_contact,
    create_customer, credit_position, customer_ledger, customer_totals, delete_contact,
    field_suggestions, form_as_typed, form_from_customer, get_contact, get_customer,
    list_contacts, list_customer_invoices, list_customer_receipts, list_customers, list_row,
    make_primary_contact, name_lock_reason, save_notes, tag_list, update_contact, update_customer,
    PAYMENT_METHOD_OPTIONS, PAYMENT_TYPE_OPTIONS, give_receipt_a_voucher_number,
    ADDRESS_TYPE_OPTIONS, list_vendors_for_link, linked_vendor, partner_ledger, amount_in_words,
    clean_date as clean_receipt_date,
)

bp = Blueprint("customers", __name__, url_prefix="/customers")


def _field_limits(fields=TEXT_FIELDS):
    """The longest text each box of a card accepts, for the maxlength attribute."""
    return dict({field: max_length for field, _label, max_length in fields}, name=NAME_MAX_LENGTH)


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


def _journal_names(org_id):
    """The journal names already used in this company, for the Journal box."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT journal_name FROM journal_headers
        WHERE organization_id = %s AND journal_name IS NOT NULL AND journal_name <> ''
        ORDER BY journal_name
    """, (org_id,))
    names = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return names


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
#
# These screens only read and write the customers table, the people kept
# under a customer (customer_contacts), and the three figures at the top of
# the card, which are READ from the invoices and receipts. The rules (what
# may be typed, the ID, when a name is locked, what the figures mean) live in
# customers/services.py; nothing here touches a journal entry.
# ---------------------------------------------------------------------

def _customer_card(template, cursor, org_id, **context):
    """Show one of the customer card screens with everything they need."""
    return render_template(
        template,
        nature_options=NATURE_OF_SUPPLY_OPTIONS,
        limits=_field_limits(),
        contact_limits=_field_limits(CONTACT_TEXT_FIELDS),
        suggestions=field_suggestions(cursor, org_id),
        tag_list=tag_list,          # so the card can show the tags one by one
        registration_options=REGISTRATION_OPTIONS,
        province_options=PROVINCE_OPTIONS,
        **context,
    )


# The tabs under the card, in the order they are drawn. "customer" is the
# card itself, which is always in front of him; the rest are the tabs.
OPEN_TABS = ("contacts", "sales", "accounting", "notes", "credit")


def _open_tab(wanted):
    """Which tab starts open. Anything unknown opens the card's own body."""
    wanted = (wanted or "").strip().lower()
    return wanted if wanted in OPEN_TABS else "contacts"


def _show_edit_card(cursor, org_id, customer, *, values=None, problems=None,
                    contact_form=None, contact_problems=None, editing_contact=None,
                    new_contact_form=None, new_contact_problems=None, open_tab="contacts",
                    notes_text=None, notes_problems=None):
    """Draw the Edit card: the totals, the card itself, and the five tabs."""
    totals = customer_totals(cursor, org_id, customer["customer_id"])
    invoices = list_customer_invoices(cursor, org_id, customer["customer_id"])
    receipts = list_customer_receipts(cursor, org_id, customer["customer_id"])
    return _customer_card(
        "customers/edit_customer.html", cursor, org_id,
        customer=customer,
        values=form_from_customer(customer) if values is None else values,
        problems=problems or [],
        name_locked=name_lock_reason(cursor, org_id, customer),
        totals=totals,
        # the tabs under the card
        contacts=list_contacts(cursor, org_id, customer["customer_id"]),
        invoices=invoices,
        receipts=receipts,
        ledger=customer_ledger(cursor, org_id, customer),
        credit=credit_position(totals, customer.get("credit_limit")),
        open_invoices=open_invoices_for_customer(cursor, org_id, customer["customer_id"]),
        notes_text=(customer.get("notes") or "") if notes_text is None else notes_text,
        notes_problems=notes_problems or [],
        # the contact being changed in place
        contact_form=contact_form,
        contact_problems=contact_problems or [],
        editing_contact=editing_contact,
        # the "Add a contact" box at the bottom
        new_contact_form=blank_contact_form() if new_contact_form is None else new_contact_form,
        new_contact_problems=new_contact_problems or [],
        open_tab=_open_tab(open_tab),
        # the vendor behind this customer, for the Purchases figure
        vendors=list_vendors_for_link(cursor, org_id),
        linked_vendor=linked_vendor(cursor, org_id, totals.get("vendor_id")),
        address_types=ADDRESS_TYPE_OPTIONS,
    )


@bp.route("/")
@require_organization
def customers_list():
    org_id = current_organization_id()
    search = clean_search(request.args.get("q"))
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customers = list_customers(cursor, org_id, search)
        total = count_customers(cursor, org_id)
    finally:
        cursor.close()
        conn.close()
    return render_template(
        "customers/customers_list.html",
        customers=[list_row(customer) for customer in customers],
        columns=LIST_COLUMNS, search=search, total=total,
    )


@bp.route("/new", methods=["GET", "POST"])
@require_organization
def new_customer():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        values, problems = blank_form(), []
        if request.method == "POST":
            data, problems = clean_customer_form(request.form)
            if not problems:
                try:
                    create_customer(cursor, org_id, data)
                    conn.commit()
                    return redirect(url_for("customers.customers_list"))
                except ValidationError as error:
                    conn.rollback()
                    problems = [str(error)]
            # Refused: show the card again with everything that was typed.
            values = form_as_typed(request.form)
        return _customer_card("customers/new_customer.html", cursor, org_id, values=values, problems=problems)
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>")
@require_organization
def customer_view(customer_id):
    """The customer's own page, for reading.

    The Edit card is for changing things; this is the page to open when you
    only want to look - and the page to show someone who should not be editing
    anything. Everything on it is read from the documents; nothing is written.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = get_customer(cursor, org_id, customer_id)
        if customer is None:
            abort(404)
        totals = customer_totals(cursor, org_id, customer_id)
        open_rows = open_invoices_for_customer(cursor, org_id, customer_id)
        return render_template(
            "customers/customer_view.html",
            customer=customer,
            totals=totals,
            credit=credit_position(totals, customer.get("credit_limit")),
            invoices=list_customer_invoices(cursor, org_id, customer_id),
            receipts=list_customer_receipts(cursor, org_id, customer_id),
            contacts=list_contacts(cursor, org_id, customer_id),
            ledger=customer_ledger(cursor, org_id, customer),
            open_invoices=open_rows,
            buckets=bucket_totals(open_rows),
            linked_vendor=linked_vendor(cursor, org_id, totals.get("vendor_id")),
            tag_list=tag_list,
        )
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>/statement")
@require_organization
def customer_statement(customer_id):
    """The Statement of Account - the sheet that goes to the customer.

    Same printed frame as the voucher and the invoice (A4, company head, four
    signature boxes). A date range can be given (?start_date=&end_date=); with
    none it is everything to today.
    """
    org_id = current_organization_id()
    try:
        start_date = clean_receipt_date(request.args.get("start_date")) if (request.args.get("start_date") or "").strip() else None
        end_date = clean_receipt_date(request.args.get("end_date")) if (request.args.get("end_date") or "").strip() else None
    except ValueError:
        start_date = end_date = None

    conn = get_conn()
    cursor = conn.cursor()
    try:
        statement = account_statement(cursor, org_id, customer_id, start_date, end_date)
        if statement is None:
            abort(404)
    finally:
        cursor.close()
        conn.close()
    return render_template("customers/print/customer_statement.html", statement=statement)


@bp.route("/<int:customer_id>/terms")
@require_organization
def customer_terms(customer_id):
    """What the invoice and receipt screens need to know about one customer.

    Read by the two forms when a customer is chosen: how long he is given to
    pay (so the invoice's due date fills itself), whether he deducts
    withholding tax and at what rate (so a receipt offers it), and the room
    left on his credit limit.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = get_customer(cursor, org_id, customer_id)
        if customer is None:
            abort(404)
        credit = credit_status(cursor, org_id, customer_id)
    finally:
        cursor.close()
        conn.close()
    return jsonify({
        "id": customer_id,
        "name": customer["name"],
        "payment_terms_days": customer.get("payment_terms_days"),
        "withholds_wht": bool(customer.get("withholds_wht")),
        "default_wht_rate": str(customer["default_wht_rate"]) if customer.get("default_wht_rate") is not None else "",
        "credit_limit": str(credit["limit"]),
        "credit_used": str(credit["used"]),
        "credit_room": str(credit["room"]),
        "credit_has_limit": credit["has_limit"],
        "over_limit": credit["over"],
    })


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@require_organization
def edit_customer(customer_id):
    """
    Change a customer's card. The ID never changes. The name can be changed
    only while the customer has no entries (see customers/services.py).

    An INACTIVE customer keeps its history and its balance; it only stops
    being offered when a new receipt, invoice or journal entry is entered.

    `?contact=<id>` opens the Contacts tab with that contact ready to change.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = get_customer(cursor, org_id, customer_id)
        if customer is None:            # no such customer in THIS organization
            abort(404)

        if request.method == "POST":
            data, problems = clean_customer_form(request.form, editing=True)
            if not problems:
                try:
                    update_customer(cursor, org_id, customer_id, data)
                    conn.commit()
                    return redirect(url_for("customers.customers_list"))
                except ValidationError as error:
                    conn.rollback()
                    problems = [str(error)]
            return _show_edit_card(cursor, org_id, customer,
                                   values=form_as_typed(request.form, base=form_from_customer(customer)),
                                   problems=problems)

        editing_contact, contact_form = None, None
        wanted = (request.args.get("contact") or "").strip()
        if wanted.isascii() and wanted.isdigit():
            contact = get_contact(cursor, org_id, customer_id, int(wanted))
            if contact is not None:
                editing_contact = contact["contact_id"]
                contact_form = contact_form_from_contact(contact)
        return _show_edit_card(
            cursor, org_id, customer,
            editing_contact=editing_contact, contact_form=contact_form,
            open_tab="contacts" if editing_contact is not None else (request.args.get("tab") or "contacts"),
        )
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------
# The Notes tab of the card
# ---------------------------------------------------------------------

@bp.route("/<int:customer_id>/vendor", methods=["POST"])
@require_organization
def link_customer_vendor(customer_id):
    """Link this customer to the vendor he is on the buying side, or unlink him.

    Only the Purchases figure of the card reads it; nothing else about the
    customer, his invoices or his receipts changes.
    """
    org_id = current_organization_id()
    vendor_id = (request.form.get("vendor_id") or "").strip() or None
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _customer_or_404(cursor, org_id, customer_id)
        if vendor_id:
            if linked_vendor(cursor, org_id, int(vendor_id)) is None:
                abort(400)
            vendor_id = int(vendor_id)
        cursor.execute(
            "UPDATE customers SET vendor_id = %s WHERE customer_id = %s AND organization_id = %s",
            (vendor_id, customer_id, org_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="sales"))


@bp.route("/<int:customer_id>/notes", methods=["POST"])
@require_organization
def save_customer_notes(customer_id):
    """Write what he typed in the Notes tab. Nothing else on the card changes."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = _customer_or_404(cursor, org_id, customer_id)
        typed = request.form.get("notes") or ""
        try:
            save_notes(cursor, org_id, customer_id, typed)
        except ValidationError as error:
            return _show_edit_card(cursor, org_id, customer, open_tab="notes",
                                   notes_text=typed, notes_problems=[str(error)])
        conn.commit()
        return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="notes"))
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------
# The contacts kept under a customer
# ---------------------------------------------------------------------

def _customer_or_404(cursor, org_id, customer_id):
    customer = get_customer(cursor, org_id, customer_id)
    if customer is None:
        abort(404)
    return customer


@bp.route("/<int:customer_id>/contacts", methods=["POST"])
@require_organization
def add_customer_contact(customer_id):
    """Add one contact card. The first contact of a customer becomes primary."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = _customer_or_404(cursor, org_id, customer_id)
        data, problems = clean_contact_form(request.form)
        if not problems:
            try:
                create_contact(cursor, org_id, customer_id, data)
                conn.commit()
                return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="contacts"))
            except ValidationError as error:
                conn.rollback()
                problems = [str(error)]
        return _show_edit_card(cursor, org_id, customer, open_tab="contacts",
                               new_contact_form=contact_form_as_typed(request.form),
                               new_contact_problems=problems)
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>/contacts/<int:contact_id>", methods=["POST"])
@require_organization
def edit_customer_contact(customer_id, contact_id):
    """Save one contact card. Which contact is primary is changed by the two actions below."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = _customer_or_404(cursor, org_id, customer_id)
        data, problems = clean_contact_form(request.form)
        if not problems:
            try:
                update_contact(cursor, org_id, customer_id, contact_id, data)
                conn.commit()
                return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="contacts"))
            except ValidationError as error:
                conn.rollback()
                problems = [str(error)]
        return _show_edit_card(cursor, org_id, customer, open_tab="contacts",
                               editing_contact=contact_id,
                               contact_form=contact_form_as_typed(request.form),
                               contact_problems=problems)
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>/contacts/<int:contact_id>/primary", methods=["POST"])
@require_organization
def make_customer_contact_primary(customer_id, contact_id):
    """Make this contact the primary one for the customer."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _customer_or_404(cursor, org_id, customer_id)
        try:
            make_primary_contact(cursor, org_id, customer_id, contact_id)
            conn.commit()
        except ValidationError:
            conn.rollback()
        return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="contacts"))
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>/contacts/<int:contact_id>/delete", methods=["POST"])
@require_organization
def delete_customer_contact(customer_id, contact_id):
    """Remove a contact. If it was the primary one, the oldest left takes over."""
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _customer_or_404(cursor, org_id, customer_id)
        try:
            delete_contact(cursor, org_id, customer_id, contact_id)
            conn.commit()
        except ValidationError:
            conn.rollback()
        return redirect(url_for("customers.edit_customer", customer_id=customer_id, tab="contacts"))
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------
# Customer Receipts (on-account only for now - see module docstring)
# ---------------------------------------------------------------------

@bp.route("/receipts")
@require_organization
def customer_receipts_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    # "Receipts" and "Cheques" are the same list, the second one only showing
    # the money that came in by cheque (the Cheques tab of his screen).
    tab = (request.args.get("tab") or "receipts").strip().lower()
    sql = """
        SELECT cr.customer_receipt_id, c.name, cr.amount, cr.receipt_date, cr.has_wht,
               cr.voucher_no, cr.payment_method, cr.payment_type, cr.cheque_no, cr.cheque_date
        FROM customer_receipts cr JOIN customers c ON c.customer_id = cr.customer_id
        WHERE cr.organization_id = %s
    """
    params = [org_id]
    if tab == "cheques":
        sql += " AND cr.payment_method = 'Cheque'"
    sql += " ORDER BY cr.receipt_date DESC, cr.customer_receipt_id DESC"
    cursor.execute(sql, tuple(params))
    receipts = cursor.fetchall()
    cursor.execute(
        "SELECT COUNT(*) FROM customer_receipts WHERE organization_id = %s AND payment_method = 'Cheque'",
        (org_id,))
    cheque_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return render_template(
        "customers/customer_receipts_list.html",
        receipts=receipts,
        tab=tab,
        cheque_count=cheque_count,
    )


@bp.route("/reports/partner-ledger")
@require_organization
def customer_partner_ledger():
    """Reports > Customer Partner Ledger.

    One customer at a time: every posted invoice (he owes more) and every
    receipt (he owes less), oldest first, with a running balance. Read-only -
    it looks at the documents that already exist and writes nothing.
    """
    org_id = current_organization_id()
    customer_id = request.args.get("customer_id", type=int)
    start_text = (request.args.get("start_date") or "").strip()
    end_text = (request.args.get("end_date") or "").strip()

    error = None
    start_date = end_date = None
    try:
        start_date = clean_receipt_date(start_text) if start_text else None
        end_date = clean_receipt_date(end_text) if end_text else None
    except ValueError as problem:
        error = str(problem)

    ledger = None
    if customer_id and not error:
        conn = get_conn()
        cursor = conn.cursor()
        try:
            ledger = partner_ledger(cursor, org_id, customer_id, start_date, end_date)
        finally:
            cursor.close()
            conn.close()
        if ledger is None:
            abort(404)

    return render_template(
        "customers/customer_partner_ledger.html",
        customers=_get_customers(org_id),
        selected_customer=customer_id,
        ledger=ledger,
        start_date=start_text,
        end_date=end_text,
        error=error,
    )


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

        # The boxes his own receipt screen has above the amount. Anything that
        # is not one of the offered answers falls back to the usual one, so an
        # old screen (or an old bookmark) keeps saving receipts exactly as
        # before; a cheque without its number or date is refused instead of
        # being saved half-filled.
        payment_type = (request.form.get("payment_type") or "").strip() or "Customer Payment"
        if payment_type not in PAYMENT_TYPE_OPTIONS:
            payment_type = "Customer Payment"
        payment_method = (request.form.get("payment_method") or "").strip() or "Cash"
        if payment_method not in PAYMENT_METHOD_OPTIONS:
            payment_method = "Cash"
        journal_name = (request.form.get("journal_name") or "").strip() or "Customer Receipts"
        cheque_no = (request.form.get("cheque_no") or "").strip() or None
        cheque_bank = (request.form.get("cheque_bank") or "").strip() or None
        try:
            cheque_date = clean_receipt_date(request.form.get("cheque_date")) if payment_method == "Cheque" else None
        except ValueError:
            cheque_date = None
        if payment_method == "Cheque" and not (cheque_no and cheque_date):
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "customers/new_customer_receipt.html",
                customers=_get_customers(org_id), accounts=_get_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                payment_types=PAYMENT_TYPE_OPTIONS, payment_methods=PAYMENT_METHOD_OPTIONS,
                journals=_journal_names(org_id),
                error="A cheque needs its number and its date. Fill both in, or change Payment Method.",
                today=date.today().isoformat(),
            )
        if payment_method != "Cheque":
            cheque_no = cheque_bank = cheque_date = None

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
                journal_name=journal_name,
                lines=je_lines, source_type="CustomerReceipt", auto_post=True,
            )

            cursor.execute("""
                INSERT INTO customer_receipts
                    (organization_id, customer_id, amount, receipt_date, memo,
                     deposit_to_account_id, has_wht, wht_rate, wht_amount, journal_header_id,
                     payment_type, journal_name, payment_method,
                     cheque_no, cheque_bank, cheque_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING customer_receipt_id
            """, (
                org_id, customer_id, gross_amount, request.form["receipt_date"],
                request.form.get("memo", "").strip() or None, deposit_to_account_id,
                has_wht, wht_rate, wht_amount, journal_header_id,
                payment_type, journal_name, payment_method,
                cheque_no, cheque_bank, cheque_date,
            ))
            customer_receipt_id = cursor.fetchone()[0]
            # The voucher number his screen shows - given here, and never
            # changed afterwards (customers/services.py).
            give_receipt_a_voucher_number(
                cursor, org_id, customer_receipt_id, request.form["receipt_date"])

            # Which invoices this money settles. Optional: a receipt that
            # settles nothing is still a real receipt (an unapplied credit),
            # and the invoices it names get their still-due figure written
            # back from the allocations themselves.
            invoice_ids = request.form.getlist("invoice_id[]")
            alloc_amounts = request.form.getlist("alloc_amount[]")
            wanted = []
            for index, invoice_id in enumerate(invoice_ids):
                amount = alloc_amounts[index] if index < len(alloc_amounts) else ""
                if invoice_id and (amount or "").strip():
                    wanted.append((invoice_id, amount))
            apply_receipt_to_invoices(
                cursor, org_id, customer_receipt_id, customer_id, gross_amount, wanted)

            conn.commit()
        except (UnbalancedEntryError, ValueError) as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "customers/new_customer_receipt.html",
                customers=_get_customers(org_id), accounts=_get_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                payment_types=PAYMENT_TYPE_OPTIONS, payment_methods=PAYMENT_METHOD_OPTIONS,
                journals=_journal_names(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("customers.customer_receipt_detail", customer_receipt_id=customer_receipt_id))

    return render_template(
        "customers/new_customer_receipt.html",
        customers=_get_customers(org_id), accounts=_get_accounts(org_id),
        tax_rates=get_active_tax_rates(org_id),
        payment_types=PAYMENT_TYPE_OPTIONS, payment_methods=PAYMENT_METHOD_OPTIONS,
                journals=_journal_names(org_id),
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
               coa.name, cr.has_wht, cr.wht_rate, cr.wht_amount, cr.journal_header_id,
               cr.voucher_no, cr.payment_type, cr.journal_name, cr.payment_method,
               cr.cheque_no, cr.cheque_bank, cr.cheque_date
        FROM customer_receipts cr
        JOIN customers c ON c.customer_id = cr.customer_id
        JOIN chart_of_accounts coa ON coa.account_id = cr.deposit_to_account_id
        WHERE cr.customer_receipt_id = %s AND cr.organization_id = %s
    """, (customer_receipt_id, org_id))
    receipt = cursor.fetchone()
    if not receipt:
        cursor.close()
        conn.close()
        abort(404)
    allocations = receipt_allocations(cursor, org_id, customer_receipt_id)
    unapplied = money_round(to_decimal(receipt[2] or 0)
                            - sum((row["amount"] for row in allocations), Decimal("0")))
    cursor.close()
    conn.close()
    return render_template(
        "customers/customer_receipt_detail.html",
        receipt=receipt, allocations=allocations, unapplied=unapplied,
    )


@bp.route("/<int:customer_id>/open-invoices")
@require_organization
def customer_open_invoices(customer_id):
    """The posted invoices of one customer that still have something owed.

    Used by the receipt form: choose the customer, and the invoices he may be
    paying for are listed with what is left on each.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM customers WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, org_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        abort(404)
    invoices = open_invoices(cursor, org_id, customer_id)
    cursor.close()
    conn.close()
    return jsonify({"invoices": [
        {
            "id": invoice["invoice_id"],
            "invoice_no": invoice["invoice_no"] or "",
            "date": invoice["date_text"],
            "due": invoice["due_text"],
            "total": str(invoice["total"]),
            "paid": str(invoice["paid"]),
            "remaining": str(invoice["remaining"]),
        }
        for invoice in invoices
    ]})


@bp.route("/reports/ageing")
@require_organization
def customer_ageing_report():
    """What each customer still owes, split 0-30 / 31-60 / 61-90 / older."""
    org_id = current_organization_id()
    as_of_text = (request.args.get("as_of") or "").strip()
    if as_of_text:
        try:
            as_of = clean_receipt_date(as_of_text)
        except ValueError:
            as_of = date.today()
    else:
        as_of = date.today()

    conn = get_conn()
    cursor = conn.cursor()
    rows, totals = customer_ageing(cursor, org_id, as_of)
    cursor.close()
    conn.close()
    return render_template(
        "customers/customer_ageing.html",
        ageing_rows=rows, ageing_totals=totals, buckets=AGEING_BUCKETS,
        as_of=as_of, as_of_text=as_of.isoformat(),
    )


@bp.route("/receipts/<int:customer_receipt_id>/print")
@require_organization
def customer_receipt_print(customer_receipt_id):
    """The printable voucher for one customer receipt (CPV/2026/00001).

    A receipt voucher is the piece of paper the customer signs when he hands
    the money over, so it carries the figures, the words, the method and the
    cheque details - and nothing that is not on the receipt itself.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cr.customer_receipt_id, c.name, cr.amount, cr.receipt_date, cr.memo,
               coa.name, cr.has_wht, cr.wht_rate, cr.wht_amount, cr.journal_header_id,
               cr.voucher_no, cr.payment_type, cr.journal_name, cr.payment_method,
               cr.cheque_no, cr.cheque_bank, cr.cheque_date,
               c.address, c.city, c.ntn, c.phone
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

    amount = money_round(receipt[2] or 0)
    withheld = money_round(receipt[8] or 0) if receipt[6] else Decimal("0")
    net = amount - withheld
    return render_template(
        "customers/print/customer_receipt_print.html",
        receipt=receipt,
        amount=amount,
        withheld=withheld,
        net=net,
        amount_words=amount_in_words(amount),
        net_words=amount_in_words(net),
        is_draft=False,
    )
