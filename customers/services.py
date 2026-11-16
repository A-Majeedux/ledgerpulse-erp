"""
customers/services.py

The rules of the customer card, kept apart from the web pages
(customers/routes.py) so that each rule can be read and tested on its own:

  - what a filled-in customer form must look like before it is saved
  - how a customer gets its ID (a running number inside its company)
  - when a customer's name may still be changed
  - reading and writing the customers table, always for ONE organization

Nothing in this file creates, changes or posts a journal entry.

WHY A NAME CAN BE LOCKED, AND WHY TWO CUSTOMERS CANNOT SHARE A NAME (YET)
    A journal line remembers its customer by NAME (journal_lines.partner_name),
    not by ID. So if a customer that already has entries were renamed, its old
    entries would stay under the old name and its balance would split in two.
    And two customers with the same name would be added together as if they
    were one. Until journal lines store the customer's ID, the name of a
    customer with entries is locked, and a second customer with the same name
    is refused.

WHERE THE RULES COME FROM
    The boxes, the ID and the list columns follow the owner's own customer
    screens (14 Nov 2026). The limits on each box are the limits of the
    columns in customers/schema.py - a longer text is refused with a message
    here rather than reaching the database and crashing the page.

    This module keeps its own ValidationError, and its own reading of an
    amount typed into the form, because ZYRO has no shared form-checking file
    yet. Both are small and live here; if another module ever needs the same
    checks, this is the place they can be lifted out of.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from core.money import money_round, to_decimal

NATURE_OF_SUPPLY_OPTIONS = ["Supplies", "Services"]

# What a buyer is, for sales tax. The first is what most cards are.
REGISTRATION_OPTIONS = ["Registered", "Unregistered", "Retail"]

# The provinces a sale can fall under (Islamabad is not a province, but it is
# where the federal capital territory's sales tax is administered).
PROVINCE_OPTIONS = ["Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan",
                    "Islamabad Capital Territory", "Gilgit-Baltistan", "Azad Kashmir"]

NAME_MAX_LENGTH = 150                           # customers.name and journal_lines.partner_name
MAX_CREDIT_LIMIT = Decimal("999999999999.99")   # the largest amount NUMERIC(14, 2) can hold
SEARCH_MAX_LENGTH = 100
SEARCH_MAX_WORDS = 6

# Tags: words the bookkeeper files a customer under. Typed as one line,
# separated by commas; kept as one line in customers.tags.
TAG_MAX_LENGTH = 40                     # one tag
TAGS_MAX_COUNT = 20                     # how many tags one customer may carry
TAGS_COLUMN_WIDTH = 255                 # customers.tags

# The currency he deals with the customer in - three letters, for reference.
CURRENCY_MAX_LENGTH = 10                # customers.currency
WEBSITE_MAX_LENGTH = 255                # customers.website

# The account every customer's balance sits on (ZYRO's own convention: the
# account coded 1100, "Trade Receivables" - the same code the receipt screen
# posts to). A journal line on this account that carries the customer's name
# means the customer has entries in the books.
TRADE_RECEIVABLE_CODE = "1100"

# The optional text boxes of the card: (form field = column, label on screen,
# longest text the column can hold).
TEXT_FIELDS = [
    ("email", "Email", 150),
    ("phone", "Phone", 50),
    ("address", "Street", 500),
    ("street2", "Street 2", 200),
    ("city", "City", 100),
    ("state", "State", 100),
    ("zip_code", "ZIP", 20),
    ("country", "Country", 100),
    ("region", "Region", 50),
    ("territory", "Territory", 100),
    ("category_parent", "Category Parent", 100),
    ("category", "Category", 100),
    ("lic_number", "LIC Number", 50),
    ("ntn", "NTN", 30),
    ("strn", "STRN", 30),
    ("cnic", "CNIC", 20),
    ("province", "Province", 50),
]

# Boxes that offer, while typing, the values this company's other customers
# already use (so "KSR" is not typed as "Kasur" next time).
SUGGESTED_FIELDS = ("region", "territory", "category_parent", "category", "city", "state", "country")

# Every column of a customer, in the order the SELECTs below return them.
CUSTOMER_COLUMNS = [
    "customer_id", "customer_no", "name", "is_company", "nature_of_supply", "ntn", "strn", "cnic",
    "address", "street2", "city", "state", "zip_code", "country", "region", "territory",
    "category_parent", "category", "lic_number", "lic_expiry_date", "email", "phone",
    "credit_limit", "is_active", "tags", "is_vendor", "currency", "website", "notes",
    # tax and terms (15 Nov 2026)
    "province", "registration_type", "is_filer", "withholds_wht", "default_wht_rate", "payment_terms_days",
]
_SELECT_CUSTOMER = "SELECT " + ", ".join(CUSTOMER_COLUMNS) + " FROM customers"

# The columns of the customer list, in the order they are shown.
#   shown : is the column visible before the user has ticked or unticked anything?
#   kind  : how the value is written (text / number / amount / date / name)
# "Name" is always shown, so it has no tick-box.
LIST_COLUMNS = [
    {"key": "customer_no", "label": "ID", "shown": True, "kind": "number"},
    {"key": "name", "label": "Name", "shown": True, "kind": "name", "fixed": True},
    {"key": "credit_limit", "label": "Credit Limit", "shown": True, "kind": "amount"},
    {"key": "ntn", "label": "NTN", "shown": False, "kind": "text"},
    {"key": "email", "label": "Email", "shown": True, "kind": "text"},
    {"key": "phone", "label": "Phone", "shown": True, "kind": "text"},
    {"key": "region", "label": "Region", "shown": True, "kind": "text"},
    {"key": "territory", "label": "Territory", "shown": True, "kind": "text"},
    {"key": "lic_number", "label": "LIC Number", "shown": True, "kind": "text"},
    {"key": "lic_expiry_date", "label": "LIC Expiry Date", "shown": True, "kind": "date"},
    {"key": "category_parent", "label": "Category Parent", "shown": True, "kind": "text"},
    {"key": "address", "label": "Street", "shown": False, "kind": "text"},
    {"key": "city", "label": "City", "shown": False, "kind": "text"},
    {"key": "state", "label": "State", "shown": False, "kind": "text"},
    {"key": "country", "label": "Country", "shown": True, "kind": "text"},
    {"key": "strn", "label": "STRN", "shown": False, "kind": "text"},
    {"key": "cnic", "label": "CNIC", "shown": False, "kind": "text"},
    {"key": "nature_of_supply", "label": "Nature of Supply", "shown": False, "kind": "text"},
    {"key": "category", "label": "Category", "shown": False, "kind": "text"},
    {"key": "province", "label": "Province", "shown": False, "kind": "text"},
    {"key": "registration_type", "label": "Registration", "shown": False, "kind": "text"},
]

NAME_LOCKED_MESSAGE = (
    "The name of this customer cannot be changed: the customer already has entries "
    "in the books, and a journal entry remembers its customer by name."
)


# ---------------------------------------------------------------------
# What was typed
# ---------------------------------------------------------------------
class ValidationError(Exception):
    """
    Something typed into the card cannot be accepted. The message is written
    for the person at the keyboard and is safe to show on the form as it is.
    """


# Characters no keyboard types (they only arrive from a script). One of them,
# the NUL character, cannot be stored in PostgreSQL text at all.
_UNTYPEABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_amount(raw, label, allow_zero=False):
    """
    An amount typed into a box -> a real, finite Decimal rounded to 2 decimal
    places the way every amount in ZYRO is rounded - or a ValidationError
    with a message for the person at the keyboard.

    Negative amounts are never accepted: a correction is its own document,
    not a negative one.
    """
    text = raw.strip() if isinstance(raw, str) else raw
    if text is None or text == "":
        raise ValidationError(f"{label} is required.")
    try:
        value = to_decimal(text)
    except (ValueError, InvalidOperation):
        raise ValidationError(f"{label} must be a number.")
    # Decimal also accepts the words "NaN" and "Infinity", and a number too
    # long to round at all. Neither may reach the ledger.
    if not value.is_finite():
        raise ValidationError(f"{label} must be a number.")
    value = money_round(value)
    if value < 0:
        raise ValidationError(f"{label} cannot be negative.")
    if value == 0 and not allow_zero:
        raise ValidationError(f"{label} must be greater than zero.")
    return value


def clean_line(raw):
    """One line of typed text: trimmed, and every run of spaces made one space."""
    text = _UNTYPEABLE.sub("", raw) if isinstance(raw, str) else ""
    return re.sub(r"\s+", " ", text).strip()


def clean_block(raw):
    """Text that may have several lines (the Street box): each line trimmed, blank lines dropped."""
    text = _UNTYPEABLE.sub("", raw) if isinstance(raw, str) else ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def clean_tags(raw):
    """
    The tags box -> one line of tags ("a, b, c"), or None when it is empty.

    Commas separate the tags; a tag is trimmed, a tag typed twice (in any
    capitalisation) is kept once, and the order he typed is kept. Raises
    ValidationError when a tag is too long, there are too many, or they do
    not fit the column.
    """
    text = _UNTYPEABLE.sub("", raw) if isinstance(raw, str) else ""
    tags, seen = [], set()
    for part in text.split(","):
        tag = re.sub(r"\s+", " ", part).strip()
        if not tag:
            continue
        if len(tag) > TAG_MAX_LENGTH:
            raise ValidationError(f"Each tag must be at most {TAG_MAX_LENGTH} characters.")
        if tag.lower() in seen:         # typed twice: kept once
            continue
        seen.add(tag.lower())
        tags.append(tag)
    if len(tags) > TAGS_MAX_COUNT:
        raise ValidationError(f"At most {TAGS_MAX_COUNT} tags on one customer.")
    joined = ", ".join(tags)
    if len(joined) > TAGS_COLUMN_WIDTH:
        raise ValidationError(
            f"These tags are together too long (maximum {TAGS_COLUMN_WIDTH} characters). "
            f"Keep fewer of them, or make them shorter."
        )
    return joined or None


def tag_list(tags):
    """'Punjab, VIP' -> ['Punjab', 'VIP'] for showing them one by one."""
    return [tag.strip() for tag in (tags or "").split(",") if tag.strip()]


_WEBSITE_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%")


def clean_website(raw):
    """
    The Website box -> the address as typed, or None when it is empty.

    Only a shape is checked - it must look like a web address, not be a real
    one; nothing is opened or fetched. A stray "https://" is accepted, and so
    is "www.example.com".
    """
    text = clean_line(raw)
    if not text:
        return None
    if len(text) > WEBSITE_MAX_LENGTH:
        raise ValidationError(f"Website is too long (maximum {WEBSITE_MAX_LENGTH} characters).")
    host = text.split("//")[-1].split("/")[0]
    if (any(character not in _WEBSITE_CHARACTERS for character in text)
            or " " in text or "." not in host or host.startswith(".") or host.endswith(".")):
        raise ValidationError("Website does not look like a web address (for example www.example.com).")
    return text


_CURRENCY = re.compile(r"^[A-Z]{3}$")


def clean_currency(raw):
    """
    The Currency box -> a three-letter code in capitals (PKR, USD), or None.

    Empty means "the company's own currency". This is a note on the card
    only: ZYRO keeps the books in the company's currency and posts nothing
    in any other currency (foreign-currency postings are not built).
    """
    text = clean_line(raw).upper()
    if not text:
        return None
    if not _CURRENCY.match(text):
        raise ValidationError("Currency must be a three-letter code (for example PKR).")
    return text


def same_name(one, other):
    """Are two names the same text once stray spaces are ignored? (Capital letters DO count.)"""
    return clean_line(one) == clean_line(other)


def clean_customer_form(form, editing=False):
    """
    Check everything typed into the customer card.

    Returns (data, problems):
      data      the values ready to be saved, keyed by column name
      problems  messages for the person at the keyboard, one per mistake.
                When this list is not empty, NOTHING may be saved.

    `form` is anything with .get(name) - request.form, or a plain dict.
    With editing=True the Status box (Active / Inactive) is read as well;
    data["is_active"] is None when the form did not send it.
    """
    data, problems = {}, []

    name = clean_line(form.get("name"))
    if not name:
        problems.append("Customer name is required.")
    elif len(name) > NAME_MAX_LENGTH:
        problems.append(f"Customer name is too long (maximum {NAME_MAX_LENGTH} characters).")
    data["name"] = name

    partner_type = clean_line(form.get("partner_type")) or "company"
    if partner_type not in ("company", "person"):
        problems.append("Choose Person or Company.")
    data["is_company"] = partner_type != "person"

    nature = clean_line(form.get("nature_of_supply")) or NATURE_OF_SUPPLY_OPTIONS[0]
    if nature not in NATURE_OF_SUPPLY_OPTIONS:
        problems.append("Nature of supply must be Supplies or Services.")
    data["nature_of_supply"] = nature

    for field, label, max_length in TEXT_FIELDS:
        text = clean_block(form.get(field)) if field == "address" else clean_line(form.get(field))
        if len(text) > max_length:
            problems.append(f"{label} is too long (maximum {max_length} characters).")
        data[field] = text or None

    if data["email"] and not _EMAIL.match(data["email"]):
        problems.append("Email does not look like an email address (for example name@example.com).")
    if data["phone"] and not any(character.isdigit() for character in data["phone"]):
        problems.append("Phone must contain a number.")

    data["lic_expiry_date"] = None
    typed_date = clean_line(form.get("lic_expiry_date"))
    if typed_date:
        try:
            expiry = date.fromisoformat(typed_date)
        except ValueError:
            problems.append("LIC Expiry Date is not a valid date.")
        else:
            if 1900 <= expiry.year <= 2200:
                data["lic_expiry_date"] = expiry
            else:
                problems.append("LIC Expiry Date must be between the years 1900 and 2200.")

    data["credit_limit"] = Decimal("0.00")
    typed_limit = clean_line(form.get("credit_limit"))
    if typed_limit:                     # an empty box means "no limit set": 0.00
        try:
            limit = parse_amount(typed_limit, "Credit limit", allow_zero=True)
        except ValidationError as error:
            problems.append(str(error))
        except ArithmeticError:         # a number too long to be rounded at all, e.g. 1e999999
            problems.append("Credit limit is too large (the most it can be is 999,999,999,999.99).")
        else:
            if limit > MAX_CREDIT_LIMIT:
                problems.append("Credit limit is too large (the most it can be is 999,999,999,999.99).")
            else:
                data["credit_limit"] = limit + Decimal("0.00")      # "-0" becomes plain 0.00

    # The four boxes of the "rest of the card": tags, the also-a-vendor mark,
    # the currency he deals in, and the website.
    for field, cleaner, label in (("tags", clean_tags, "Tags"),
                                  ("website", clean_website, "Website"),
                                  ("currency", clean_currency, "Currency")):
        try:
            data[field] = cleaner(form.get(field))
        except ValidationError as error:
            problems.append(str(error))
            data[field] = None
    data["is_vendor"] = str(form.get("is_vendor") or "").strip().lower() in ("1", "on", "true", "yes")

    # Tax and terms. Registration type is one of three offered answers; the two
    # ticks are ticks; the rate and the terms are numbers with a sane range, and
    # a blank box means "not set" rather than zero.
    registration = clean_line(form.get("registration_type")) or REGISTRATION_OPTIONS[0]
    if registration not in REGISTRATION_OPTIONS:
        problems.append("Registration must be Registered, Unregistered or Retail.")
    data["registration_type"] = registration
    data["is_filer"] = str(form.get("is_filer") or "").strip().lower() in ("1", "on", "true", "yes")
    data["withholds_wht"] = str(form.get("withholds_wht") or "").strip().lower() in ("1", "on", "true", "yes")

    data["default_wht_rate"] = None
    typed_rate = clean_line(form.get("default_wht_rate"))
    if typed_rate:
        try:
            rate = Decimal(typed_rate)
        except (InvalidOperation, ArithmeticError):
            problems.append("Default WHT rate must be a number (for example 4.5).")
        else:
            if not Decimal("0") <= rate <= Decimal("100"):
                problems.append("Default WHT rate must be between 0 and 100.")
            else:
                data["default_wht_rate"] = rate.quantize(Decimal("0.01"))

    data["payment_terms_days"] = None
    typed_days = clean_line(form.get("payment_terms_days"))
    if typed_days:
        if not typed_days.isdigit():
            problems.append("Payment terms must be a whole number of days (for example 30).")
        elif int(typed_days) > 365:
            problems.append("Payment terms cannot be longer than 365 days.")
        else:
            data["payment_terms_days"] = int(typed_days)

    if editing:
        status = clean_line(form.get("status"))
        if status == "":
            data["is_active"] = None    # not sent: leave the customer as it is
        elif status in ("active", "inactive"):
            data["is_active"] = status == "active"
        else:
            problems.append("Status must be Active or Inactive.")
            data["is_active"] = None

    return data, problems


# ---------------------------------------------------------------------
# What the card shows
# ---------------------------------------------------------------------
FORM_FIELDS = (["name", "partner_type", "nature_of_supply", "lic_expiry_date", "credit_limit", "status",
                "tags", "is_vendor", "currency", "website",
                "registration_type", "is_filer", "withholds_wht", "default_wht_rate", "payment_terms_days"]
               + [field for field, _label, _max_length in TEXT_FIELDS])


def blank_form():
    """The boxes of an empty New Customer card."""
    values = {field: "" for field in FORM_FIELDS}
    values.update(partner_type="company", nature_of_supply=NATURE_OF_SUPPLY_OPTIONS[0],
                  credit_limit="0.00", status="active",
                  registration_type=REGISTRATION_OPTIONS[0],
                  province="Punjab")
    return values


def form_from_customer(customer):
    """The boxes of the Edit card, filled from a saved customer."""
    values = blank_form()
    for field in FORM_FIELDS:
        if field in customer and customer[field] is not None:
            values[field] = str(customer[field])
    values["partner_type"] = "company" if customer["is_company"] else "person"
    values["lic_expiry_date"] = customer["lic_expiry_date"].isoformat() if customer["lic_expiry_date"] else ""
    values["credit_limit"] = f"{Decimal(customer['credit_limit'] or 0):.2f}"
    values["status"] = "active" if customer["is_active"] is not False else "inactive"
    values["tags"] = customer["tags"] or ""
    values["website"] = customer["website"] or ""
    values["currency"] = customer["currency"] or ""
    values["is_vendor"] = "1" if customer["is_vendor"] else ""      # a tick-box: "1" or empty
    values["registration_type"] = customer.get("registration_type") or REGISTRATION_OPTIONS[0]
    values["is_filer"] = "1" if customer.get("is_filer") else ""
    values["withholds_wht"] = "1" if customer.get("withholds_wht") else ""
    rate = customer.get("default_wht_rate")
    values["default_wht_rate"] = f"{Decimal(rate):.2f}" if rate is not None else ""
    days = customer.get("payment_terms_days")
    values["payment_terms_days"] = str(days) if days is not None else ""
    return values


def form_as_typed(form, base=None):
    """
    The boxes exactly as the user left them, for showing a refused card
    again without losing anything that was typed. Boxes the request did
    not send keep the value from `base` (the blank or the saved card).
    """
    values = dict(base or blank_form())
    for field in FORM_FIELDS:
        typed = form.get(field)
        if isinstance(typed, str):
            values[field] = _UNTYPEABLE.sub("", typed)
    # A tick-box the browser did not send was unticked by the user.
    for tick in ("is_vendor", "is_filer", "withholds_wht"):
        values[tick] = "1" if form.get(tick) else ""
    return values


# ---------------------------------------------------------------------
# How values are written on the list
# ---------------------------------------------------------------------
def format_amount(value):
    """1234567.5 -> '1,234,567.50' (thousands separators, 2 decimals)."""
    return f"{Decimal(value or 0):,.2f}"


def format_date(value):
    """date(2027, 3, 5) -> '05 Mar 2027'; nothing -> ''."""
    return value.strftime("%d %b %Y") if value else ""


def list_row(customer):
    """One line of the customer list: every column already written as text."""
    cells = {}
    for column in LIST_COLUMNS:
        value = customer.get(column["key"])
        if column["kind"] == "amount":
            cells[column["key"]] = format_amount(value)
        elif column["kind"] == "date":
            cells[column["key"]] = format_date(value)
        else:
            cells[column["key"]] = "" if value is None else " ".join(str(value).split())
    return {"customer_id": customer["customer_id"], "is_active": customer["is_active"] is not False, "cells": cells}


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------
def clean_search(raw):
    """What was typed into the search box, trimmed and cut to a sensible length."""
    return clean_line(raw)[:SEARCH_MAX_LENGTH].strip()


def like_pattern(word):
    r"""
    A word -> the pattern "contains this word" for SQL ILIKE ... ESCAPE '\'.
    The characters % and _ mean "anything" to ILIKE; typed by a user they
    must mean themselves ("50%" finds "50%", not everything with "50").
    """
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------
def _as_customer(row):
    return dict(zip(CUSTOMER_COLUMNS, row)) if row else None


def get_customer(cursor, organization_id, customer_id):
    """One customer of THIS organization, or None (also when it belongs to another organization)."""
    cursor.execute(_SELECT_CUSTOMER + " WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, organization_id))
    return _as_customer(cursor.fetchone())


def count_customers(cursor, organization_id):
    cursor.execute("SELECT count(*) FROM customers WHERE organization_id = %s", (organization_id,))
    return cursor.fetchone()[0]


def list_customers(cursor, organization_id, search=""):
    """
    The company's customers by name. With a search text, only those where
    EVERY typed word is found somewhere: in the name, region, territory,
    city, both categories, NTN, CNIC, LIC number, phone or email, or is
    exactly the customer's ID.
    """
    sql = _SELECT_CUSTOMER + " WHERE organization_id = %s"
    arguments = [organization_id]
    for word in clean_search(search).split()[:SEARCH_MAX_WORDS]:
        pattern = like_pattern(word)
        sql += r"""
            AND (name ILIKE %s ESCAPE '\' OR region ILIKE %s ESCAPE '\' OR territory ILIKE %s ESCAPE '\'
                 OR city ILIKE %s ESCAPE '\' OR category_parent ILIKE %s ESCAPE '\' OR category ILIKE %s ESCAPE '\'
                 OR ntn ILIKE %s ESCAPE '\' OR cnic ILIKE %s ESCAPE '\' OR lic_number ILIKE %s ESCAPE '\'
                 OR phone ILIKE %s ESCAPE '\' OR email ILIKE %s ESCAPE '\'
                 OR CAST(customer_no AS TEXT) = %s)"""
        arguments += [pattern] * 11 + [word]
    sql += " ORDER BY lower(name), customer_no, customer_id"
    cursor.execute(sql, arguments)
    return [_as_customer(row) for row in cursor.fetchall()]


def field_suggestions(cursor, organization_id):
    """{box: [values this company's customers already use]} for the boxes in SUGGESTED_FIELDS."""
    cursor.execute("""
        SELECT 'region', region FROM customers WHERE organization_id = %s AND region IS NOT NULL
        UNION SELECT 'territory', territory FROM customers WHERE organization_id = %s AND territory IS NOT NULL
        UNION SELECT 'category_parent', category_parent FROM customers WHERE organization_id = %s AND category_parent IS NOT NULL
        UNION SELECT 'category', category FROM customers WHERE organization_id = %s AND category IS NOT NULL
        UNION SELECT 'city', city FROM customers WHERE organization_id = %s AND city IS NOT NULL
        UNION SELECT 'state', state FROM customers WHERE organization_id = %s AND state IS NOT NULL
        UNION SELECT 'country', country FROM customers WHERE organization_id = %s AND country IS NOT NULL
        ORDER BY 1, 2
    """, (organization_id,) * 7)
    suggestions = {field: [] for field in SUGGESTED_FIELDS}
    for field, value in cursor.fetchall():
        if len(suggestions[field]) < 200:
            suggestions[field].append(value)
    return suggestions


# ---------------------------------------------------------------------
# The two name rules (see the note at the top of this file)
# ---------------------------------------------------------------------
def name_lock_reason(cursor, organization_id, customer):
    """
    NAME_LOCKED_MESSAGE when this customer already has entries, else None.

    "Has entries" means any one of:
      - a receipt from this customer
      - an invoice to this customer (even a draft one already has its draft
        journal entry, written with the name)
      - a journal line on the Trade Receivables account (code 1100) of this
        company carrying this name - a manual journal entry, for example
    """
    cursor.execute("""
        SELECT
            EXISTS (SELECT 1 FROM customer_receipts
                    WHERE organization_id = %s AND customer_id = %s)
         OR EXISTS (SELECT 1 FROM sales_invoices
                    WHERE organization_id = %s AND customer_id = %s)
         OR EXISTS (SELECT 1
                    FROM journal_lines line
                    JOIN journal_headers entry ON entry.journal_header_id = line.journal_header_id
                    JOIN chart_of_accounts account ON account.account_id = line.account_id
                    WHERE entry.organization_id = %s
                      AND account.organization_id = %s
                      AND account.code = %s
                      AND line.partner_name = %s)
    """, (organization_id, customer["customer_id"], organization_id, customer["customer_id"],
          organization_id, organization_id, TRADE_RECEIVABLE_CODE, customer["name"]))
    return NAME_LOCKED_MESSAGE if cursor.fetchone()[0] else None


def customer_with_name(cursor, organization_id, name, except_customer_id=None):
    """
    Another customer of this company that already uses this name
    (capital letters and extra spaces ignored), as (ID, name) - or None.
    """
    cursor.execute(r"""
        SELECT customer_no, name FROM customers
        WHERE organization_id = %s
          AND lower(regexp_replace(btrim(name), '\s+', ' ', 'g')) = lower(%s)
          AND (%s IS NULL OR customer_id <> %s)
        ORDER BY customer_id
        LIMIT 1
    """, (organization_id, clean_line(name), except_customer_id, except_customer_id))
    return cursor.fetchone()


def _refuse_a_tax_number_already_in_use(cursor, organization_id, data, except_customer_id=None):
    """Refuse a second customer carrying the same NTN or CNIC.

    The name rule already stops the same words twice, but the same business
    typed as "Gulf Agro" and "Gulf Agro Chemicals (Pvt) Ltd" slips past it - and
    then its balance is split in two and the ageing shows two small rows. A tax
    number is unique to one business, so it is the honest key to refuse on.

    Blank numbers are not compared: most walk-in customers have none, and many
    of them would share an empty box.
    """
    for field, label in (("ntn", "NTN"), ("cnic", "CNIC")):
        value = clean_line(data.get(field) or "")
        if not value:
            continue
        sql = "SELECT customer_id, name FROM customers WHERE organization_id = %s AND lower(trim(" + field + ")) = lower(%s)"
        arguments = [organization_id, value]
        if except_customer_id:
            sql += " AND customer_id <> %s"
            arguments.append(except_customer_id)
        cursor.execute(sql + " LIMIT 1", arguments)
        row = cursor.fetchone()
        if row:
            raise ValidationError(
                f"{label} {value} is already on customer \"{row[1]}\" (ID {row[0]}). "
                f"One business has one {label} - open that customer instead of adding a second, "
                f"or correct the {label} here."
            )

    # The phone number: compared as digits only, so 0300-1234567 and
    # (0300) 1234567 are the same number. Short numbers are not compared -
    # a box that says "123" or nothing at all is not an identity.
    digits = "".join(character for character in (data.get("phone") or "") if character.isdigit())
    if len(digits) >= 7:
        sql = ("SELECT customer_id, name, phone FROM customers WHERE organization_id = %s "
               "AND phone IS NOT NULL AND phone <> ''")
        arguments = [organization_id]
        if except_customer_id:
            sql += " AND customer_id <> %s"
            arguments.append(except_customer_id)
        cursor.execute(sql, arguments)
        for row_id, row_name, row_phone in cursor.fetchall():
            if "".join(character for character in row_phone if character.isdigit()) == digits:
                raise ValidationError(
                    f"Phone {clean_line(data.get('phone'))} is already on customer \"{row_name}\" (ID {row_id}). "
                    f"If this is the same business, open that customer instead of adding a second one; "
                    f"if two customers really do share a phone, leave the phone blank here."
                )


def _refuse_a_name_already_in_use(cursor, organization_id, name, except_customer_id=None):
    taken = customer_with_name(cursor, organization_id, name, except_customer_id)
    if taken:
        number, existing_name = taken
        raise ValidationError(
            f'A customer named "{existing_name}" already exists (ID {number}). Two customers cannot share a '
            f"name yet, because a journal entry remembers its customer by name. Add something to this name "
            f"that tells the two apart - the town, for example."
        )


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------
def _one_customer_change_at_a_time(cursor, organization_id):
    """
    Two people saving a customer of the same company at the same moment must
    not be given the same ID, or both be allowed the same new name. Holding
    this company's row until the save is committed makes the second save wait
    for the first. (NO KEY UPDATE: it does not hold up anyone who is only
    entering documents for the company.)
    """
    cursor.execute("SELECT 1 FROM organizations WHERE organization_id = %s FOR NO KEY UPDATE", (organization_id,))


def next_customer_no(cursor, organization_id):
    """The next ID of this company: one more than its highest so far (1 for its first customer)."""
    cursor.execute("SELECT COALESCE(MAX(customer_no), 0) + 1 FROM customers WHERE organization_id = %s",
                   (organization_id,))
    return cursor.fetchone()[0]


_SAVED_COLUMNS = (["name", "is_company", "nature_of_supply", "credit_limit", "lic_expiry_date",
                   "tags", "is_vendor", "currency", "website",
                   "registration_type", "is_filer", "withholds_wht", "default_wht_rate",
                   "payment_terms_days"]
                  + [field for field, _label, _max_length in TEXT_FIELDS])


def create_customer(cursor, organization_id, data):
    """
    Save a new customer from clean_customer_form() data and return
    (customer_id, ID). Raises ValidationError when the name is already in use.
    The caller commits.
    """
    _one_customer_change_at_a_time(cursor, organization_id)
    _refuse_a_name_already_in_use(cursor, organization_id, data["name"])
    _refuse_a_tax_number_already_in_use(cursor, organization_id, data)
    number = next_customer_no(cursor, organization_id)
    columns = ["organization_id", "customer_no"] + _SAVED_COLUMNS
    cursor.execute(
        "INSERT INTO customers (" + ", ".join(columns) + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ") "
        "RETURNING customer_id",
        [organization_id, number] + [data[column] for column in _SAVED_COLUMNS],
    )
    return cursor.fetchone()[0], number


def update_customer(cursor, organization_id, customer_id, data):
    """
    Save the Edit card over one customer of THIS company. The ID never
    changes. Raises ValidationError when the name was changed although it is
    locked, or to a name already in use. The caller commits.

    KNOWN LIMIT: the lock is checked against the entries that exist at this
    moment. A document for a brand-new customer that someone else is posting
    in the very same instant can still be written under the old name. That
    gap closes for good when journal lines store the customer's ID.
    """
    _one_customer_change_at_a_time(cursor, organization_id)
    # Read the customer again NOW: nobody else can be changing it any more.
    customer = get_customer(cursor, organization_id, customer_id)
    if customer is None:
        raise ValidationError("This customer no longer exists.")
    values = dict(data)
    if same_name(values["name"], customer["name"]):
        values["name"] = customer["name"]       # untouched: keep it exactly as it is stored
    else:
        if name_lock_reason(cursor, organization_id, customer):
            raise ValidationError(NAME_LOCKED_MESSAGE)
        _refuse_a_name_already_in_use(cursor, organization_id, values["name"], customer["customer_id"])
    _refuse_a_tax_number_already_in_use(cursor, organization_id, values, customer["customer_id"])
    is_active = values.get("is_active")
    if is_active is None:
        is_active = customer["is_active"] is not False
    cursor.execute(
        "UPDATE customers SET " + ", ".join(f"{column} = %s" for column in _SAVED_COLUMNS) + ", is_active = %s "
        "WHERE customer_id = %s AND organization_id = %s",
        [values[column] for column in _SAVED_COLUMNS] + [is_active, customer["customer_id"], organization_id],
    )


# =====================================================================
# The contacts kept under a customer ("Contacts & Addresses")
# =====================================================================
# A customer is one company or person in the books. The people and addresses
# under it - a manager, a delivery address, an accounts office - are kept in
# customer_contacts, and they never post anything to the ledger.
#
# One rule keeps them tidy: as long as a customer has contacts at all, one of
# them is the PRIMARY one. The first contact added becomes primary by itself;
# "Make primary" moves it; removing the primary hands it to the oldest that is
# left. Nothing can leave a customer with two primaries or none.

CONTACT_TEXT_FIELDS = [
    ("job_position", "Job Position", 100),
    ("email", "Email", 150),
    ("phone", "Phone", 50),
    ("street", "Street", 500),
    ("street2", "Street 2", 200),
    ("city", "City", 100),
    ("state", "State", 100),
    ("zip_code", "ZIP", 20),
    ("country", "Country", 100),
]

CONTACT_FORM_FIELDS = ["name", "address_type"] + [field for field, _label, _max_length in CONTACT_TEXT_FIELDS]

# What a contact card IS - the three answers his own card offers.
ADDRESS_TYPE_OPTIONS = ["Invoice Address", "Delivery Address", "Other"]

# The columns a contact card writes. The name is one of them, so a contact
# cannot be saved without one (clean_contact_form refuses it first).
_SAVED_CONTACT_COLUMNS = ["name", "address_type"] + [field for field, _label, _max_length in CONTACT_TEXT_FIELDS]

CONTACT_COLUMNS = (["contact_id", "name"] + _SAVED_CONTACT_COLUMNS + ["is_primary"])
_SELECT_CONTACT = "SELECT " + ", ".join(CONTACT_COLUMNS) + " FROM customer_contacts"


def clean_contact_form(form):
    """
    Check one contact card. Returns (data, problems), the same way
    clean_customer_form does: when problems is not empty nothing is saved.
    """
    data, problems = {}, []

    name = clean_line(form.get("name"))
    if not name:
        problems.append("Contact name is required.")
    elif len(name) > NAME_MAX_LENGTH:
        problems.append(f"Contact name is too long (maximum {NAME_MAX_LENGTH} characters).")
    data["name"] = name

    address_type = clean_line(form.get("address_type")) or "Other"
    if address_type not in ADDRESS_TYPE_OPTIONS:
        problems.append("Contact type must be Invoice Address, Delivery Address or Other.")
    data["address_type"] = address_type

    for field, label, max_length in CONTACT_TEXT_FIELDS:
        text = clean_block(form.get(field)) if field == "street" else clean_line(form.get(field))
        if len(text) > max_length:
            problems.append(f"{label} is too long (maximum {max_length} characters).")
        data[field] = text or None

    if data["email"] and not _EMAIL.match(data["email"]):
        problems.append("Email does not look like an email address (for example name@example.com).")
    if data["phone"] and not any(character.isdigit() for character in data["phone"]):
        problems.append("Phone must contain a number.")

    return data, problems


def blank_contact_form():
    values = {field: "" for field in CONTACT_FORM_FIELDS}
    values["address_type"] = "Other"
    return values


def contact_form_from_contact(contact):
    values = blank_contact_form()
    for field in CONTACT_FORM_FIELDS:
        if contact.get(field) is not None:
            values[field] = str(contact[field])
    return values


def contact_form_as_typed(form, base=None):
    values = dict(base or blank_contact_form())
    for field in CONTACT_FORM_FIELDS:
        typed = form.get(field)
        if isinstance(typed, str):
            values[field] = _UNTYPEABLE.sub("", typed)
    return values


def _as_contact(row):
    return dict(zip(CONTACT_COLUMNS, row)) if row else None


def list_contacts(cursor, organization_id, customer_id):
    """The customer's contacts, the primary one first, then by name."""
    cursor.execute(_SELECT_CONTACT + " WHERE organization_id = %s AND customer_id = %s "
                   "ORDER BY is_primary DESC, lower(name), contact_id",
                   (organization_id, customer_id))
    return [_as_contact(row) for row in cursor.fetchall()]


def get_contact(cursor, organization_id, customer_id, contact_id):
    """One contact of THIS customer and THIS organization, or None."""
    cursor.execute(_SELECT_CONTACT + " WHERE contact_id = %s AND customer_id = %s AND organization_id = %s",
                   (contact_id, customer_id, organization_id))
    return _as_contact(cursor.fetchone())


def count_contacts(cursor, organization_id, customer_id):
    cursor.execute("SELECT count(*) FROM customer_contacts WHERE organization_id = %s AND customer_id = %s",
                   (organization_id, customer_id))
    return cursor.fetchone()[0]


def create_contact(cursor, organization_id, customer_id, data):
    """
    Add a contact to a customer and return its id. The first contact of a
    customer becomes the primary one by itself. The caller commits.
    """
    cursor.execute("SELECT 1 FROM customers WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, organization_id))
    if cursor.fetchone() is None:
        raise ValidationError("This customer no longer exists.")
    is_primary = count_contacts(cursor, organization_id, customer_id) == 0
    columns = ["organization_id", "customer_id"] + _SAVED_CONTACT_COLUMNS + ["is_primary"]
    cursor.execute(
        "INSERT INTO customer_contacts (" + ", ".join(columns) + ") VALUES ("
        + ", ".join(["%s"] * len(columns)) + ") RETURNING contact_id",
        [organization_id, customer_id] + [data[column] for column in _SAVED_CONTACT_COLUMNS] + [is_primary],
    )
    return cursor.fetchone()[0]


def update_contact(cursor, organization_id, customer_id, contact_id, data):
    """Save one contact card. Which contact is primary is not changed here. The caller commits."""
    if get_contact(cursor, organization_id, customer_id, contact_id) is None:
        raise ValidationError("This contact no longer exists.")
    cursor.execute(
        "UPDATE customer_contacts SET " + ", ".join(f"{column} = %s" for column in _SAVED_CONTACT_COLUMNS)
        + " WHERE contact_id = %s AND customer_id = %s AND organization_id = %s",
        [data[column] for column in _SAVED_CONTACT_COLUMNS] + [contact_id, customer_id, organization_id],
    )


def make_primary_contact(cursor, organization_id, customer_id, contact_id):
    """Make one contact the primary one; every other contact of the customer stops being it."""
    if get_contact(cursor, organization_id, customer_id, contact_id) is None:
        raise ValidationError("This contact no longer exists.")
    cursor.execute("UPDATE customer_contacts SET is_primary = FALSE "
                   "WHERE organization_id = %s AND customer_id = %s AND contact_id <> %s",
                   (organization_id, customer_id, contact_id))
    cursor.execute("UPDATE customer_contacts SET is_primary = TRUE WHERE contact_id = %s", (contact_id,))


def delete_contact(cursor, organization_id, customer_id, contact_id):
    """
    Remove one contact. If it was the primary one, the oldest contact that is
    left becomes primary, so the customer keeps exactly one while it has any.
    The caller commits.
    """
    contact = get_contact(cursor, organization_id, customer_id, contact_id)
    if contact is None:
        raise ValidationError("This contact no longer exists.")
    cursor.execute("DELETE FROM customer_contacts WHERE contact_id = %s AND organization_id = %s AND customer_id = %s",
                   (contact_id, organization_id, customer_id))
    if contact["is_primary"]:
        cursor.execute("SELECT contact_id FROM customer_contacts WHERE organization_id = %s AND customer_id = %s "
                       "ORDER BY contact_id LIMIT 1", (organization_id, customer_id))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE customer_contacts SET is_primary = TRUE WHERE contact_id = %s", (row[0],))


# =====================================================================
# The three figures at the top of the card
# =====================================================================
# What they mean, in plain words (they only READ; nothing is posted):
#   Sales      every sales invoice entered for this customer, drafts included
#   Invoiced   the invoices that are in the books - the posted ones
#   Due        Invoiced minus the receipts already received from him
#
# They are found from the documents, by the customer's ID, not from the
# journal lines by name - so a renamed customer cannot split them.
#
# KNOWN LIMIT: a manual journal entry that carries his name on account 1100
# is not in these three figures yet, and neither is anything to do with
# foreign currency (there is none in the system).

def _table_exists(cursor, name):
    """Is this table in the database? Asked without ever raising, because the
    vendor side of the books is made by another part of the program."""
    cursor.execute("SELECT to_regclass(%s)", ("public." + name,))
    return cursor.fetchone()[0] is not None


def list_vendors_for_link(cursor, organization_id):
    """The vendors this customer could be linked to: [(id, name)]."""
    if not _table_exists(cursor, "vendors"):
        return []
    cursor.execute("""
        SELECT vendor_id, name FROM vendors
        WHERE organization_id = %s ORDER BY name
    """, (organization_id,))
    return [(row[0], row[1]) for row in cursor.fetchall()]


def linked_vendor(cursor, organization_id, vendor_id):
    """(vendor_id, name) of the linked vendor, or None."""
    if not vendor_id or not _table_exists(cursor, "vendors"):
        return None
    cursor.execute("SELECT vendor_id, name FROM vendors WHERE vendor_id = %s AND organization_id = %s",
                   (vendor_id, organization_id))
    row = cursor.fetchone()
    return (row[0], row[1]) if row else None


def vendor_purchases(cursor, organization_id, vendor_id):
    """What has been bought from that vendor: {'purchases', 'bills'}.

    The figure is what the vendor bills written for him add up to - the same
    lines the Purchases screen prints - and it is zero when the customer is
    not linked to a vendor at all.
    """
    empty = {"purchases": money_round(0), "purchases_text": format_amount(0), "bills": 0}
    if not vendor_id:
        return empty
    if not (_table_exists(cursor, "vendor_bills") and _table_exists(cursor, "vendor_bill_lines")):
        return empty
    cursor.execute("""
        SELECT COUNT(DISTINCT bill.vendor_bill_id), COALESCE(SUM(line.total), 0)
        FROM vendor_bills bill
        LEFT JOIN vendor_bill_lines line ON line.vendor_bill_id = bill.vendor_bill_id
        WHERE bill.organization_id = %s AND bill.vendor_id = %s
    """, (organization_id, vendor_id))
    bills, purchases = cursor.fetchone()
    purchases = money_round(purchases or 0)
    return {"purchases": purchases, "purchases_text": format_amount(purchases), "bills": bills or 0}


def customer_totals(cursor, organization_id, customer_id):
    """The figures at the top of the card, and what each one means.

    sales, invoiced, received   every invoice (posted or draft), the POSTED
                                ones, and the money received from him
    applied                     how much of the posted invoices the receipts
                                have been matched to (customer_receipt_allocations)
    due                         what the posted invoices still owe:
                                invoiced - applied. This is the same figure the
                                Customer Aging Report shows for him.
    unapplied                   money received but not yet matched to an invoice
    net                         invoiced - received: what he owes after every
                                rupee received, matched or not. This is what the
                                partner ledger closes at.

    'purchases' and 'bills' come from the vendor this customer is linked to
    (vendor_id); both are zero when he is not linked to one.
    """
    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE status = 'Posted'),
               COUNT(*),
               COALESCE(SUM(total) FILTER (WHERE status = 'Posted'), 0),
               COALESCE(SUM(total), 0)
        FROM (
            SELECT invoice.sales_invoice_id, invoice.status,
                   COALESCE(SUM(line.total), 0) AS total
            FROM sales_invoices invoice
            LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
            WHERE invoice.organization_id = %s AND invoice.customer_id = %s
            GROUP BY invoice.sales_invoice_id, invoice.status
        ) AS written
    """, (organization_id, customer_id))
    posted_count, invoice_count, invoiced, sales = cursor.fetchone()

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM customer_receipts "
                   "WHERE organization_id = %s AND customer_id = %s", (organization_id, customer_id))
    received = cursor.fetchone()[0]

    # How much of those posted invoices the receipts have been matched to. A
    # receipt that matches nothing is still money received - it is counted in
    # "unapplied" below, not lost.
    cursor.execute("""
        SELECT COALESCE(SUM(allocation.allocated_amount), 0)
        FROM customer_receipt_allocations allocation
        JOIN sales_invoices invoice ON invoice.sales_invoice_id = allocation.sales_invoice_id
        WHERE invoice.organization_id = %s AND invoice.customer_id = %s AND invoice.status = 'Posted'
    """, (organization_id, customer_id))
    applied = cursor.fetchone()[0]

    invoiced = money_round(invoiced or 0)
    received = money_round(received or 0)
    applied = money_round(applied or 0)
    totals = {
        "sales": money_round(sales or 0),
        "invoiced": invoiced,
        "received": received,
        "applied": applied,
        "due": money_round(invoiced - applied),
        "unapplied": money_round(received - applied),
        "net": money_round(invoiced - received),
        "invoices": invoice_count or 0,
        "posted_invoices": posted_count or 0,
    }
    # the same figures ready to be written on the card
    for key in ("sales", "invoiced", "received", "applied", "due", "unapplied", "net"):
        totals[key + "_text"] = format_amount(totals[key])

    # What the customer buys FROM US is above; what we buy from him is here,
    # read from the vendor he is linked to.
    cursor.execute("SELECT vendor_id FROM customers WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, organization_id))
    row = cursor.fetchone()
    totals["vendor_id"] = row[0] if row else None
    totals.update(vendor_purchases(cursor, organization_id, totals["vendor_id"]))
    return totals


# ---------------------------------------------------------------------------
# The other tabs of the card: Sales & Purchase, Accounting, Notes, Credit Limit
#
# All of them READ. None of them creates or changes a document, and none of
# them posts anything. The Notes tab writes the one column it owns.
# ---------------------------------------------------------------------------

NOTES_MAX_LENGTH = 2000          # what one Notes tab holds
TRADE_RECEIVABLES_CODE = "1100"  # the ledger a customer is followed on


def clean_notes(notes):
    """The Notes tab: what he wants to remember about the customer.

    The lines he typed are kept as he typed them (a note is not a name - two
    lines mean two lines), only the spaces at the ends of each line go, so a
    note looks the same the next time he opens it.
    """
    text = (notes or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if not text:
        return None
    if len(text) > NOTES_MAX_LENGTH:
        raise ValidationError(f"Notes are too long (maximum {NOTES_MAX_LENGTH} characters).")
    return text


def save_notes(cursor, organization_id, customer_id, notes):
    """Write the Notes tab. Nothing else on the card is touched."""
    cursor.execute(
        "UPDATE customers SET notes = %s WHERE customer_id = %s AND organization_id = %s",
        (clean_notes(notes), customer_id, organization_id),
    )
    return cursor.rowcount > 0


def _invoice_row(invoice_id, invoice_no, invoice_date, due_date, status, total, paid=0):
    total = money_round(total or 0)
    paid = money_round(paid or 0)
    remaining = money_round(total - paid)
    return {
        "invoice_id": invoice_id,
        "invoice_no": invoice_no,
        "status": status,
        "date_text": format_date(invoice_date),
        "due_date": due_date,
        "due_text": format_date(due_date),
        "total": total,
        "total_text": format_amount(total),
        "paid": paid,
        "paid_text": format_amount(paid),
        "remaining": remaining,
        "remaining_text": format_amount(remaining),
    }


def list_customer_invoices(cursor, organization_id, customer_id):
    """Every sales invoice entered for the customer, newest first - drafts included.

    The number shown is the invoice's own printed number (INV/2026/00001),
    given when it was saved (sales/services.py).
    """
    cursor.execute("""
        SELECT invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date, invoice.due_date,
               invoice.status, COALESCE(SUM(line.total), 0), COALESCE(invoice.paid_amount, 0)
        FROM sales_invoices invoice
        LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
        WHERE invoice.organization_id = %s AND invoice.customer_id = %s
        GROUP BY invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date, invoice.due_date,
                 invoice.status, invoice.paid_amount
        ORDER BY invoice.invoice_date DESC, invoice.sales_invoice_id DESC
    """, (organization_id, customer_id))
    return [_invoice_row(*row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------
# Matching a receipt to the invoices it settles
#
# A receipt credits Trade Receivables with the whole amount and says nothing
# about WHICH invoices it settles. Without that, every invoice looks unpaid
# forever and an ageing report is guesswork. These helpers keep the answer:
# customer_receipt_allocations - one row per invoice a receipt knocked off -
# and the paid/remaining figure on the invoice derived from those rows.
#
# The allocation rows are the truth. sales_invoices.paid_amount and
# sales_invoices.amount_due are a cache of them, rewritten by
# refresh_invoice_balance() whenever an allocation changes. Nothing here
# posts a journal entry: the money is already in the ledger.
# ---------------------------------------------------------------------

AGEING_BUCKETS = [
    ("0-30", "0-30 days", 0, 30),
    ("31-60", "31-60 days", 31, 60),
    ("61-90", "61-90 days", 61, 90),
    ("older", "Over 90 days", 91, None),
]


def invoice_total(cursor, sales_invoice_id):
    """What the invoice is worth: the sum of its own lines, tax included."""
    cursor.execute(
        "SELECT COALESCE(SUM(total), 0) FROM sales_invoice_lines WHERE sales_invoice_id = %s",
        (sales_invoice_id,))
    return money_round(cursor.fetchone()[0] or 0)


def invoice_paid(cursor, sales_invoice_id):
    """How much of the invoice the receipts have knocked off."""
    cursor.execute(
        "SELECT COALESCE(SUM(allocated_amount), 0) FROM customer_receipt_allocations "
        "WHERE sales_invoice_id = %s", (sales_invoice_id,))
    return money_round(cursor.fetchone()[0] or 0)


def refresh_invoice_balance(cursor, sales_invoice_id):
    """Rewrite the invoice's paid and still-due figures from its allocations."""
    total = invoice_total(cursor, sales_invoice_id)
    paid = invoice_paid(cursor, sales_invoice_id)
    due = money_round(total - paid)
    cursor.execute(
        "UPDATE sales_invoices SET paid_amount = %s, amount_due = %s WHERE sales_invoice_id = %s",
        (paid, due, sales_invoice_id))
    return {"total": total, "paid": paid, "due": due}


def open_invoices(cursor, organization_id, customer_id):
    """The invoices a receipt may be applied to, oldest first.

    Only posted invoices with something still owed: a draft is not yet in the
    ledger, so money cannot honestly be matched to it. Oldest first, because
    that is the order money is normally applied in.
    """
    cursor.execute("""
        SELECT invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date,
               invoice.due_date, invoice.status,
               COALESCE(SUM(line.total), 0) AS total,
               COALESCE(invoice.paid_amount, 0) AS paid
        FROM sales_invoices invoice
        LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
        WHERE invoice.organization_id = %s AND invoice.customer_id = %s
          AND invoice.status = 'Posted'
        GROUP BY invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date,
                 invoice.due_date, invoice.status, invoice.paid_amount
        ORDER BY COALESCE(invoice.due_date, invoice.invoice_date), invoice.sales_invoice_id
    """, (organization_id, customer_id))
    rows = []
    for invoice_id, invoice_no, invoice_date, due_date, status, total, paid in cursor.fetchall():
        total = money_round(total or 0)
        paid = money_round(paid or 0)
        remaining = money_round(total - paid)
        if remaining <= 0:
            continue
        rows.append({
            "invoice_id": invoice_id,
            "invoice_no": invoice_no,
            "status": status,
            "date_text": format_date(invoice_date),
            "due_date": due_date,
            "due_text": format_date(due_date),
            "total": total,
            "paid": paid,
            "remaining": remaining,
            "total_text": format_amount(total),
            "paid_text": format_amount(paid),
            "remaining_text": format_amount(remaining),
        })
    return rows


def receipt_allocations(cursor, organization_id, customer_receipt_id):
    """The invoices one receipt was applied to."""
    cursor.execute("""
        SELECT allocation.sales_invoice_id, invoice.invoice_no,
               allocation.allocated_amount, invoice.invoice_date
        FROM customer_receipt_allocations allocation
        JOIN sales_invoices invoice ON invoice.sales_invoice_id = allocation.sales_invoice_id
        JOIN customer_receipts receipt ON receipt.customer_receipt_id = allocation.customer_receipt_id
        WHERE allocation.customer_receipt_id = %s AND receipt.organization_id = %s
        ORDER BY invoice.invoice_date, allocation.allocation_id
    """, (customer_receipt_id, organization_id))
    rows = []
    for invoice_id, invoice_no, amount, invoice_date in cursor.fetchall():
        rows.append({
            "invoice_id": invoice_id,
            "invoice_no": invoice_no,
            "amount": money_round(amount or 0),
            "amount_text": format_amount(amount),
            "date_text": format_date(invoice_date),
        })
    return rows


def unmatch_receipt_from_invoice(cursor, organization_id, customer_receipt_id, sales_invoice_id):
    """Take one invoice back off a receipt.

    The allocation is the only record of the match, so removing it is all there
    is to it - what an invoice has been paid is counted from the allocations
    every time it is read (invoice_paid), never stored twice. Returns the invoice
    number and the amount that was taken off, for the message on the page.
    """
    cursor.execute("""
        SELECT allocation.allocation_id, allocation.allocated_amount, invoice.invoice_no
        FROM customer_receipt_allocations allocation
        JOIN sales_invoices invoice ON invoice.sales_invoice_id = allocation.sales_invoice_id
        JOIN customer_receipts receipt ON receipt.customer_receipt_id = allocation.customer_receipt_id
        WHERE allocation.customer_receipt_id = %s AND allocation.sales_invoice_id = %s
          AND receipt.organization_id = %s
    """, (customer_receipt_id, sales_invoice_id, organization_id))
    row = cursor.fetchone()
    if not row:
        raise ValueError("That invoice is not matched to this receipt.")
    allocation_id, amount, invoice_no = row
    cursor.execute("DELETE FROM customer_receipt_allocations WHERE allocation_id = %s", (allocation_id,))
    return invoice_no, money_round(amount or 0)


def apply_receipt_to_invoices(cursor, organization_id, customer_receipt_id, customer_id,
                              receipt_amount, wanted):
    """Match a receipt to the invoices it settles.

    `wanted` is a list of (invoice_id, amount) as typed. Every line is checked
    before anything is written, so a receipt is either applied completely or
    not at all, and the message says which invoice is at fault:

      - the invoice must belong to this company and this customer
      - it must be posted, and not already settled in full
      - an amount must be more than zero and not more than what is still owed
        on that invoice, and not more than what is still unapplied on the
        receipt
    """
    receipt_amount = money_round(receipt_amount or 0)
    cleaned = []
    for invoice_id, raw_amount in wanted:
        if not invoice_id:
            continue
        try:
            amount = money_round(raw_amount or 0)
        except ValueError:
            raise ValueError("One of the amounts applied to an invoice is not a number.")
        if amount <= 0:
            continue
        cleaned.append((int(invoice_id), amount))

    if not cleaned:
        return []

    total_applied = money_round(sum((amount for _, amount in cleaned), Decimal("0")))
    if total_applied > receipt_amount:
        raise ValueError(
            "The receipt is for %s but %s has been applied to invoices. "
            "Lower the amounts so they do not add up to more than the receipt."
            % (format_amount(receipt_amount), format_amount(total_applied)))

    cursor.execute("SELECT customer_id FROM customer_receipts WHERE customer_receipt_id = %s "
                   "AND organization_id = %s", (customer_receipt_id, organization_id))
    row = cursor.fetchone()
    if not row or int(row[0]) != int(customer_id):
        raise ValueError("That receipt does not belong to this customer.")

    applied = []
    for invoice_id, amount in cleaned:
        cursor.execute("""
            SELECT invoice.customer_id, invoice.status, invoice.invoice_no,
                   COALESCE(SUM(line.total), 0)
            FROM sales_invoices invoice
            LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
            WHERE invoice.sales_invoice_id = %s AND invoice.organization_id = %s
            GROUP BY invoice.sales_invoice_id, invoice.customer_id, invoice.status, invoice.invoice_no
        """, (invoice_id, organization_id))
        found = cursor.fetchone()
        if not found:
            raise ValueError("That invoice is not one of this company's invoices.")
        owner_id, status, invoice_no, invoice_total_raw = found
        if int(owner_id) != int(customer_id):
            raise ValueError("Invoice %s belongs to another customer." % (invoice_no or invoice_id))
        if status != "Posted":
            raise ValueError("Invoice %s is still a draft, so money cannot be applied to it yet."
                             % (invoice_no or invoice_id))
        remaining = money_round(money_round(invoice_total_raw or 0) - invoice_paid(cursor, invoice_id))
        if remaining <= 0:
            raise ValueError("Invoice %s is already settled." % (invoice_no or invoice_id))
        if amount > remaining:
            raise ValueError(
                "Invoice %s still owes %s, so %s cannot be applied to it."
                % (invoice_no or invoice_id, format_amount(remaining), format_amount(amount)))

        cursor.execute("""
            INSERT INTO customer_receipt_allocations
                (customer_receipt_id, sales_invoice_id, allocated_amount)
            VALUES (%s, %s, %s)
        """, (customer_receipt_id, invoice_id, amount))
        refresh_invoice_balance(cursor, invoice_id)
        applied.append({"invoice_id": invoice_id, "invoice_no": invoice_no,
                        "amount": amount, "amount_text": format_amount(amount)})

    return applied


def customer_open_invoices(cursor, organization_id, customer_id, as_of=None):
    """The invoices of one customer that still owe something, with their age.

    Used by the customer's own page, by the statement, and by the ageing
    report - one query, so all three always say the same thing.
    """
    as_of = as_of or date.today()
    cursor.execute("""
        SELECT invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date,
               invoice.due_date, invoice.status,
               COALESCE(SUM(line.total), 0) AS total,
               COALESCE(invoice.paid_amount, 0) AS paid
        FROM sales_invoices invoice
        LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
        WHERE invoice.organization_id = %s AND invoice.customer_id = %s AND invoice.status = 'Posted'
        GROUP BY invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date,
                 invoice.due_date, invoice.status, invoice.paid_amount
        ORDER BY COALESCE(invoice.due_date, invoice.invoice_date), invoice.sales_invoice_id
    """, (organization_id, customer_id))

    rows = []
    for invoice_id, invoice_no, invoice_date, due_date, status, total, paid in cursor.fetchall():
        remaining = money_round(money_round(total or 0) - money_round(paid or 0))
        if remaining <= 0:
            continue
        start = due_date or invoice_date
        days = (as_of - start).days if start else 0
        rows.append({
            "invoice_id": invoice_id,
            "invoice_no": invoice_no,
            "status": status,
            "invoice_date": invoice_date,
            "date_text": format_date(invoice_date),
            "due_date": due_date,
            "due_text": format_date(due_date),
            "days": days,
            "bucket": _bucket_for(days),
            "remaining": remaining,
            "remaining_text": format_amount(remaining),
        })
    return rows


def _bucket_for(days):
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "older"


def bucket_totals(rows):
    """0-30 / 31-60 / 61-90 / older out of customer_open_invoices() rows."""
    totals = {key: Decimal("0") for key, _label, _low, _high in AGEING_BUCKETS}
    for row in rows:
        totals[row["bucket"]] += row["remaining"]
    totals = {key: money_round(value) for key, value in totals.items()}
    totals["total"] = money_round(sum(totals.values()))
    totals["text"] = {key: format_amount(value) for key, value in totals.items() if key != "text"}
    return totals


def customer_ageing(cursor, organization_id, as_of=None):
    """What each customer still owes, split 0-30 / 31-60 / 61-90 / older.

    Days are counted from the invoice's due date, or from the invoice date
    when it has none, to `as_of` (today by default). Only posted invoices with
    something still owed are counted, which is what the partner ledger shows
    as money owed by that customer - so the totals tie.
    """
    as_of = as_of or date.today()
    cursor.execute("""
        SELECT customer.customer_id, customer.name,
               invoice.sales_invoice_id, invoice.invoice_no, invoice.invoice_date,
               invoice.due_date,
               COALESCE(SUM(line.total), 0) AS total,
               COALESCE(invoice.paid_amount, 0) AS paid
        FROM customers customer
        JOIN sales_invoices invoice ON invoice.customer_id = customer.customer_id
        LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
        WHERE customer.organization_id = %s AND invoice.status = 'Posted'
        GROUP BY customer.customer_id, customer.name, invoice.sales_invoice_id,
                 invoice.invoice_no, invoice.invoice_date, invoice.due_date, invoice.paid_amount
        ORDER BY customer.name
    """, (organization_id,))

    customers = {}
    for customer_id, name, invoice_id, invoice_no, invoice_date, due_date, total, paid in cursor.fetchall():
        remaining = money_round(money_round(total or 0) - money_round(paid or 0))
        if remaining <= 0:
            continue
        start = due_date or invoice_date
        days = (as_of - start).days if start else 0
        bucket = _bucket_for(days)

        entry = customers.setdefault(customer_id, {
            "customer_id": customer_id,
            "name": name,
            "buckets": {"0-30": Decimal("0"), "31-60": Decimal("0"),
                        "61-90": Decimal("0"), "older": Decimal("0")},
            "total": Decimal("0"),
            "invoices": [],
        })
        entry["buckets"][bucket] += remaining
        entry["total"] += remaining
        entry["invoices"].append({
            "invoice_id": invoice_id,
            "invoice_no": invoice_no,
            "date_text": format_date(invoice_date),
            "due_text": format_date(due_date),
            "days": days,
            "remaining": remaining,
            "remaining_text": format_amount(remaining),
            "bucket": bucket,
        })

    rows = []
    for entry in customers.values():
        entry["total"] = money_round(entry["total"])
        entry["total_text"] = format_amount(entry["total"])
        for key in entry["buckets"]:
            entry["buckets"][key] = money_round(entry["buckets"][key])
        rows.append(entry)
    rows.sort(key=lambda row: row["total"], reverse=True)

    totals = {key: money_round(sum((row["buckets"][key] for row in rows), Decimal("0")))
              for key, _, _, _ in AGEING_BUCKETS}
    totals["total"] = money_round(sum((row["total"] for row in rows), Decimal("0")))
    return rows, totals


def _receipt_row(receipt_id, receipt_date, amount, memo):
    return {
        "receipt_id": receipt_id,
        "date_text": format_date(receipt_date),
        "amount": money_round(amount or 0),
        "amount_text": format_amount(amount),
        "memo": memo,
    }


def list_customer_receipts(cursor, organization_id, customer_id):
    """The money received from the customer, newest first."""
    cursor.execute("""
        SELECT customer_receipt_id, receipt_date, amount, memo
        FROM customer_receipts
        WHERE organization_id = %s AND customer_id = %s
        ORDER BY receipt_date DESC, customer_receipt_id DESC
    """, (organization_id, customer_id))
    return [_receipt_row(*row) for row in cursor.fetchall()]


def customer_ledger(cursor, organization_id, customer):
    """What the books themselves say about the customer.

    The lines on Trade Receivables (account 1100) whose name is his name.
    This is the one place a hand-written journal entry shows up: the three
    figures at the top of the card read documents, not the ledger.
    """
    name = (customer.get("name") or "").strip()
    lines, debit_total, credit_total = [], Decimal("0"), Decimal("0")
    if name:
        cursor.execute("""
            SELECT header.accounting_date, header.journal_name, header.reference, header.status,
                   line.label, COALESCE(line.partner_name, ''), line.debit, line.credit
            FROM journal_lines line
            JOIN journal_headers header ON header.journal_header_id = line.journal_header_id
            JOIN chart_of_accounts account ON account.account_id = line.account_id
            WHERE header.organization_id = %s
              AND account.code = %s
              AND LOWER(TRIM(COALESCE(line.partner_name, ''))) = LOWER(%s)
            ORDER BY header.accounting_date DESC, line.journal_line_id DESC
        """, (organization_id, TRADE_RECEIVABLES_CODE, name))
        for accounting_date, journal_name, reference, status, label, partner, debit, credit in cursor.fetchall():
            debit, credit = money_round(debit or 0), money_round(credit or 0)
            debit_total += debit
            credit_total += credit
            lines.append({
                "date_text": format_date(accounting_date),
                "journal_name": journal_name,
                "reference": reference or label or "",
                "status": status,
                "debit": debit,
                "credit": credit,
                "debit_text": format_amount(debit) if debit else "",
                "credit_text": format_amount(credit) if credit else "",
            })
    balance = debit_total - credit_total
    return {
        "lines": lines,
        "debit_text": format_amount(debit_total),
        "credit_text": format_amount(credit_total),
        "balance": balance,
        "balance_text": format_amount(balance),
        "name": name,
    }


def credit_position(totals, credit_limit, extra=Decimal("0")):
    """The Credit Limit tab, and the warning an invoice needs before it is saved.

    The figure held against the limit is the NET receivable (invoiced less every
    rupee received), because an unapplied receipt is still money in the bank and
    should not eat his room. `extra` is an invoice about to be added.

    `room` is signed (it is negative once he is over); `room_text` is the same
    figure written out, and `over_by`/`over_by_text` is how far over he is, which
    is what a warning should say. A limit of 0.00 means no limit at all.
    """
    limit = money_round(credit_limit or 0)
    # Older totals (and the tests) hand in only "due"; the net is used when it is there.
    held = totals.get("net")
    if held is None:
        held = totals.get("due") or 0
    used = money_round(held) + money_round(extra or 0)
    room = money_round(limit - used)
    has_limit = limit > 0
    return {
        "limit": limit,
        "limit_text": format_amount(limit),
        "used": used,
        "used_text": format_amount(used),
        "due_text": totals.get("due_text") or format_amount(totals.get("due") or 0),
        "room": room,
        "room_text": "\u2014" if not has_limit else format_amount(room),
        "has_limit": has_limit,
        "over": bool(has_limit and used > limit),
        "over_by": money_round(used - limit),
        "over_by_text": format_amount(abs(used - limit)),
    }


def credit_status(cursor, organization_id, customer_id, extra=Decimal("0")):
    """The same, read straight from the database (used by the invoice screens)."""
    totals = customer_totals(cursor, organization_id, customer_id)
    cursor.execute("SELECT credit_limit FROM customers WHERE customer_id = %s AND organization_id = %s",
                   (customer_id, organization_id))
    row = cursor.fetchone()
    return credit_position(totals, row[0] if row else 0, extra)


# -------------------------------------------------------------------------
# The number on a customer receipt (his "Voucher Number")
#
# His own receipt screen shows a voucher number in the same shape as the
# invoice numbers: letters, the year, and a running number that starts again
# at 1 every year inside each company:
#
#     CPV / 2026 / 00001
#      |     |       |
#      |     |       +-- the running number in that company, that year
#      |     +---------- the year of the receipt date
#      +---------------- the letters a receipt voucher starts with
#
# Like an invoice number, it is given when the receipt is saved, never changes
# afterwards, and is never used twice (a unique index holds that promise); the
# receipts made before numbering existed get their numbers once, oldest first.
# -------------------------------------------------------------------------

RECEIPT_PREFIX = "CPV"
RECEIPT_DIGITS = 5

PAYMENT_TYPE_OPTIONS = ["Customer Payment", "Advance Payment", "Refund", "Other"]
PAYMENT_METHOD_OPTIONS = ["Cash", "Cheque", "Bank Transfer", "Online Transfer", "Other"]


def receipt_voucher_number(year, number):
    """(2026, 42) -> 'CPV/2026/00042'."""
    return f"{RECEIPT_PREFIX}/{year}/{int(number):0{RECEIPT_DIGITS}d}"


def _last_receipt_number(cursor, organization_id, year):
    cursor.execute("""
        SELECT COALESCE(MAX(CAST(SPLIT_PART(voucher_no, '/', 3) AS INTEGER)), 0)
        FROM customer_receipts
        WHERE organization_id = %s AND voucher_no LIKE %s
    """, (organization_id, f"{RECEIPT_PREFIX}/{year}/%"))
    return cursor.fetchone()[0] or 0


def next_receipt_voucher_number(cursor, organization_id, receipt_date):
    if isinstance(receipt_date, str):
        receipt_date = date.fromisoformat(receipt_date)
    year = receipt_date.year
    return receipt_voucher_number(year, _last_receipt_number(cursor, organization_id, year) + 1)


def give_receipt_a_voucher_number(cursor, organization_id, customer_receipt_id, receipt_date):
    """Write the receipt's voucher number. Returns it.

    Two receipts saved in the same second can work out the same number; the
    unique index refuses the second one and this simply takes the number after
    it. The failed try is rolled back to its own savepoint, not the receipt.
    """
    import psycopg2
    for _attempt in range(5):
        number = next_receipt_voucher_number(cursor, organization_id, receipt_date)
        cursor.execute("SAVEPOINT receipt_number")
        try:
            cursor.execute(
                "UPDATE customer_receipts SET voucher_no = %s WHERE customer_receipt_id = %s",
                (number, customer_receipt_id))
            cursor.execute("RELEASE SAVEPOINT receipt_number")
            return number
        except psycopg2.errors.UniqueViolation:
            cursor.execute("ROLLBACK TO SAVEPOINT receipt_number")
    raise RuntimeError("Could not give the receipt a voucher number - please try again.")


def number_missing_receipts(cursor):
    """Give a voucher number to every receipt that has none, oldest first."""
    cursor.execute("""
        SELECT customer_receipt_id, organization_id, receipt_date
        FROM customer_receipts
        WHERE voucher_no IS NULL
        ORDER BY organization_id, receipt_date, customer_receipt_id
    """)
    numbered = 0
    for customer_receipt_id, organization_id, receipt_date in cursor.fetchall():
        give_receipt_a_voucher_number(cursor, organization_id, customer_receipt_id, receipt_date)
        numbered += 1
    return numbered


def clean_date(text):
    """A date box as the browser sends it. Empty means none; nonsense is refused."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Please give a real date.")


# -------------------------------------------------------------------------
# The Customer Partner Ledger (Reports > Customer Partner Ledger)
#
# Same shape as the Vendor Partner Ledger: the documents, oldest first, with a
# running balance. Invoices add to what he owes; receipts take it away. Only
# posted invoices count - a draft is not a debt yet - and every receipt counts,
# because the money did come in.
# -------------------------------------------------------------------------
def partner_ledger(cursor, organization_id, customer_id, start_date=None, end_date=None):
    """Every posted invoice (debit) and receipt (credit) for one customer.

    Returns None when the customer is not this company's, otherwise a dict with
    the rows already written as text, the opening figure, and the closing one.
    """
    cursor.execute(
        "SELECT name FROM customers WHERE customer_id = %s AND organization_id = %s",
        (customer_id, organization_id))
    row = cursor.fetchone()
    if not row:
        return None
    name = row[0]

    invoices = """
        SELECT invoice.invoice_date AS tx_date, COALESCE(invoice.invoice_no, '') AS ref,
               'Invoice' AS kind, COALESCE(SUM(line.total), 0) AS debit, 0 AS credit
        FROM sales_invoices invoice
        LEFT JOIN sales_invoice_lines line ON line.sales_invoice_id = invoice.sales_invoice_id
        WHERE invoice.organization_id = %s AND invoice.customer_id = %s AND invoice.status = 'Posted'
        GROUP BY invoice.sales_invoice_id, invoice.invoice_date, invoice.invoice_no
    """
    receipts = """
        SELECT receipt.receipt_date AS tx_date, COALESCE(receipt.voucher_no, '') AS ref,
               'Receipt' AS kind, 0 AS debit, receipt.amount AS credit
        FROM customer_receipts receipt
        WHERE receipt.organization_id = %s AND receipt.customer_id = %s
    """
    params = [organization_id, customer_id, organization_id, customer_id]
    # `when` is a reserved word in Postgres, so the column is called tx_date.
    everything = f"SELECT * FROM ({invoices} UNION ALL {receipts}) t ORDER BY tx_date ASC, ref ASC"
    cursor.execute(everything, params)

    opening = Decimal("0")
    rows, debit_total, credit_total = [], Decimal("0"), Decimal("0")
    running = Decimal("0")
    for when, ref, kind, debit, credit in cursor.fetchall():
        debit, credit = money_round(debit or 0), money_round(credit or 0)
        if start_date and when < start_date:
            opening += debit - credit
            continue
        if end_date and when > end_date:
            continue
        running += debit - credit
        if debit:
            debit_total += debit
        if credit:
            credit_total += credit
        rows.append({
            "date": when,
            "date_text": format_date(when),
            "reference": ref,
            "kind": kind,
            "debit": debit,
            "credit": credit,
            "debit_text": format_amount(debit) if debit else "",
            "credit_text": format_amount(credit) if credit else "",
            "balance_text": format_amount(opening + running),
        })

    closing = opening + running
    return {
        "name": name,
        "rows": rows,
        "opening": opening,
        "opening_text": format_amount(opening),
        "debit_total": debit_total,
        "debit_total_text": format_amount(debit_total),
        "credit_total": credit_total,
        "credit_total_text": format_amount(credit_total),
        "closing": closing,
        "closing_text": format_amount(closing),
    }


# -------------------------------------------------------------------------
# The statement of account that goes to the customer
#
# The partner ledger in the Reports menu is ZYRO's own view: every document, in
# date order, with a running balance. A statement is the same facts written for
# the customer to read: the company's head on top, the period, what he owed at
# the start, every invoice and receipt in between, what he owes at the end, and
# how old that balance is. Nothing is posted or changed by printing it.
# -------------------------------------------------------------------------

def account_statement(cursor, organization_id, customer_id, start_date=None, end_date=None):
    """Everything the printed statement needs, or None for a stranger's customer."""
    customer = get_customer(cursor, organization_id, customer_id)
    if customer is None:
        return None

    ledger = partner_ledger(cursor, organization_id, customer_id, start_date, end_date)
    # "As at" is the end of the period asked for, or - with no period asked for -
    # the later of today and the last thing in the ledger, so that a future-dated
    # invoice is not left out of a statement that says it includes everything.
    as_of = end_date or date.today()
    if end_date is None:
        for row in ledger["rows"]:
            if row["date"] and row["date"] > as_of:
                as_of = row["date"]
    open_rows = customer_open_invoices(cursor, organization_id, customer_id, as_of=as_of)
    buckets = bucket_totals(open_rows)

    return {
        "customer": customer,
        "rows": ledger["rows"],
        "opening": ledger["opening"],
        "opening_text": ledger["opening_text"],
        "debit_total": ledger["debit_total"],
        "debit_total_text": ledger["debit_total_text"],
        "credit_total": ledger["credit_total"],
        "credit_total_text": ledger["credit_total_text"],
        "closing": ledger["closing"],
        "closing_text": ledger["closing_text"],
        "open_invoices": open_rows,
        "buckets": buckets,
        "start_date": start_date,
        "start_text": format_date(start_date) if start_date else "",
        "end_date": end_date,
        "end_text": format_date(end_date) if end_date else "",
        "as_of": as_of,
        "as_of_text": format_date(as_of),
    }


# -------------------------------------------------------------------------
# The words on a voucher ("Rupees Three Thousand Only")
# -------------------------------------------------------------------------
_WORDS_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
               "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
               "Eighteen", "Nineteen"]
_WORDS_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _words_below_thousand(number):
    """1..999 as words; 0 gives an empty string."""
    words = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(f"{_WORDS_ONES[hundreds]} Hundred")
    if rest:
        if rest < 20:
            words.append(_WORDS_ONES[rest])
        else:
            tens, ones = divmod(rest, 10)
            words.append(_WORDS_TENS[tens] + (f"-{_WORDS_ONES[ones]}" if ones else ""))
    return " ".join(words)


def amount_in_words(value, currency="Rupees", subunit="Paisa"):
    """3500.50 -> 'Rupees Three Thousand Five Hundred and Paisa Fifty Only'.

    Used on the receipt voucher, where the amount is written in words as well
    as figures. Grouping is thousands / millions / billions, in English.
    """
    amount = money_round(value or 0)
    negative = amount < 0
    amount = abs(amount)
    whole = int(amount)
    cents = int((amount - whole) * 100 + Decimal("0.5"))

    if whole == 0:
        words = "Zero"
    else:
        groups = []
        for unit in ("", "Thousand", "Million", "Billion"):
            whole, chunk = divmod(whole, 1000)
            if chunk:
                groups.insert(0, (_words_below_thousand(chunk) + (" " + unit if unit else "")).strip())
            if not whole:
                break
        words = " ".join(groups)

    text = f"{currency} {words}"
    if cents:
        text += f" and {subunit} {_words_below_thousand(cents)}"
    if negative:
        text = "Minus " + text
    return text + " Only"
