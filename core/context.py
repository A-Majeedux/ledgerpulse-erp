"""
core/context.py

Every segment needs to know "which organization's data am I looking at
right now?" This file is the one place that answers that question, so it's
answered the same way everywhere in the app.

HOW TO USE THIS IN A NEW SEGMENT:
    from core.context import current_organization_id

    @bp.route("/vendors")
    def vendors_list():
        org_id = current_organization_id()
        cursor.execute(
            "SELECT * FROM vendors WHERE organization_id = %s", (org_id,)
        )
        ...

Every query in every segment MUST filter by organization_id this way.
Forgetting it is how one organization's data would leak into another's.
"""
from flask import session, redirect, url_for
from functools import wraps


def current_organization_id():
    """
    Returns the organization_id of whichever organization the user is
    currently working in, or None if they haven't picked one yet.
    """
    return session.get("organization_id")


def set_current_organization(organization_id):
    session["organization_id"] = organization_id


def require_organization(view_function):
    """
    Put this decorator on any route that needs an active organization to
    make sense (almost every route in the app). If nobody has picked an
    organization yet, it sends them to the picker screen instead of
    letting the page crash or show empty/wrong data.

    Example:
        @bp.route("/vendors")
        @require_organization
        def vendors_list():
            ...
    """
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        if current_organization_id() is None:
            return redirect(url_for("organizations.pick_organization"))
        return view_function(*args, **kwargs)
    return wrapped
