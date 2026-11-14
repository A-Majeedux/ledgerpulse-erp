"""
core/validation.py

Checks on what a user typed into a form, done BEFORE anything is written to
the database. Every voucher form should use these instead of converting
request.form values by hand, so that:

  - a wrong value comes back as a clear message on the form, never as a
    crash page ("Internal Server Error")
  - the same rule ("an amount must be greater than zero") reads the same
    way on every screen

HOW TO USE THIS IN A ROUTE:
    from core.validation import ValidationError, parse_amount, parse_date

    try:
        amount = parse_amount(request.form.get("amount"), "Amount")
        paid_on = parse_date(request.form.get("payment_date"), "Date")
    except ValidationError as error:
        return render_template("my_form.html", error=str(error))

The browser already stops most of these mistakes (required fields, number
boxes), but the browser is only a convenience - a stale tab, a script, or
another program talking to the server skips those checks. These functions
are the check that actually protects the books.
"""
from datetime import date
from decimal import Decimal

from core.money import to_decimal, money_round


class ValidationError(Exception):
    """
    Something the user entered is not acceptable. The message is written for
    the person at the keyboard and is safe to show on the form as-is.
    """


def _to_number(raw, label):
    """Text from a form -> a real, finite Decimal, or a ValidationError."""
    text = raw.strip() if isinstance(raw, str) else raw
    if text is None or text == "":
        raise ValidationError(f"{label} is required.")
    try:
        value = to_decimal(text)
    except ValueError:
        raise ValidationError(f"{label} must be a number.")
    # Decimal accepts the words "NaN" and "Infinity". Neither may ever
    # reach the ledger: one NaN would poison every total it is added to.
    if not value.is_finite():
        raise ValidationError(f"{label} must be a number.")
    return value


def parse_amount(raw, label, allow_zero=False):
    """
    A money amount: a number greater than zero (or zero too, when
    allow_zero=True), rounded to 2 decimal places the same way every
    journal entry is. Negative amounts are never accepted - a reversal is
    its own document, not a negative one.
    """
    value = money_round(_to_number(raw, label))
    if value < 0:
        raise ValidationError(f"{label} cannot be negative.")
    if value == 0 and not allow_zero:
        raise ValidationError(f"{label} must be greater than zero.")
    return value


def parse_percent(raw, label):
    """A percentage between 0 and 100 inclusive (tax rates, discounts)."""
    value = _to_number(raw, label)
    if value < 0 or value > Decimal("100"):
        raise ValidationError(f"{label} must be between 0 and 100.")
    return value


def parse_date(raw, label):
    """A date typed into an <input type="date"> (YYYY-MM-DD). Required."""
    text = (raw or "").strip()
    if not text:
        raise ValidationError(f"{label} is required.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError(f"{label} is not a valid date.")


def require_text(raw, label, max_length=None):
    """Non-blank text (spaces alone do not count), trimmed."""
    text = (raw or "").strip()
    if not text:
        raise ValidationError(f"{label} is required.")
    if max_length is not None and len(text) > max_length:
        raise ValidationError(f"{label} is too long (maximum {max_length} characters).")
    return text
