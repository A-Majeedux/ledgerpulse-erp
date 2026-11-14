from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.services import (
    create_journal_entry, JournalEntryError,
    list_cash_and_bank_accounts, cash_or_bank_account_problem,
)
from core.validation import ValidationError, parse_amount, parse_date
from wht.routes import get_active_tax_rates, pick_wht_rate
from core.money import to_decimal, money_round
from customers.services import (
    LIST_COLUMNS, NATURE_OF_SUPPLY_OPTIONS, TEXT_FIELDS, NAME_MAX_LENGTH,
    blank_form, clean_customer_form, clean_search, count_customers, create_customer,
    field_suggestions, form_as_typed, form_from_customer, get_customer, list_customers,
    list_row, name_lock_reason, update_customer,
)

bp = Blueprint("customers", __name__, url_prefix="/customers")


def _get_account_id_by_code(cursor, org_id, code):
    cursor.execute(
        "SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
        (org_id, code)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _get_cash_and_bank_accounts(org_id):
    """The accounts a receipt can be deposited to: active bank and cash accounts only."""
    conn = get_conn()
    cursor = conn.cursor()
    accounts = list_cash_and_bank_accounts(cursor, org_id)
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
#
# These three screens only read and write the customers table. The rules
# (what may be typed, the ID, when a name is locked) live in
# customers/services.py; nothing here touches a journal entry.
# ---------------------------------------------------------------------
_FIELD_LIMITS = dict({field: max_length for field, _label, max_length in TEXT_FIELDS}, name=NAME_MAX_LENGTH)


def _customer_form(template, cursor, org_id, **context):
    return render_template(
        template,
        nature_options=NATURE_OF_SUPPLY_OPTIONS,
        limits=_FIELD_LIMITS,
        suggestions=field_suggestions(cursor, org_id),
        **context,
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
            # Refused: show the form again with everything that was typed.
            values = form_as_typed(request.form)
        return _customer_form("customers/new_customer.html", cursor, org_id, values=values, problems=problems)
    finally:
        cursor.close()
        conn.close()


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@require_organization
def edit_customer(customer_id):
    """
    Change a customer's card. The ID never changes. The name can be changed
    only while the customer has no entries (see customers/services.py).

    An INACTIVE customer keeps its history and its balance; it only stops
    being offered when a new invoice, receipt or journal entry is entered.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        customer = get_customer(cursor, org_id, customer_id)
        if customer is None:            # no such customer in THIS organization
            abort(404)
        values, problems = form_from_customer(customer), []
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
            values = form_as_typed(request.form, base=values)
        return _customer_form(
            "customers/edit_customer.html", cursor, org_id,
            customer=customer, values=values, problems=problems,
            name_locked=name_lock_reason(cursor, org_id, customer),
        )
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
        # None when the box was left empty (or holds something that is not an
        # account number) - answered with a message further down.
        raw_deposit_to = (request.form.get("deposit_to_account_id") or "").strip()
        deposit_to_account_id = (
            int(raw_deposit_to)
            if raw_deposit_to.isascii() and raw_deposit_to.isdigit() and len(raw_deposit_to) <= 9
            else None
        )

        cursor.execute("SELECT 1 FROM customers WHERE customer_id = %s AND organization_id = %s", (customer_id, org_id))
        if not cursor.fetchone():
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400)
        if deposit_to_account_id is not None:
            cursor.execute("SELECT 1 FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (deposit_to_account_id, org_id))
            if not cursor.fetchone():
                conn.rollback()
                cursor.close()
                conn.close()
                abort(400)

        # Check what was typed BEFORE anything is written, so a mistake comes
        # back as a message on the form instead of a wrong posted receipt.
        try:
            # Money received lands in a bank or cash account - never in an
            # income, tax or partner account.
            if deposit_to_account_id is None:
                raise ValidationError("Deposit to account: choose one of this company's bank or cash accounts.")
            account_problem = cash_or_bank_account_problem(cursor, org_id, deposit_to_account_id, "Deposit to account")
            if account_problem:
                raise ValidationError(account_problem)
            gross_amount = parse_amount(request.form.get("amount"), "Amount")
            receipt_date = parse_date(request.form.get("receipt_date"), "Date")
            has_wht = "has_wht" in request.form
            # The rate must be one of this organization's active rates on the
            # Tax Rates screen - never a free-typed or left-blank figure.
            wht_rate = pick_wht_rate(org_id, request.form.get("wht_rate")) if has_wht else None
        except ValidationError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "customers/new_customer_receipt.html",
                customers=_get_customers(org_id), accounts=_get_cash_and_bank_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        wht_amount = money_round(gross_amount * (wht_rate / Decimal("100"))) if has_wht and wht_rate else to_decimal(0)
        net_received = gross_amount - wht_amount

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
                accounting_date=receipt_date,
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
                org_id, customer_id, gross_amount, receipt_date,
                request.form.get("memo", "").strip() or None, deposit_to_account_id,
                has_wht, wht_rate, wht_amount, journal_header_id,
            ))
            customer_receipt_id = cursor.fetchone()[0]
            conn.commit()
        except JournalEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "customers/new_customer_receipt.html",
                customers=_get_customers(org_id), accounts=_get_cash_and_bank_accounts(org_id),
                tax_rates=get_active_tax_rates(org_id),
                error=str(error), today=date.today().isoformat(),
            )

        cursor.close()
        conn.close()
        return redirect(url_for("customers.customer_receipt_detail", customer_receipt_id=customer_receipt_id))

    return render_template(
        "customers/new_customer_receipt.html",
        customers=_get_customers(org_id), accounts=_get_cash_and_bank_accounts(org_id),
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
