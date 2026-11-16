"""
core/searchbar.py

One search bar, the same on every list.

His words: "add a search bar in every module where user can search any entry by
partner name, label, exp head, draft, posted, data reference, month reference or
can customize the search". So the bar reads the query string into a small
`Search` object, and hands the list route ready-made SQL conditions:

    search = read_search(request)
    clauses, params = search_filters(
        search,
        text_columns=["si.invoice_no", "c.name"],     # reference, partner, label
        status_column="si.status",                    # Draft / Posted
        date_column="si.invoice_date",                # month and date range
        partner_column="c.name",
        account_column="sil.account_id",              # expense head
    )
    ... "WHERE si.organization_id = %s " + and_clauses(clauses), [org_id] + params

Nothing here trusts the query string: every value is either matched with a bound
parameter or is one of the fixed choices below, and column names come from the
route (never from the URL).
"""
from dataclasses import dataclass, field

STATUS_CHOICES = ("Draft", "Posted")
MAX_QUERY_LENGTH = 80


@dataclass
class Search:
    """What the person typed into the bar."""
    q: str = ""
    status: str = ""
    partner: str = ""
    month: str = ""          # "2026-09"
    date_from: str = ""
    date_to: str = ""
    account: str = ""        # account_id, or "" for any
    sort: str = ""
    extra: dict = field(default_factory=dict)
    description: str = ""    # filled by the route, shown under the bar

    @property
    def active(self):
        return bool(self.q or self.status or self.partner or self.month
                    or self.date_from or self.date_to or self.account)

    def as_dict(self):
        """What the template hands back, so the boxes stay filled."""
        return {
            "q": self.q, "status": self.status, "partner": self.partner,
            "month": self.month, "date_from": self.date_from, "date_to": self.date_to,
            "account": self.account, "sort": self.sort,
        }


def _clean(value, limit=MAX_QUERY_LENGTH):
    return (value or "").strip()[:limit]


def read_search(request):
    """Read the search bar out of the query string."""
    args = request.args
    status = _clean(args.get("status"), 20)
    if status and status.lower() not in ("draft", "posted"):
        status = ""
    month = _clean(args.get("month"), 7)
    if month and not _looks_like_month(month):
        month = ""
    return Search(
        q=_clean(args.get("q")),
        status=status.capitalize() if status else "",
        partner=_clean(args.get("partner")),
        month=month,
        date_from=_clean_date(args.get("date_from")),
        date_to=_clean_date(args.get("date_to")),
        account=_clean(args.get("account"), 12),
        extra={key: _clean(value) for key, value in args.items()
               if key.startswith("f_") and _clean(value)},
    )


def _looks_like_month(text):
    if len(text) != 7 or text[4] != "-":
        return False
    return text[:4].isdigit() and text[5:].isdigit() and 1 <= int(text[5:]) <= 12


def _clean_date(text):
    """A date box only passes through if it is a real YYYY-MM-DD date."""
    text = _clean(text, 10)
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return ""
    if not (text[:4].isdigit() and text[5:7].isdigit() and text[8:].isdigit()):
        return ""
    return text


def search_filters(search, text_columns=(), status_column=None, date_column=None,
                   partner_column=None, account_column=None, extra_conditions=()):
    """Turn a Search into SQL conditions (no user text is ever pasted in).

    `text_columns` are matched with ILIKE against q; `partner_column` separately,
    so a partner name can be asked for on its own even when the free-text box has
    something else in it. `status_column`, `date_column` and `account_column` are
    column or expression strings chosen by the route.
    """
    clauses, params = [], []

    if search.q and text_columns:
        pattern = "%" + search.q + "%"
        parts = ["COALESCE({}::text, '') ILIKE %s".format(column) for column in text_columns]
        clauses.append("(" + " OR ".join(parts) + ")")
        params.extend([pattern] * len(parts))

    if search.partner and partner_column:
        clauses.append("COALESCE({}::text, '') ILIKE %s".format(partner_column))
        params.append("%" + search.partner + "%")

    if search.status and status_column:
        if search.status == "Draft":
            clauses.append("LOWER(COALESCE({}::text, '')) IN ('draft', 'draft entry')".format(status_column))
        else:
            clauses.append("LOWER(COALESCE({}::text, '')) NOT IN ('draft', 'draft entry')".format(status_column))

    if search.month and date_column:
        clauses.append("TO_CHAR({}, 'YYYY-MM') = %s".format(date_column))
        params.append(search.month)

    if search.date_from and date_column:
        clauses.append("{} >= %s".format(date_column))
        params.append(search.date_from)

    if search.date_to and date_column:
        clauses.append("{} <= %s".format(date_column))
        params.append(search.date_to)

    if search.account and account_column:
        clauses.append("{}::text = %s".format(account_column))
        params.append(search.account)

    for condition, values in extra_conditions:
        clauses.append(condition)
        params.extend(values)

    return clauses, params


def and_clauses(clauses):
    """Join conditions for appending to an existing WHERE."""
    if not clauses:
        return ""
    return " AND " + " AND ".join(clauses)


def describe(search, accounts=()):
    """A short sentence naming what the list is currently filtered to."""
    words = []
    if search.q:
        words.append('matching "%s"' % search.q)
    if search.partner:
        words.append("for %s" % search.partner)
    if search.status:
        words.append(search.status.lower())
    if search.month:
        words.append("in %s" % month_name(search.month))
    if search.date_from and search.date_to:
        words.append("from %s to %s" % (search.date_from, search.date_to))
    elif search.date_from:
        words.append("from %s" % search.date_from)
    elif search.date_to:
        words.append("up to %s" % search.date_to)
    if search.account:
        for account_id, label in accounts:
            if str(account_id) == str(search.account):
                words.append("on %s" % label)
                break
    return ("Showing entries " + ", ".join(words) + ".") if words else ""


MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def month_name(text):
    """'2026-09' -> 'September 2026'; anything else is returned untouched."""
    if not _looks_like_month(text):
        return text
    return "%s %s" % (MONTHS[int(text[5:]) - 1], text[:4])


# ---------------------------------------------------------------------------
# Suggestions from history
#
# His words: "the search bar should show auto suggestions from history for
# written inputs". A <datalist> under the box, filled with the values that have
# actually been used - invoice numbers, customer names, line labels - so typing
# "INV/2026" or "Chaudhry" brings up what is really in the books. The browser
# filters it as he types; nothing is fetched and no JavaScript is needed.
# ---------------------------------------------------------------------------
def suggest(cursor, parts, limit=60):
    """Distinct values to offer in the box.

    `parts` is a list of (sql, params) pairs, each selecting one text column -
    most recent first. Empty values are dropped and the result is de-duplicated
    without regard to case, because "Chaudhry" and "CHaudhry" are one customer to
    the person typing, not two.
    """
    values, seen = [], set()
    for sql, params in parts:
        cursor.execute(sql, params or ())
        for (value,) in cursor.fetchall():
            if not value:
                continue
            key = str(value).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(str(value).strip())
            if len(values) >= limit:
                return values
    return values
