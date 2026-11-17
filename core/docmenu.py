"""
core/docmenu.py

The little gear menu on a transaction page - Duplicate, Delete, and whatever
else the document allows - and the rule for who may see each item.

Why it lives here:
  * the same menu appears on every transaction (invoice, receipt, bill, journal
    entry), so the wording and the web address of each action are written once;
  * an action is shown only when the person looking at the page may use it, so
    the menu is decided on the server and a hidden button cannot be called by
    typing its address either - every action route carries the same
    require_permission() as the menu item;
  * a rule that cannot be met (a posted invoice cannot be deleted) is shown
    greyed out **with the reason**, rather than silently disappearing, so the
    person knows why the action is not there.

Nothing here touches the database. It only says what the menu should contain;
`permissions` are the codes in the permissions table (sales.create,
sales.post, vendors.create, vendors.post, accounting.create, accounting.post).
"""
from auth.services import has_permission, is_platform_admin


def _may(user_id, organization_id, permission):
    """Is this person allowed this permission? No permission asked means yes."""
    if not permission:
        return True
    if not user_id or not organization_id:
        return False
    if is_platform_admin():
        return True
    return has_permission(user_id, organization_id, permission)


# ---------------------------------------------------------------------------
# What each kind of transaction can do
#
#   url          where the action posts to ("{id}" is the document's own id)
#   post         True: it is a form button (POST); False: it is a link
#   draft_only   True: only while the document is a draft
#   blocked_by   a key in `extra` that, when true, stops it with `blocked_note`
#   confirm      asked before the form is sent
# ---------------------------------------------------------------------------
ACTIONS = {
    "sales_invoice": [
        {"key": "print", "label": "Print / PDF invoice", "icon": "i-print",
         "url": "/sales/invoices/{id}/print", "post": False},
        {"key": "edit", "label": "Edit this draft", "icon": "i-edit",
         "url": "/sales/invoices/{id}/edit", "post": False, "permission": "sales.create",
         "draft_only": True,
         "blocked_note": "A posted invoice is corrected by resetting it to a draft first."},
        {"key": "reset", "label": "Reset to draft (to correct it)", "icon": "i-arrow-up",
         "url": "/sales/invoices/{id}/reset-to-draft", "post": True, "permission": "sales.post",
         "posted_only": True,
         "confirm": "Reset this invoice to a draft? It stops counting in the books until it is posted "
                    "again, and you can then correct it.",
         "blocked_note": "Only a posted invoice can be reset."},
        {"key": "duplicate", "label": "Duplicate as a new draft", "icon": "i-refresh",
         "url": "/sales/invoices/{id}/duplicate", "post": True, "permission": "sales.create"},
        {"key": "delete", "label": "Delete this draft", "icon": "i-trash",
         "url": "/sales/invoices/{id}/delete", "post": True, "permission": "sales.create",
         "draft_only": True, "tone": "danger",
         "confirm": "Delete this draft invoice? Its lines and its draft journal entry go with it.",
         "blocked_note": "A posted invoice cannot be deleted - the books already carry it."},
    ],
    "customer_receipt": [
        # Printing is not a change to the document, so it asks for no permission
        # beyond being able to see the page it is on.
        {"key": "print", "label": "Print / PDF voucher", "icon": "i-print",
         "url": "/customers/receipts/{id}/print", "post": False},
        {"key": "duplicate", "label": "Copy to a new receipt", "icon": "i-refresh",
         "url": "/customers/receipts/new?from={id}", "post": False, "permission": "sales.create"},
        {"key": "delete", "label": "Delete this receipt", "icon": "i-trash",
         "url": "/customers/receipts/{id}/delete", "post": True, "permission": "sales.post",
         "tone": "danger", "blocked_by": "has_allocations",
         "blocked_note": "Matched to an invoice - unmatch it before deleting the receipt.",
         "confirm": "Delete this receipt? Its journal entry is removed from the books and the "
                    "money is no longer received. This cannot be undone."},
    ],
    "journal_entry": [
        {"key": "edit", "label": "Edit this draft", "icon": "i-edit",
         "url": "/accounting/journal-entries/{id}/edit", "post": False,
         "permission": "accounting.create", "draft_only": True,
         "blocked_note": "A posted entry is corrected by resetting it to a draft first."},
        {"key": "reset", "label": "Reset to draft (to correct it)", "icon": "i-arrow-up",
         "url": "/accounting/journal-entries/{id}/reset-to-draft", "post": True,
         "permission": "accounting.post", "posted_only": True,
         "confirm": "Reset this entry to a draft? It stops counting in the books until it is posted again.",
         "blocked_note": "Only a posted entry can be reset."},
        {"key": "duplicate", "label": "Duplicate as a new draft", "icon": "i-refresh",
         "url": "/accounting/journal-entries/{id}/duplicate", "post": True,
         "permission": "accounting.create"},
        {"key": "delete", "label": "Delete this draft", "icon": "i-trash",
         "url": "/accounting/journal-entries/{id}/delete", "post": True,
         "permission": "accounting.create", "draft_only": True, "tone": "danger",
         "confirm": "Delete this draft journal entry?",
         "blocked_note": "A posted entry cannot be deleted - post a reversing entry instead."},
    ],
    "vendor_bill": [
        {"key": "print", "label": "Print / PDF bill", "icon": "i-print",
         "url": "/vendors/bills/{id}/print", "post": False},
        {"key": "print_journal", "label": "Print its journal entry", "icon": "i-journal",
         "url": "/vendors/bills/{id}/journal-print", "post": False},
        {"key": "pay", "label": "Pay this bill", "icon": "i-payment",
         "url": "/vendors/payments/new?bill_id={id}", "post": False, "permission": "vendors.post",
         "posted_only": True,
         "blocked_note": "Only a posted bill can be paid - post it first."},
        {"key": "edit", "label": "Edit this draft", "icon": "i-edit",
         "url": "/vendors/bills/{id}/edit", "post": False, "permission": "vendors.create",
         "draft_only": True,
         "blocked_note": "A posted bill is corrected by resetting it to a draft first."},
        {"key": "reset", "label": "Reset to draft (to correct it)", "icon": "i-arrow-up",
         "url": "/vendors/bills/{id}/reset-to-draft", "post": True, "permission": "vendors.post",
         "posted_only": True,
         "confirm": "Reset this bill to a draft? It stops counting in the books until it is posted "
                    "again, and you can then correct it.",
         "blocked_note": "Only a posted bill can be reset."},
        {"key": "duplicate", "label": "Duplicate as a new draft", "icon": "i-refresh",
         "url": "/vendors/bills/{id}/duplicate", "post": True, "permission": "vendors.create"},
        {"key": "delete", "label": "Delete this draft", "icon": "i-trash",
         "url": "/vendors/bills/{id}/delete", "post": True, "permission": "vendors.create",
         "draft_only": True, "tone": "danger",
         "confirm": "Delete this draft bill? Its lines, its landed costs and its draft journal entry go with it.",
         "blocked_note": "A posted bill cannot be deleted - the books already carry it."},
    ],
    "vendor_payment": [
        {"key": "print", "label": "Print / PDF voucher", "icon": "i-print",
         "url": "/vendors/payments/{id}/print", "post": False},
        {"key": "print_journal", "label": "Print its journal entry", "icon": "i-journal",
         "url": "/vendors/payments/{id}/journal-print", "post": False},
        {"key": "delete", "label": "Delete this payment", "icon": "i-trash",
         "url": "/vendors/payments/{id}/delete", "post": True, "permission": "vendors.post",
         "tone": "danger", "blocked_by": "has_allocations",
         "blocked_note": "Matched to a bill - it cannot be deleted.",
         "confirm": "Delete this payment? Its journal entry is removed from the books."},
    ],
}


def build_menu(kind, document_id, status=None, user_id=None, organization_id=None, extra=None):
    """The items of the gear menu for one document, in the order they are shown.

    Each item is a dict the template can draw without knowing anything about the
    document. An item that the rule forbids comes back with `allowed` False and
    a `note` saying why; an item the person has no permission for does not come
    back at all.
    """
    extra = extra or {}
    items = []
    for action in ACTIONS.get(kind, []):
        if not _may(user_id, organization_id, action.get("permission")):
            continue
        item = {
            "key": action["key"],
            "label": action["label"],
            "icon": action["icon"],
            "url": action["url"].format(id=document_id),
            "post": action.get("post", False),
            "confirm": action.get("confirm"),
            "tone": action.get("tone"),
            "note": None,
            "allowed": True,
        }
        if action.get("draft_only") and (status or "").lower() != "draft":
            item["allowed"] = False
            item["note"] = action.get("blocked_note") or "Only a draft can be changed this way."
        if action.get("posted_only") and (status or "").lower() == "draft":
            item["allowed"] = False
            item["note"] = action.get("blocked_note") or "Only a posted document can be reset."
        if action.get("blocked_by") and extra.get(action["blocked_by"]):
            item["allowed"] = False
            item["note"] = action.get("blocked_note") or "This cannot be deleted."
        items.append(item)
    return items


def has_any(items):
    """Is there anything in this menu at all? (The gear is not drawn if not.)"""
    return bool(items)
