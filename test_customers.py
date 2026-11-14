"""
test_customers.py

Checks for the rules of the customer card (customers/services.py) that need
no database: what a filled-in customer form must look like before it may be
saved, how values are written on the list, and how the search text is used.

HOW TO RUN
    python3 test_customers.py

Whether the screens really save, number, lock and separate customers is
checked against a real database in test_workflows.py (section 11).
"""
from datetime import date
from decimal import Decimal as D

from customers.services import (
    CUSTOMER_COLUMNS, FORM_FIELDS, LIST_COLUMNS, NAME_MAX_LENGTH, NATURE_OF_SUPPLY_OPTIONS, TEXT_FIELDS,
    blank_form, clean_block, clean_customer_form, clean_line, clean_search, form_as_typed,
    form_from_customer, format_amount, format_date, like_pattern, list_row, same_name,
)


def problems_of(**typed):
    form = {"name": "Acme Traders"}
    form.update(typed)
    return clean_customer_form(form)[1]


def refused(message_part, **typed):
    """Exactly one problem, and it mentions `message_part`."""
    problems = problems_of(**typed)
    assert len(problems) == 1 and message_part.lower() in problems[0].lower(), (typed, problems)


def accepted(**typed):
    form = {"name": "Acme Traders"}
    form.update(typed)
    data, problems = clean_customer_form(form)
    assert problems == [], (typed, problems)
    return data


def test_typed_text_is_tidied():
    assert clean_line("  Ali   Traders  ") == "Ali Traders"                 # outer spaces off, inner runs made one
    assert clean_line("Ali\tTraders\r\nKasur") == "Ali Traders Kasur"       # a tab or a line break is a space
    assert clean_line("Ali\x00 Tra\x07ders") == "Ali Traders"               # characters no keyboard types are dropped
    assert clean_line(None) == "" and clean_line(123) == "" and clean_line("   ") == ""
    assert clean_line("عبدالمجید  ٹریڈرز") == "عبدالمجید ٹریڈرز"            # any script is kept as typed
    # the Street box may have several lines: each is tidied, empty ones go
    assert clean_block("  14-B   Industrial Estate \r\n\r\n   Kot Lakhpat  \n") == "14-B Industrial Estate\nKot Lakhpat"
    assert clean_block(None) == "" and clean_block(" \n \n") == ""
    # the same name, apart from stray spaces - capital letters DO make a different name
    assert same_name("Ali  Traders ", "Ali Traders")
    assert not same_name("ali traders", "Ali Traders")
    assert not same_name("Ali Traders", "Ali Trader")
    print("✓ Typed text is tidied (spaces, line breaks, untypeable characters) passed")


def test_the_smallest_valid_form():
    data, problems = clean_customer_form({"name": "Acme Traders"})
    assert problems == []
    assert data["name"] == "Acme Traders"
    assert data["is_company"] is True                           # Company unless Person is chosen
    assert data["nature_of_supply"] == "Supplies"
    assert data["credit_limit"] == D("0.00")
    assert data["lic_expiry_date"] is None
    for field, _label, _max_length in TEXT_FIELDS:              # every empty box is saved as "nothing", not as ""
        assert data[field] is None, field
    assert "is_active" not in data                              # the Status box belongs to the Edit form only
    # exactly the columns the save writes - nothing missing, nothing extra
    assert set(data) == {"name", "is_company", "nature_of_supply", "credit_limit", "lic_expiry_date"} | {
        field for field, _label, _max_length in TEXT_FIELDS}
    print("✓ The smallest valid form (a name and nothing else) passed")


def test_name():
    refused("Customer name is required", name="")
    refused("Customer name is required", name="     ")          # spaces alone are not a name
    refused("Customer name is required", name="\x00\x00")
    assert clean_customer_form({})[1] == ["Customer name is required."]     # the box not sent at all
    assert accepted(name="A" * NAME_MAX_LENGTH)["name"] == "A" * NAME_MAX_LENGTH
    refused("too long (maximum 150", name="A" * (NAME_MAX_LENGTH + 1))
    assert accepted(name="  Ali   Traders ")["name"] == "Ali Traders"
    assert NAME_MAX_LENGTH == 150                               # = customers.name and journal_lines.partner_name
    print("✓ Customer name (required, not just spaces, at most 150 characters) passed")


def test_person_or_company_and_nature_of_supply():
    assert accepted(partner_type="person")["is_company"] is False
    assert accepted(partner_type="company")["is_company"] is True
    refused("Person or Company", partner_type="robot")
    assert accepted(nature_of_supply="Services")["nature_of_supply"] == "Services"
    refused("Supplies or Services", nature_of_supply="Goods")
    refused("Supplies or Services", nature_of_supply="supplies")           # exactly as offered
    assert NATURE_OF_SUPPLY_OPTIONS == ["Supplies", "Services"]             # these decide the WHT Receivable ledger
    print("✓ Person / Company and Nature of supply passed")


def test_every_text_box_has_a_limit():
    for field, label, max_length in TEXT_FIELDS:
        filler = "7" if field in ("phone", "ntn", "strn", "cnic", "zip_code") else "x"
        longest = filler * max_length
        if field == "email":
            longest = "a@b." + "c" * (max_length - 4)
        assert accepted(**{field: longest})[field] == longest, field                    # exactly at the limit: saved
        too_long = longest + filler
        problems = problems_of(**{field: too_long})
        assert any(f"{label} is too long (maximum {max_length} characters)" in problem for problem in problems), (field, problems)
        assert accepted(**{field: "  "})[field] is None, field                          # blank -> nothing
    assert accepted(territory="  KASUR   TOWN ")["territory"] == "KASUR TOWN"
    assert accepted(address=" Canal Road \n\n Block B ")["address"] == "Canal Road\nBlock B"
    print(f"✓ Each of the {len(TEXT_FIELDS)} text boxes keeps to the length its column can hold passed")


def test_email_and_phone():
    for good in ("a@b.co", "accounts@crescent-dye.example", "first.last+tag@sub.domain.pk"):
        assert accepted(email=good)["email"] == good
    for bad in ("plainaddress", "a@b", "@b.co", "a@.", "a b@c.com", "a@b@c.com"):
        refused("does not look like an email", email=bad)
    for good in ("0300-7520665", "+92 42 3590000", "042-111-000-111 ext 12"):
        assert accepted(phone=good)["phone"] == good
    refused("Phone must contain a number", phone="call me")
    print("✓ Email and phone passed")


def test_licence_expiry_date():
    assert accepted(lic_expiry_date="2027-03-05")["lic_expiry_date"] == date(2027, 3, 5)
    assert accepted(lic_expiry_date="")["lic_expiry_date"] is None
    assert accepted(lic_expiry_date="2028-02-29")["lic_expiry_date"] == date(2028, 2, 29)     # a leap day
    for bad in ("2027-02-29", "2028-13-01", "05/03/2027", "tomorrow", "2027-03-05; DROP TABLE customers"):
        refused("not a valid date", lic_expiry_date=bad)
    for bad in ("0001-01-01", "1899-12-31", "2201-01-01", "9999-12-31"):
        refused("between the years 1900 and 2200", lic_expiry_date=bad)
    print("✓ LIC Expiry Date (a real date, between 1900 and 2200, or empty) passed")


def test_credit_limit():
    assert accepted(credit_limit="")["credit_limit"] == D("0.00")                # empty = no limit set
    assert accepted(credit_limit="0")["credit_limit"] == D("0.00")
    assert accepted(credit_limit="2500000")["credit_limit"] == D("2500000.00")
    assert accepted(credit_limit=" 40000000.00 ")["credit_limit"] == D("40000000.00")
    assert accepted(credit_limit="100.005")["credit_limit"] == D("100.01")       # rounded half up, like every amount
    assert accepted(credit_limit="100.004")["credit_limit"] == D("100.00")
    assert str(accepted(credit_limit="-0")["credit_limit"]) == "0.00"            # never "-0.00"
    assert accepted(credit_limit="999999999999.99")["credit_limit"] == D("999999999999.99")    # the most the column holds
    for too_large in ("1000000000000", "999999999999.995", "1e15", "1e999999"):  # the last one cannot even be rounded
        refused("too large", credit_limit=too_large)
    refused("cannot be negative", credit_limit="-1")
    for not_a_number in ("abc", "NaN", "Infinity", "-Infinity", "1,000", "0x10", "12 34"):
        refused("must be a number", credit_limit=not_a_number)
    for value in ("0", "2500000", "100.005", "999999999999.99"):                # always Decimal, never float
        assert isinstance(accepted(credit_limit=value)["credit_limit"], D)
    print("✓ Credit limit (Decimal, 0 to 999,999,999,999.99, wrong input refused) passed")


def test_status_on_the_edit_form():
    edit = lambda **typed: clean_customer_form(dict({"name": "Acme Traders"}, **typed), editing=True)
    assert edit(status="active") == (dict(edit(status="active")[0], is_active=True), [])
    assert edit(status="active")[0]["is_active"] is True
    assert edit(status="inactive")[0]["is_active"] is False
    assert edit()[0]["is_active"] is None and edit()[1] == []          # not sent: the customer stays as it is
    data, problems = edit(status="deleted")
    assert data["is_active"] is None and problems == ["Status must be Active or Inactive."]
    print("✓ Status on the Edit form passed")


def test_all_mistakes_are_reported_together():
    data, problems = clean_customer_form({
        "name": "  ", "email": "nobody", "lic_expiry_date": "31-12-2026", "credit_limit": "-5", "ntn": "9" * 31})
    assert len(problems) == 5, problems
    assert [problem.split(" ")[0] for problem in problems] == ["Customer", "NTN", "Email", "LIC", "Credit"], problems
    print("✓ Several mistakes are reported together, not one at a time passed")


def test_what_the_form_shows():
    blank = blank_form()
    assert set(blank) == set(FORM_FIELDS)
    assert (blank["partner_type"], blank["nature_of_supply"], blank["credit_limit"], blank["status"]) == (
        "company", "Supplies", "0.00", "active")
    assert all(blank[field] == "" for field in blank if field not in ("partner_type", "nature_of_supply", "credit_limit", "status"))

    customer = dict.fromkeys(CUSTOMER_COLUMNS)
    customer.update(customer_id=7, customer_no=3, name="Ravi Tanneries", is_company=False, nature_of_supply="Services",
                    credit_limit=D("1000000"), lic_expiry_date=date(2026, 12, 31), territory="KASUR", is_active=False)
    shown = form_from_customer(customer)
    assert set(shown) == set(FORM_FIELDS)
    assert (shown["name"], shown["partner_type"], shown["nature_of_supply"], shown["credit_limit"],
            shown["lic_expiry_date"], shown["territory"], shown["status"]) == (
        "Ravi Tanneries", "person", "Services", "1000000.00", "2026-12-31", "KASUR", "inactive")
    assert shown["email"] == "" and shown["address"] == ""          # a box with nothing saved shows empty, not "None"
    # a customer saved before the Status existed (is_active empty) is Active
    assert form_from_customer(dict(customer, is_active=None))["status"] == "active"

    # a refused form comes back with every box as the user left it - also the wrong ones
    typed = form_as_typed({"name": "  New  Name ", "credit_limit": "abc", "lic_expiry_date": "31-12-2026", "territory": "KSR"}, base=shown)
    assert (typed["name"], typed["credit_limit"], typed["lic_expiry_date"], typed["territory"]) == (
        "  New  Name ", "abc", "31-12-2026", "KSR")
    assert typed["partner_type"] == "person" and typed["status"] == "inactive"      # boxes not sent keep the saved value
    assert form_as_typed({"name": "X"})["nature_of_supply"] == "Supplies"            # ... or the blank form's
    assert form_as_typed({"name": "A\x00B"})["name"] == "AB"
    print("✓ What the form shows (blank, filled from a customer, as typed after a refusal) passed")


def test_how_the_list_writes_values():
    assert format_amount(D("1234567.5")) == "1,234,567.50"
    assert format_amount(D("40000000")) == "40,000,000.00"
    assert format_amount(0) == "0.00" and format_amount(None) == "0.00"
    assert format_amount(D("999999999999.99")) == "999,999,999,999.99"
    assert format_date(date(2027, 3, 5)) == "05 Mar 2027"
    assert format_date(date(2026, 12, 31)) == "31 Dec 2026"
    assert format_date(None) == ""

    customer = dict.fromkeys(CUSTOMER_COLUMNS)
    customer.update(customer_id=7, customer_no=10450, name="Sample Traders", credit_limit=D("40000000"),
                    lic_expiry_date=date(2027, 3, 5), address="Canal Road\nBlock B", is_active=True)
    row = list_row(customer)
    assert row["customer_id"] == 7 and row["is_active"] is True
    assert row["cells"]["customer_no"] == "10450"                   # an ID is written plainly, without a comma
    assert row["cells"]["credit_limit"] == "40,000,000.00"
    assert row["cells"]["lic_expiry_date"] == "05 Mar 2027"
    assert row["cells"]["address"] == "Canal Road Block B"          # one line on the list
    assert row["cells"]["email"] == ""                              # empty stays empty - never the word "None"
    assert set(row["cells"]) == {column["key"] for column in LIST_COLUMNS}
    assert list_row(dict(customer, is_active=False))["is_active"] is False
    print("✓ How the list writes amounts, dates and empty boxes passed")


def test_list_columns():
    keys = [column["key"] for column in LIST_COLUMNS]
    assert len(keys) == len(set(keys))                              # no column twice
    assert set(keys) <= set(CUSTOMER_COLUMNS)                       # every column is a real field of a customer
    assert keys[:2] == ["customer_no", "name"]                      # ID first, then the name
    assert [column["key"] for column in LIST_COLUMNS if column.get("fixed")] == ["name"]   # only the name has no tick-box
    assert all(column["kind"] in ("text", "number", "amount", "date", "name") for column in LIST_COLUMNS)
    # shown before anything is ticked: the columns of the owner's own customer list
    assert [column["label"] for column in LIST_COLUMNS if column["shown"]] == [
        "ID", "Name", "Credit Limit", "Email", "Phone", "Region", "Territory", "LIC Number", "LIC Expiry Date",
        "Category Parent", "Country"]
    assert [column["label"] for column in LIST_COLUMNS if not column["shown"]] == [
        "NTN", "Street", "City", "State", "STRN", "CNIC", "Nature of Supply", "Category"]
    print(f"✓ The {len(LIST_COLUMNS)} list columns ({sum(1 for c in LIST_COLUMNS if c['shown'])} shown at first) passed")


def test_search_text():
    assert clean_search("  kasur   traders ") == "kasur traders"
    assert clean_search(None) == "" and clean_search("\x00") == ""
    assert len(clean_search("x" * 500)) == 100
    # what the user types means itself: % and _ are not "anything"
    assert like_pattern("kasur") == "%kasur%"
    assert like_pattern("50%") == "%50\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("back\\slash") == "%back\\\\slash%"
    print("✓ Search text (trimmed, cut to 100 characters, % and _ taken literally) passed")


if __name__ == "__main__":
    test_typed_text_is_tidied()
    test_the_smallest_valid_form()
    test_name()
    test_person_or_company_and_nature_of_supply()
    test_every_text_box_has_a_limit()
    test_email_and_phone()
    test_licence_expiry_date()
    test_credit_limit()
    test_status_on_the_edit_form()
    test_all_mistakes_are_reported_together()
    test_what_the_form_shows()
    test_how_the_list_writes_values()
    test_list_columns()
    test_search_text()
    print("ALL CUSTOMER CARD TESTS PASSED SUCCESSFULLY.")
