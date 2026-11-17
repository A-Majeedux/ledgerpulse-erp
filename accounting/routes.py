import secrets
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from core.db import get_conn
from core.context import current_organization_id, require_organization
from core.docmenu import build_menu
from core.searchbar import read_search, search_filters, and_clauses, describe, suggest
from auth.services import require_permission, current_user_id
from accounting.schema import ACCOUNT_TYPES
from accounting.services import (create_journal_entry, delete_draft_journal_entry,
                               duplicate_journal_entry, post_journal_entry,
                               replace_journal_entry, reset_journal_entry_to_draft,
                               UnbalancedEntryError)
from accounting.intelligence import suggest_account_for_entry
from core.money import to_decimal

bp = Blueprint("accounting", __name__, url_prefix="/accounting")


# ---------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------

@bp.route("/chart-of-accounts")
@require_organization
def chart_of_accounts_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name, account_type, allow_reconciliation, is_active
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
    )


@bp.route("/chart-of-accounts/new", methods=["GET", "POST"])
@require_organization
def new_account():
    org_id = current_organization_id()
    if request.method == "POST":
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chart_of_accounts
                (organization_id, code, name, account_type, allow_reconciliation)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            org_id,
            request.form["code"].strip(),
            request.form["name"].strip(),
            request.form["account_type"],
            "allow_reconciliation" in request.form,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("accounting.chart_of_accounts_list"))
    return render_template("accounting/new_account.html", account_types=ACCOUNT_TYPES)


# ---------------------------------------------------------------------
# Journal Entries
# ---------------------------------------------------------------------

@bp.route("/journal-entries")
@require_organization
def journal_entries_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    # The search bar. Partner, label and expense head live on the entry's lines,
    # so those facets are asked with an EXISTS over the lines rather than a join -
    # a join would repeat the entry once per matching line.
    search = read_search(request)
    clauses, params = search_filters(
        search,
        text_columns=["jh.reference", "jh.journal_name"],
        status_column="jh.status",
        date_column="jh.accounting_date",
    )
    if search.partner:
        clauses.append("EXISTS (SELECT 1 FROM journal_lines pl WHERE pl.journal_header_id = "
                       "jh.journal_header_id AND COALESCE(pl.partner_name, '') ILIKE %s)")
        params.append("%" + search.partner + "%")
    if search.account:
        clauses.append("EXISTS (SELECT 1 FROM journal_lines al WHERE al.journal_header_id = "
                       "jh.journal_header_id AND al.account_id::text = %s)")
        params.append(search.account)
    if search.q:
        pattern = "%" + search.q + "%"
        clauses.append("EXISTS (SELECT 1 FROM journal_lines ql WHERE ql.journal_header_id = "
                       "jh.journal_header_id AND (COALESCE(ql.label, '') ILIKE %s "
                       "OR COALESCE(ql.partner_name, '') ILIKE %s))")
        params.extend([pattern, pattern])
    accounts = _filter_accounts(cursor, org_id)
    search.description = describe(search, accounts)
    cursor.execute("""
        SELECT
            jh.journal_header_id, jh.reference, jh.accounting_date,
            jh.journal_name, jh.status,
            COALESCE(SUM(jl.debit), 0) AS total
        FROM journal_headers jh
        LEFT JOIN journal_lines jl ON jl.journal_header_id = jh.journal_header_id
        WHERE jh.organization_id = %s
    """ + and_clauses(clauses) + """
        GROUP BY jh.journal_header_id
        ORDER BY jh.accounting_date DESC, jh.journal_header_id DESC
    """, [org_id] + params)
    entries = cursor.fetchall()
    suggestions = {
        "q": suggest(cursor, [
            ("SELECT reference FROM journal_headers WHERE organization_id = %s AND reference IS NOT NULL "
             "ORDER BY journal_header_id DESC LIMIT 40", (org_id,)),
            ("SELECT DISTINCT jl.partner_name FROM journal_lines jl "
             "JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id "
             "WHERE jh.organization_id = %s AND jl.partner_name IS NOT NULL LIMIT 40", (org_id,)),
            ("SELECT DISTINCT jl.label FROM journal_lines jl "
             "JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id "
             "WHERE jh.organization_id = %s AND jl.label IS NOT NULL LIMIT 40", (org_id,)),
        ]),
        "partner": suggest(cursor, [
            ("SELECT DISTINCT jl.partner_name FROM journal_lines jl "
             "JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id "
             "WHERE jh.organization_id = %s AND jl.partner_name IS NOT NULL ORDER BY 1 LIMIT 60", (org_id,)),
        ]),
    }
    cursor.close()
    conn.close()
    return render_template("accounting/journal_entries_list.html", entries=entries,
                           search=search, accounts=accounts, suggestions=suggestions)


def _filter_accounts(cursor, org_id):
    """The accounts a list may be filtered to: (id, 'code · name')."""
    cursor.execute("""
        SELECT account_id, code || ' · ' || name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE ORDER BY code
    """, (org_id,))
    return cursor.fetchall()


@bp.route("/journal-entries/new", methods=["GET", "POST"])
@require_organization
def new_journal_entry():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()

    if request.method == "POST":
        account_ids = request.form.getlist("account_id[]")
        partner_names = request.form.getlist("partner_name[]")
        labels = request.form.getlist("label[]")
        debits = request.form.getlist("debit[]")
        credits = request.form.getlist("credit[]")

        lines = []
        for i in range(len(account_ids)):
            if not account_ids[i]:
                continue  # skip blank rows left over from the dynamic grid
            lines.append({
                "account_id": int(account_ids[i]),
                "partner_name": partner_names[i].strip() or None,
                "label": labels[i].strip() or None,
                "debit": to_decimal(debits[i] or 0),
                "credit": to_decimal(credits[i] or 0),
            })

        try:
            journal_header_id = create_journal_entry(
                cursor,
                organization_id=org_id,
                reference=request.form.get("reference", "").strip() or None,
                accounting_date=request.form["accounting_date"],
                journal_name=request.form.get("journal_name", "Miscellaneous Operations").strip(),
                lines=lines,
                source_type="Manual",
                auto_post=False,
            )
            conn.commit()
        except UnbalancedEntryError as error:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template(
                "accounting/new_journal_entry.html",
                accounts=_get_accounts(org_id),
                error=str(error),
                form=request.form,
            )

        cursor.close()
        conn.close()
        return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))

    accounts = _get_accounts(org_id)
    cursor.close()
    conn.close()
    return render_template(
        "accounting/new_journal_entry.html",
        accounts=accounts,
        error=None,
        form=None,
        today=date.today().isoformat(),
    )


@bp.route("/journal-entries/<int:journal_header_id>")
@require_organization
def journal_entry_detail(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT journal_header_id, reference, accounting_date, journal_name, status, source_type, source_id
        FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s
    """, (journal_header_id, org_id))
    header = cursor.fetchone()
    if not header:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute("""
        SELECT coa.code, coa.name, jl.partner_name, jl.label, jl.debit, jl.credit
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
        menu=build_menu("journal_entry", journal_header_id, status=header[4],
                        user_id=current_user_id(), organization_id=org_id),
        trail=_journal_trail(journal_header_id, header),
    )


def _journal_trail(journal_header_id, header):
    """Where this entry came from: the document that wrote it, if any.

    A sales invoice, a receipt, a bill and a payment all record themselves on
    their journal entry (source_type / source_id), so the entry can lead back to
    the paper instead of making the reader hunt for it in a list.
    """
    source_type, source_id = (header[5] or "Manual"), header[6]
    chips = []
    if source_type == "SalesInvoice" and source_id:
        chips.append({"icon": "i-invoice", "label": "Sales invoice", "meta": "the document behind this entry",
                      "url": url_for("sales.sales_invoice_detail", sales_invoice_id=source_id)})
    elif source_type == "CustomerReceipt" and source_id:
        chips.append({"icon": "i-receipt", "label": "Customer receipt", "meta": "the document behind this entry",
                      "url": url_for("customers.customer_receipt_detail", customer_receipt_id=source_id)})
    elif source_type == "VendorBill" and source_id:
        chips.append({"icon": "i-bill", "label": "Vendor bill", "meta": "the document behind this entry",
                      "url": url_for("vendors.vendor_bill_detail", vendor_bill_id=source_id)})
    elif source_type == "VendorPayment" and source_id:
        chips.append({"icon": "i-payment", "label": "Vendor payment", "meta": "the document behind this entry",
                      "url": url_for("vendors.vendor_payment_detail", vendor_payment_id=source_id)})
    elif source_type == "VendorCreditNote" and source_id:
        chips.append({"icon": "i-credit-note", "label": "Vendor credit note", "meta": "the document behind this entry",
                      "url": url_for("vendors.vendor_credit_note_detail", vendor_credit_note_id=source_id)})
    else:
        chips.append({"icon": "i-journal", "label": "Written by hand", "meta": "no document behind it"})
    chips.append({"icon": "i-accounts", "label": "Journal entries", "meta": "all of them",
                  "url": url_for("accounting.journal_entries_list")})
    return chips


@bp.route("/journal-entries/<int:journal_header_id>/edit", methods=["GET", "POST"])
@require_organization
@require_permission("accounting.create")
def edit_journal_entry(journal_header_id):
    """Correct a draft entry, in the same form that writes a new one.

    The entry keeps its identity - its number, its source, anything pointing at
    it - and only its date, wording and lines change.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        accounts = _get_accounts(org_id)
        cursor.execute("""
            SELECT journal_header_id, reference, accounting_date, journal_name, status, source_id
            FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s
        """, (journal_header_id, org_id))
        header = cursor.fetchone()
        if not header:
            abort(404)

        def refusal(message):
            flash(message, "error")
            return redirect(url_for("accounting.journal_entry_detail",
                                    journal_header_id=journal_header_id))

        if (header[4] or "").lower() != "draft":
            return refusal("That entry is posted. Reset it to a draft before changing it.")
        if header[5] is not None:
            return refusal("This entry belongs to a document - correct the document instead.")

        if request.method == "POST":
            account_ids = request.form.getlist("account_id[]")
            partner_names = request.form.getlist("partner_name[]")
            labels = request.form.getlist("label[]")
            debits = request.form.getlist("debit[]")
            credits = request.form.getlist("credit[]")
            lines = []
            for index, account_id in enumerate(account_ids):
                if not account_id:
                    continue
                lines.append({
                    "account_id": int(account_id),
                    "partner_name": partner_names[index].strip() or None,
                    "label": labels[index].strip() or None,
                    "debit": to_decimal(debits[index] or 0),
                    "credit": to_decimal(credits[index] or 0),
                })
            try:
                replace_journal_entry(
                    cursor, org_id, journal_header_id,
                    reference=request.form.get("reference", "").strip() or None,
                    accounting_date=request.form["accounting_date"],
                    journal_name=request.form.get("journal_name", "Miscellaneous Operations").strip(),
                    lines=lines)
                conn.commit()
            except (UnbalancedEntryError, ValueError) as error:
                conn.rollback()
                return render_template(
                    "accounting/new_journal_entry.html", accounts=accounts,
                    draft=_draft_from_form(request.form, journal_header_id),
                    draft_lines=_lines_from_form(request.form),
                    error=str(error), today=date.today().isoformat())
            flash("The entry is corrected. Post it when you are ready.", "success")
            return redirect(url_for("accounting.journal_entry_detail",
                                    journal_header_id=journal_header_id))

        cursor.execute("""
            SELECT account_id, partner_name, label, debit, credit
            FROM journal_lines WHERE journal_header_id = %s
            ORDER BY line_order, journal_line_id
        """, (journal_header_id,))
        # as text: the numbers go straight into the page's JavaScript
        draft_lines = [{"account_id": row[0], "partner_name": row[1] or "", "label": row[2] or "",
                        "debit": str(row[3] or 0), "credit": str(row[4] or 0)}
                       for row in cursor.fetchall()]
        draft = {"id": journal_header_id, "reference": header[1] or "",
                 "accounting_date": header[2].isoformat() if header[2] else "",
                 "journal_name": header[3] or ""}
        return render_template(
            "accounting/new_journal_entry.html", accounts=accounts,
            draft=draft, draft_lines=draft_lines, error=None, today=date.today().isoformat())
    finally:
        cursor.close()
        conn.close()


def _draft_from_form(form, journal_header_id):
    return {"id": journal_header_id,
            "reference": (form.get("reference") or "").strip(),
            "accounting_date": form.get("accounting_date") or "",
            "journal_name": (form.get("journal_name") or "").strip()}


def _lines_from_form(form):
    account_ids = form.getlist("account_id[]")
    partner_names = form.getlist("partner_name[]")
    labels = form.getlist("label[]")
    debits = form.getlist("debit[]")
    credits = form.getlist("credit[]")
    rows = []
    for index, account_id in enumerate(account_ids):
        if not account_id:
            continue
        rows.append({"account_id": int(account_id),
                     "partner_name": partner_names[index] if index < len(partner_names) else "",
                     "label": labels[index] if index < len(labels) else "",
                     "debit": debits[index] if index < len(debits) else 0,
                     "credit": credits[index] if index < len(credits) else 0})
    return rows


@bp.route("/journal-entries/<int:journal_header_id>/reset-to-draft", methods=["POST"])
@require_organization
@require_permission("accounting.post")
def reset_journal_entry_route(journal_header_id):
    """Take a posted entry back to a draft so it can be corrected.

    An entry that belongs to an invoice, a bill or a receipt is refused here -
    it is reset from that document's own page, so the two never disagree.
    """
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        reference = reset_journal_entry_to_draft(cursor, org_id, journal_header_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        flash(str(error), "error")
    else:
        flash(f"{reference or 'The entry'} is a draft again. Correct it, then post it.", "success")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))


@bp.route("/journal-entries/<int:journal_header_id>/duplicate", methods=["POST"])
@require_organization
@require_permission("accounting.create")
def duplicate_journal_entry_route(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        new_id = duplicate_journal_entry(cursor, org_id, journal_header_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        abort(400, description=str(error))
    finally:
        cursor.close()
        conn.close()
    flash("A copy of the entry is saved as a draft - change what you need, then post it.", "success")
    return redirect(url_for("accounting.journal_entry_detail", journal_header_id=new_id))


@bp.route("/journal-entries/<int:journal_header_id>/delete", methods=["POST"])
@require_organization
@require_permission("accounting.create")
def delete_journal_entry_route(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        reference = delete_draft_journal_entry(cursor, org_id, journal_header_id)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        cursor.close()
        conn.close()
        flash(str(error), "error")
        return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))
    cursor.close()
    conn.close()
    flash(f"{reference or 'The draft entry'} was deleted.", "success")
    return redirect(url_for("accounting.journal_entries_list"))


@bp.route("/journal-entries/<int:journal_header_id>/post", methods=["POST"])
@require_organization
def post_journal_entry_route(journal_header_id):
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM journal_headers WHERE journal_header_id = %s AND organization_id = %s",
        (journal_header_id, org_id)
    )
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        abort(404)
    try:
        post_journal_entry(cursor, journal_header_id)
        conn.commit()
    except UnbalancedEntryError:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect(url_for("accounting.journal_entry_detail", journal_header_id=journal_header_id))


def _get_accounts(org_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT account_id, code, name FROM chart_of_accounts
        WHERE organization_id = %s AND is_active = TRUE
        ORDER BY code
    """, (org_id,))
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return accounts


@bp.route("/intelligence/suggest", methods=["GET"])
@require_organization
def intelligence_suggest():
    org_id = current_organization_id()
    query_text = request.args.get("q", "")
    partner_name = request.args.get("partner", "")
    
    conn = get_conn()
    cursor = conn.cursor()
    try:
        suggestions = suggest_account_for_entry(cursor, org_id, query_text, partner_name)
    finally:
        cursor.close()
        conn.close()
        
    return jsonify({"success": True, "suggestions": suggestions})


@bp.route("/bank-transfers")
@require_organization
def bank_transfers_list():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bt.bank_transfer_id, bt.transfer_date, bt.amount, bt.currency, bt.memo,
               sa.name as source_name, da.name as dest_name, bt.journal_header_id
        FROM bank_transfers bt
        JOIN chart_of_accounts sa ON sa.account_id = bt.source_account_id
        JOIN chart_of_accounts da ON da.account_id = bt.destination_account_id
        WHERE bt.organization_id = %s
        ORDER BY bt.transfer_date DESC, bt.bank_transfer_id DESC
    """, (org_id,))
    transfers = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("accounting/bank_transfers_list.html", transfers=transfers)


@bp.route("/bank-transfers/new", methods=["GET", "POST"])
@require_organization
def new_bank_transfer():
    org_id = current_organization_id()
    conn = get_conn()
    cursor = conn.cursor()

    if request.method == "POST":
        source_account_id = int(request.form["source_account_id"])
        destination_account_id = int(request.form["destination_account_id"])
        amount = to_decimal(request.form["amount"])
        transfer_date = request.form["transfer_date"]
        memo = request.form.get("memo", "").strip()

        cursor.execute("SELECT name FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (source_account_id, org_id))
        src_row = cursor.fetchone()
        cursor.execute("SELECT name FROM chart_of_accounts WHERE account_id = %s AND organization_id = %s", (destination_account_id, org_id))
        dst_row = cursor.fetchone()

        if not src_row or not dst_row or source_account_id == destination_account_id or amount <= 0:
            conn.rollback()
            cursor.close()
            conn.close()
            abort(400, description="Invalid bank transfer parameters.")

        source_name = src_row[0]
        dest_name = dst_row[0]

        try:
            je_lines = [
                {"account_id": destination_account_id, "partner_name": None, "label": f"Bank Transfer from {source_name}", "debit": amount, "credit": 0},
                {"account_id": source_account_id, "partner_name": None, "label": f"Bank Transfer to {dest_name}", "debit": 0, "credit": amount},
            ]
            reference = f"TRF-{date.today().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"
            journal_header_id = create_journal_entry(
                cursor,
                organization_id=org_id,
                reference=reference,
                accounting_date=transfer_date,
                journal_name="Bank Internal Transfers",
                lines=je_lines,
                source_type="BankTransfer",
                auto_post=True,
            )

            cursor.execute("""
                INSERT INTO bank_transfers
                    (organization_id, transfer_date, amount, source_account_id, destination_account_id, memo, journal_header_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING bank_transfer_id
            """, (org_id, transfer_date, amount, source_account_id, destination_account_id, memo, journal_header_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template("accounting/new_bank_transfer.html", accounts=_get_accounts(org_id), error=str(e), today=date.today().isoformat())

        cursor.close()
        conn.close()
        return redirect(url_for("accounting.bank_transfers_list"))

    accounts = _get_accounts(org_id)
    cursor.close()
    conn.close()
    return render_template("accounting/new_bank_transfer.html", accounts=accounts, error=None, today=date.today().isoformat())

