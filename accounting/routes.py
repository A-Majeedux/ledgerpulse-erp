from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from core.db import get_conn
from core.context import current_organization_id, require_organization
from accounting.schema import (
    ACCOUNT_TYPES, ACCOUNT_KINDS, KINDS_FOR_TYPE, KIND_GENERAL, CASH_AND_BANK_KINDS,
    TRADE_PARTNER_KINDS, NON_TRADING_PARTNER_KINDS, PARTNER_KINDS, CONTROL_ACCOUNT_KINDS,
    KIND_TRADE_RECEIVABLE, TRADE_RECEIVABLES_CODE, TRADE_PAYABLES_CODE,
)
from accounting.services import create_journal_entry, post_journal_entry, JournalEntryError
from core.validation import ValidationError, parse_amount, parse_date, require_text

# A journal entry created by another screen (a vendor bill, a sales invoice)
# is the accounting shadow of that document. It must be posted FROM the
# document, so the two can never disagree (ledger says Posted, bill says
# Draft). This maps journal_headers.source_type to the screen that owns it:
#   source_type -> (name shown to the user, endpoint, name of its id argument)
SOURCE_DOCUMENTS = {
    "VendorBill": ("Vendor Bill", "vendors.vendor_bill_detail", "vendor_bill_id"),
    "SalesInvoice": ("Sales Invoice", "sales.sales_invoice_detail", "sales_invoice_id"),
}

bp = Blueprint("accounting", __name__, url_prefix="/accounting")


# ---------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------

def _parse_account_kind(code, account_type, raw_kind):
    """
    The kind chosen on the account form, checked against the account's code
    and type. Returns the kind to store, or raises ValidationError with a
    message for the form.
    """
    if code in CONTROL_ACCOUNT_KINDS:
        # 1100 / 2000 are the accounts every customer / vendor posts to. Their
        # kind is part of how the vouchers work, so it is not a choice.
        control_kind = CONTROL_ACCOUNT_KINDS[code]
        needed_type = "Asset" if control_kind == KIND_TRADE_RECEIVABLE else "Liability"
        if account_type != needed_type:
            raise ValidationError(
                f"Account {code} is the {control_kind.lower()} control account, so its type must be {needed_type}."
            )
        return control_kind
    kind = (raw_kind or "").strip() or KIND_GENERAL
    if kind in TRADE_PARTNER_KINDS:
        raise ValidationError(
            f"{kind} belongs to the control account customers and vendors post to "
            f"({TRADE_RECEIVABLES_CODE} and {TRADE_PAYABLES_CODE}). It cannot be given to another account."
        )
    if kind not in ACCOUNT_KINDS:
        raise ValidationError("Kind must be one of: " + ", ".join(ACCOUNT_KINDS) + ".")
    allowed = KINDS_FOR_TYPE.get(account_type, [KIND_GENERAL])
    if kind not in allowed:
        raise ValidationError(
            f"{kind} is not available for an account of type {account_type}. "
            f"For {account_type} choose: " + ", ".join(allowed) + "."
        )
    return kind


@bp.route("/chart-of-accounts")
@require_organization
def chart_of_accounts_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name, account_type, allow_reconciliation, is_active, account_kind
        FROM chart_of_accounts
        WHERE organization_id = %s
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template(
        "accounting/chart_of_accounts.html",
        accounts=accounts,
        account_types=ACCOUNT_TYPES,
        general_kind=KIND_GENERAL,
        cash_and_bank_kinds=CASH_AND_BANK_KINDS,
    )


@bp.route("/chart-of-accounts/new", methods=["GET", "POST"])
@require_organization
def new_account():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        try:
            code = require_text(request.form.get("code"), "Code", max_length=20)
            name = require_text(request.form.get("name"), "Name", max_length=150)
            account_type = request.form.get("account_type")
            if account_type not in ACCOUNT_TYPES:
                raise ValidationError("Type must be one of: " + ", ".join(ACCOUNT_TYPES) + ".")
            account_kind = _parse_account_kind(code, account_type, request.form.get("account_kind"))
            # Account codes are unique inside one organization. Checking first
            # gives a readable message instead of a database error page.
            cursor.execute(
                "SELECT name FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
                (org_id, code)
            )
            existing = cursor.fetchone()
            if existing:
                raise ValidationError(
                    f"Account code {code} is already used by \"{existing[0]}\". Choose a different code."
                )
        except ValidationError as error:
            cursor.close()
            conn.close()
            return render_template(
                "accounting/new_account.html",
                account_types=ACCOUNT_TYPES, kinds_for_type=KINDS_FOR_TYPE,
                error=str(error), form=request.form,
            )

        cursor.execute("""
            INSERT INTO chart_of_accounts
                (organization_id, code, name, account_type, allow_reconciliation, account_kind)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            org_id,
            code,
            name,
            account_type,
            "allow_reconciliation" in request.form,
            account_kind,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("accounting.chart_of_accounts_list"))
    return render_template(
        "accounting/new_account.html",
        account_types=ACCOUNT_TYPES, kinds_for_type=KINDS_FOR_TYPE, error=None, form=None,
    )


@bp.route("/chart-of-accounts/<int:account_id>/edit", methods=["GET", "POST"])
@require_organization
def edit_account(account_id):
    """
    Change an account's name, its kind, its reconciliation tick and whether
    it is active.

    The code and the type are shown but cannot be changed here: vouchers
    find their control accounts by code, and an account's type decides
    where its balance appears in every report.

    An INACTIVE account keeps its balance and its history; it only stops
    being offered in the account lists of new documents.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name, account_type, allow_reconciliation, is_active, account_kind
        FROM chart_of_accounts
        WHERE account_id = %s AND organization_id = %s
    """, (account_id, org_id))
    account = cursor.fetchone()
    if not account:
        cursor.close()
        conn.close()
        abort(404)
    code, account_type, current_kind = account[1], account[3], account[6]
    # 1100 / 2000: every invoice, bill, receipt and payment posts to them,
    # so their kind is fixed and they cannot be switched off.
    is_control_account = code in CONTROL_ACCOUNT_KINDS
    kinds = [current_kind] if is_control_account else list(KINDS_FOR_TYPE.get(account_type, [KIND_GENERAL]))
    if current_kind not in kinds:
        kinds.append(current_kind)      # never hide what the account is today

    error = None
    if request.method == "POST":
        try:
            name = require_text(request.form.get("name"), "Name", max_length=150)
            if is_control_account:
                account_kind, is_active = current_kind, True
            else:
                account_kind = _parse_account_kind(code, account_type, request.form.get("account_kind"))
                status = (request.form.get("status") or "").strip()
                if status not in ("active", "inactive"):
                    raise ValidationError("Status must be Active or Inactive.")
                is_active = status == "active"
            cursor.execute("""
                UPDATE chart_of_accounts
                SET name = %s, account_kind = %s, allow_reconciliation = %s, is_active = %s
                WHERE account_id = %s AND organization_id = %s
            """, (name, account_kind, "allow_reconciliation" in request.form, is_active, account_id, org_id))
            conn.commit()
            cursor.close()
            conn.close()
            return redirect(url_for("accounting.chart_of_accounts_list"))
        except ValidationError as validation_error:
            conn.rollback()
            error = str(validation_error)

    cursor.close()
    conn.close()
    return render_template(
        "accounting/edit_account.html",
        account=account, kinds=kinds, is_control_account=is_control_account,
        error=error, form=request.form if request.method == "POST" else None,
    )


# ---------------------------------------------------------------------
# Journal Entries
# ---------------------------------------------------------------------

@bp.route("/journal-entries")
@require_organization
def journal_entries_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            jh.journal_header_id, jh.reference, jh.accounting_date,
            jh.journal_name, jh.status,
            COALESCE(SUM(jl.debit), 0) AS total
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE jh.organization_id = %s
        GROUP BY jh.journal_header_id
        ORDER BY jh.accounting_date DESC, jh.journal_header_id DESC
    """, (org_id,))
    entries = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("accounting/journal_entries_list.html", entries=entries)


# How the General account list is headed, type by type.
GENERAL_GROUP_TITLES = {
    "Asset": "Assets", "Liability": "Liabilities", "Equity": "Equity",
    "Revenue": "Revenue", "Expense": "Expenses",
}


def _is_id(text):
    """True for what a database id looks like in a form: 1 to 9 plain digits."""
    return text.isascii() and text.isdigit() and len(text) <= 9


def _journal_entry_choices(cursor, org_id):
    """
    What the two account columns of the journal entry grid can hold, for
    ONE organization:

      General account   every active account that is NOT a partner account:
                        revenue, expenses, bank, cash, tax ledgers, equity...
      Partner account   the organization's active customers and vendors, BY
                        NAME, plus its active non-trading partner accounts
                        (one account per director / employee).

    A customer line is posted to Trade Receivables (code 1100) and a vendor
    line to Trade Creditors (code 2000), with the partner's name on the
    line - exactly what an invoice, a bill, a receipt or a payment does.

    The same data builds the page AND checks what comes back from it, so a
    line can never be saved on something the form did not offer.
    """
    cursor.execute("""
        SELECT account_id, code, name, account_type, account_kind, is_active
        FROM chart_of_accounts
        WHERE organization_id = %s
        ORDER BY code, account_id
    """, (org_id,))
    accounts = {
        account_id: {
            "id": account_id, "code": code, "name": name, "type": account_type,
            "kind": account_kind, "active": bool(is_active), "label": f"{code} - {name}",
        }
        for account_id, code, name, account_type, account_kind, is_active in cursor.fetchall()
    }
    cursor.execute("""
        SELECT customer_id, name FROM customers
        WHERE organization_id = %s AND is_active = TRUE ORDER BY name, customer_id
    """, (org_id,))
    customers = dict(cursor.fetchall())
    cursor.execute("""
        SELECT vendor_id, name FROM vendors
        WHERE organization_id = %s AND is_active = TRUE ORDER BY name, vendor_id
    """, (org_id,))
    vendors = dict(cursor.fetchall())

    def with_code(code):
        return next((account for account in accounts.values() if account["code"] == code), None)

    return {
        "accounts": accounts,
        "customers": customers,                         # {customer_id: name}
        "vendors": vendors,                             # {vendor_id: name}
        "receivables": with_code(TRADE_RECEIVABLES_CODE),   # the account customer lines land on
        "payables": with_code(TRADE_PAYABLES_CODE),         # the account vendor lines land on
    }


def _journal_entry_options(choices):
    """
    The choices above, shaped for the page. It travels as JSON (the grid
    rows are built in the browser): lists of {"value", "label"} in groups.
    """
    accounts = [account for account in choices["accounts"].values() if account["active"]]

    general = []
    for account_type in ACCOUNT_TYPES + ["Other"]:
        options = [
            {"value": str(account["id"]), "label": account["label"]}
            for account in accounts
            if account["kind"] not in PARTNER_KINDS
            and (account["type"] == account_type
                 or (account_type == "Other" and account["type"] not in ACCOUNT_TYPES))
        ]
        if options:
            general.append({"title": GENERAL_GROUP_TITLES.get(account_type, account_type), "options": options})

    partner = []
    receivables, payables = choices["receivables"], choices["payables"]
    if choices["customers"]:
        partner.append({
            "title": "Customers",
            "posts_to": receivables["label"] if receivables else None,
            "options": [{"value": f"customer:{customer_id}", "label": name}
                        for customer_id, name in choices["customers"].items()],
        })
    if choices["vendors"]:
        partner.append({
            "title": "Vendors",
            "posts_to": payables["label"] if payables else None,
            "options": [{"value": f"vendor:{vendor_id}", "label": name}
                        for vendor_id, name in choices["vendors"].items()],
        })
    non_trading = [account for account in accounts if account["kind"] in NON_TRADING_PARTNER_KINDS]
    if non_trading:
        partner.append({
            "title": "Non-trading partners (directors, employees)",
            "posts_to": None,
            "options": [{"value": f"account:{account['id']}", "label": account["label"], "note": account["kind"]}
                        for account in non_trading],
        })
    return {"general": general, "partner": partner}


def _line_account(choices, row, number):
    """
    The account ONE grid line is posted to, from its two account boxes.
    Returns (account_id, partner_name). Raises ValidationError with the
    sentence to show when the line's choice cannot be accepted.
    """
    general, partner = row["account_id"], row["partner_account"]
    accounts = choices["accounts"]
    reload_hint = " If you switched company in another tab, reload this page and pick again."

    if general and partner:
        raise ValidationError(
            f"Line {number}: a line is posted to one account. Pick a general account "
            "OR a partner account - this line has one in each box."
        )

    if general:
        account = accounts.get(int(general)) if _is_id(general) else None
        if account is None:
            raise ValidationError(f"Line {number}: select an account from the General account list." + reload_hint)
        if account["kind"] in TRADE_PARTNER_KINDS:
            who = "customer" if account["kind"] == KIND_TRADE_RECEIVABLE else "vendor"
            raise ValidationError(
                f"Line {number}: {account['label']} is the account all {who}s share, so it is not picked directly. "
                f"Leave General account empty and pick the {who} under Partner account - "
                f"the line is then posted to {account['label']} for you."
            )
        if account["kind"] in NON_TRADING_PARTNER_KINDS:
            raise ValidationError(
                f"Line {number}: {account['label']} is a partner account. "
                "Pick it under Partner account, not under General account."
            )
        if not account["active"]:
            raise ValidationError(
                f"Line {number}: {account['label']} is inactive. Pick another account, "
                "or make it active again on the Chart of Accounts."
            )
        return account["id"], None

    kind, _, raw_id = partner.partition(":")
    if kind not in ("customer", "vendor", "account") or not _is_id(raw_id):
        raise ValidationError(f"Line {number}: pick the partner from the Partner account list.")
    partner_id = int(raw_id)

    if kind == "account":           # a non-trading partner: the person IS the account
        account = accounts.get(partner_id)
        if account is None:
            raise ValidationError(f"Line {number}: pick the partner from the Partner account list." + reload_hint)
        if account["kind"] in TRADE_PARTNER_KINDS:
            raise ValidationError(
                f"Line {number}: {account['label']} is shared by all customers or vendors. "
                "Pick the customer or vendor by name under Partner account."
            )
        if account["kind"] not in NON_TRADING_PARTNER_KINDS:
            raise ValidationError(
                f"Line {number}: {account['label']} is not a partner account. Pick it under General account."
            )
        if not account["active"]:
            raise ValidationError(
                f"Line {number}: {account['label']} is inactive. Pick another partner, "
                "or make it active again on the Chart of Accounts."
            )
        return account["id"], None

    names = choices["customers"] if kind == "customer" else choices["vendors"]
    control = choices["receivables"] if kind == "customer" else choices["payables"]
    if partner_id not in names:
        raise ValidationError(
            f"Line {number}: that {kind} is not one of this company's active {kind}s." + reload_hint
        )
    if control is None:
        code = TRADE_RECEIVABLES_CODE if kind == "customer" else TRADE_PAYABLES_CODE
        raise ValidationError(
            f"Line {number}: this company has no account with code {code}, which every {kind} is posted to. "
            "Add it on the Chart of Accounts first."
        )
    return control["id"], names[partner_id]


@bp.route("/journal-entries/new", methods=["GET", "POST"])
@require_organization
def new_journal_entry():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    choices = _journal_entry_choices(cursor, org_id)
    options = _journal_entry_options(choices)

    if request.method == "POST":
        # One list per grid column. Every row of the grid sends every box
        # (an unused box arrives empty), so position i is row i in each list.
        columns = {
            name: request.form.getlist(f"{name}[]")
            for name in ("account_id", "partner_account", "label", "debit", "credit")
        }
        row_count = max(len(values) for values in columns.values())

        def cell(name, i):
            """The i-th value of one grid column ('' if the browser sent fewer)."""
            values = columns[name]
            return values[i].strip() if i < len(values) else ""

        # Exactly what was typed, row by row, so that a refused entry comes
        # back with its lines still on screen instead of an empty grid.
        submitted_lines = [{name: cell(name, i) for name in columns} for i in range(row_count)]

        try:
            accounting_date = parse_date(request.form.get("accounting_date"), "Accounting Date")

            lines = []
            for number, row in enumerate(submitted_lines, start=1):
                debit = parse_amount(row["debit"] or "0", f"Line {number}: debit", allow_zero=True)
                credit = parse_amount(row["credit"] or "0", f"Line {number}: credit", allow_zero=True)
                if not row["account_id"] and not row["partner_account"]:
                    if debit == 0 and credit == 0:
                        continue  # a blank row left over from the dynamic grid
                    raise ValidationError(
                        f"Line {number}: select an account - a general account, or a partner account."
                    )
                account_id, partner_name = _line_account(choices, row, number)
                if debit != 0 and credit != 0:
                    raise ValidationError(
                        f"Line {number}: enter a debit or a credit, not both. "
                        "Use two lines if the account is both debited and credited."
                    )
                if debit == 0 and credit == 0:
                    raise ValidationError(f"Line {number}: enter a debit or a credit amount.")
                lines.append({
                    "account_id": account_id,
                    "partner_name": partner_name,
                    "label": row["label"] or None,
                    "debit": debit,
                    "credit": credit,
                })
            if len(lines) < 2:
                raise ValidationError(
                    "A journal entry needs at least two lines: one debit and one credit."
                )

            journal_header_id = create_journal_entry(
                cursor,
                organization_id=org_id,
                reference=request.form.get("reference", "").strip() or None,
                accounting_date=accounting_date,
                journal_name=request.form.get("journal_name", "Miscellaneous Operations").strip(),
                lines=lines,
                source_type="Manual",
                auto_post=False,
            )
            conn.commit()
        except (ValidationError, JournalEntryError) as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "accounting/new_journal_entry.html",
                options=options,
                error=str(error),
                form=request.form,
                submitted_lines=submitted_lines,
            )

        cursor.close()
        conn.close()
        return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))

    cursor.close()
    conn.close()
    return render_template(
        "accounting/new_journal_entry.html",
        options=options,
        error=None,
        form=None,
        submitted_lines=[],
        today=date.today().isoformat(),
    )


@bp.route("/journal-entries/<int:journal_header_id>")
@require_organization
def journal_entry_detail(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT journal_header_id, reference, accounting_date, journal_name, status,
               source_type, source_id
        FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s
    """, (journal_header_id, org_id))
    header = cursor.fetchone()
    if not header:
        cursor.close()
        conn.close()
        abort(404)
    source_document = _source_document(header[5], header[6])

    cursor.execute("""
        SELECT coa.code, coa.name, jl.partner_name, jl.label, jl.debit, jl.credit, coa.account_kind
        FROM journal_lines jl
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jl.journal_header_id = %s
        ORDER BY jl.line_order
    """, (journal_header_id,))
    lines = cursor.fetchall()
    cursor.close()
    conn.close()

    total_debit = sum(float(line[4]) for line in lines)
    total_credit = sum(float(line[5]) for line in lines)

    return render_template(
        "accounting/journal_entry_detail.html",
        header=header,
        lines=lines,
        total_debit=total_debit,
        total_credit=total_credit,
        source_document=source_document,
        trade_partner_kinds=TRADE_PARTNER_KINDS,
        non_trading_partner_kinds=NON_TRADING_PARTNER_KINDS,
        trade_receivable_kind=KIND_TRADE_RECEIVABLE,
    )


def _source_document(source_type, source_id):
    """
    If a journal entry was created by another screen (see SOURCE_DOCUMENTS),
    returns {"label": "Vendor Bill", "url": "/vendors/bills/7"} so the
    journal entry page can link to it. Returns None for a manual entry.
    """
    if source_type not in SOURCE_DOCUMENTS:
        return None
    label, endpoint, id_argument = SOURCE_DOCUMENTS[source_type]
    url = url_for(endpoint, **{id_argument: source_id}) if source_id else None
    return {"label": label, "url": url}


@bp.route("/journal-entries/<int:journal_header_id>/post", methods=["POST"])
@require_organization
def post_journal_entry_route(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_type FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s",
        (journal_header_id, org_id)
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        abort(404)
    if row[0] in SOURCE_DOCUMENTS:
        # Posting it here would leave the ledger Posted while the bill /
        # invoice it belongs to still says Draft.
        cursor.close()
        conn.close()
        abort(409, description=(
            f"This journal entry belongs to a {SOURCE_DOCUMENTS[row[0]][0]}. "
            f"Open the {SOURCE_DOCUMENTS[row[0]][0]} and post it there."
        ))
    try:
        post_journal_entry(cursor, journal_header_id)
        conn.commit()
    except JournalEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))
