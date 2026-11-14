"""
tests/test_customer_card.py

Checks for the customer card (customers/services.py).

Two kinds of check, in the same file, kept apart by class:

  CustomerCardRulesTests   needs NO database. What a filled-in card must look
                           like before it may be saved, how values are written
                           on the list, and how the search text is used. Safe to
                           run at any time.

  CustomerCardDatabaseTests  saves, numbers, locks, lists and searches real
                           customers, to prove the rules hold on the database
                           too. It uses throwaway organizations named "TEST_",
                           the same way tests/test_vendors.py does, and needs
                           DATABASE_URL set to a TEST database:

    python -m pytest tests/test_customer_card.py -q
"""
import os
import unittest
import uuid
from datetime import date
from decimal import Decimal as D

from customers.services import (
    CONTACT_FORM_FIELDS, CONTACT_TEXT_FIELDS, CUSTOMER_COLUMNS, FORM_FIELDS, LIST_COLUMNS,
    MAX_CREDIT_LIMIT, NAME_LOCKED_MESSAGE, NAME_MAX_LENGTH, NATURE_OF_SUPPLY_OPTIONS,
    TAGS_MAX_COUNT, TAG_MAX_LENGTH, TEXT_FIELDS, TRADE_RECEIVABLE_CODE, ValidationError,
    NOTES_MAX_LENGTH, blank_contact_form, blank_form, clean_block, clean_contact_form,
    clean_currency, clean_customer_form, clean_line, clean_notes, clean_search, clean_tags,
    clean_website, contact_form_as_typed, contact_form_from_contact, credit_position,
    form_as_typed, form_from_customer, format_amount, format_date, like_pattern, list_row,
    parse_amount, same_name, tag_list,
)


# ---------------------------------------------------------------------------
# The rules (no database)
# ---------------------------------------------------------------------------
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


class CustomerCardRulesTests(unittest.TestCase):

    def test_typed_text_is_tidied(self):
        self.assertEqual(clean_line("  Ali   Traders  "), "Ali Traders")
        self.assertEqual(clean_line("Ali\tTraders\r\nKasur"), "Ali Traders Kasur")
        self.assertEqual(clean_line("Ali\x00 Tra\x07ders"), "Ali Traders")     # untypeable characters dropped
        self.assertEqual((clean_line(None), clean_line(123), clean_line("   ")), ("", "", ""))
        self.assertEqual(clean_line("عبدالمجید  ٹریڈرز"), "عبدالمجید ٹریڈرز")   # any script kept as typed
        self.assertEqual(clean_block("  14-B   Industrial Estate \r\n\r\n   Kot Lakhpat  \n"),
                         "14-B Industrial Estate\nKot Lakhpat")
        self.assertEqual((clean_block(None), clean_block(" \n \n")), ("", ""))
        self.assertTrue(same_name("Ali  Traders ", "Ali Traders"))
        self.assertFalse(same_name("ali traders", "Ali Traders"))             # capital letters DO count
        self.assertFalse(same_name("Ali Traders", "Ali Trader"))

    def test_the_smallest_valid_card(self):
        data, problems = clean_customer_form({"name": "Acme Traders"})
        self.assertEqual(problems, [])
        self.assertEqual(data["name"], "Acme Traders")
        self.assertIs(data["is_company"], True)                              # Company unless Person is chosen
        self.assertEqual(data["nature_of_supply"], "Supplies")
        self.assertEqual(data["credit_limit"], D("0.00"))
        self.assertIsNone(data["lic_expiry_date"])
        for field, _label, _max_length in TEXT_FIELDS:                       # an empty box is "nothing", not ""
            self.assertIsNone(data[field], field)
        self.assertNotIn("is_active", data)                                  # Status is for the Edit card only
        self.assertEqual(data["tags"], None)                                # no tags typed
        self.assertIs(data["is_vendor"], False)                             # not ticked
        self.assertIsNone(data["currency"])                                 # = the company's currency
        self.assertIsNone(data["website"])
        self.assertEqual(set(data), {"name", "is_company", "nature_of_supply", "credit_limit", "lic_expiry_date",
                                     "tags", "is_vendor", "currency", "website"}
                         | {field for field, _label, _max_length in TEXT_FIELDS})

    def test_name(self):
        refused("Customer name is required", name="")
        refused("Customer name is required", name="     ")                   # spaces alone are not a name
        refused("Customer name is required", name="\x00\x00")
        self.assertEqual(clean_customer_form({})[1], ["Customer name is required."])   # box not sent at all
        self.assertEqual(accepted(name="A" * NAME_MAX_LENGTH)["name"], "A" * NAME_MAX_LENGTH)
        refused("too long (maximum 150", name="A" * (NAME_MAX_LENGTH + 1))
        self.assertEqual(accepted(name="  Ali   Traders ")["name"], "Ali Traders")
        self.assertEqual(NAME_MAX_LENGTH, 150)                               # = customers.name

    def test_person_or_company_and_nature_of_supply(self):
        self.assertIs(accepted(partner_type="person")["is_company"], False)
        self.assertIs(accepted(partner_type="company")["is_company"], True)
        refused("Person or Company", partner_type="robot")
        self.assertEqual(accepted(nature_of_supply="Services")["nature_of_supply"], "Services")
        refused("Supplies or Services", nature_of_supply="Goods")
        refused("Supplies or Services", nature_of_supply="supplies")          # exactly as offered
        self.assertEqual(NATURE_OF_SUPPLY_OPTIONS, ["Supplies", "Services"])  # they decide the WHT Receivable ledger

    def test_every_text_box_keeps_to_the_length_of_its_column(self):
        for field, label, max_length in TEXT_FIELDS:
            filler = "7" if field in ("phone", "ntn", "strn", "cnic", "zip_code") else "x"
            longest = filler * max_length
            if field == "email":
                longest = "a@b." + "c" * (max_length - 4)
            self.assertEqual(accepted(**{field: longest})[field], longest, field)
            problems = problems_of(**{field: longest + filler})
            self.assertTrue(any(f"{label} is too long (maximum {max_length} characters)" in problem
                                for problem in problems), (field, problems))
            self.assertIsNone(accepted(**{field: "  "})[field], field)        # blank -> nothing
        self.assertEqual(accepted(territory="  KASUR   TOWN ")["territory"], "KASUR TOWN")
        self.assertEqual(accepted(address=" Canal Road \n\n Block B ")["address"], "Canal Road\nBlock B")

    def test_email_and_phone(self):
        for good in ("a@b.co", "accounts@crescent-dye.example", "first.last+tag@sub.domain.pk"):
            self.assertEqual(accepted(email=good)["email"], good)
        for bad in ("plainaddress", "a@b", "@b.co", "a@.", "a b@c.com", "a@b@c.com"):
            refused("does not look like an email", email=bad)
        for good in ("0300-7520665", "+92 42 3590000", "042-111-000-111 ext 12"):
            self.assertEqual(accepted(phone=good)["phone"], good)
        refused("Phone must contain a number", phone="call me")

    def test_licence_expiry_date(self):
        self.assertEqual(accepted(lic_expiry_date="2027-03-05")["lic_expiry_date"], date(2027, 3, 5))
        self.assertIsNone(accepted(lic_expiry_date="")["lic_expiry_date"])
        self.assertEqual(accepted(lic_expiry_date="2028-02-29")["lic_expiry_date"], date(2028, 2, 29))   # a leap day
        for bad in ("2027-02-29", "2028-13-01", "05/03/2027", "tomorrow", "2027-03-05; DROP TABLE customers"):
            refused("not a valid date", lic_expiry_date=bad)
        for bad in ("0001-01-01", "1899-12-31", "2201-01-01", "9999-12-31"):
            refused("between the years 1900 and 2200", lic_expiry_date=bad)

    def test_credit_limit(self):
        self.assertEqual(accepted(credit_limit="")["credit_limit"], D("0.00"))          # empty = no limit set
        self.assertEqual(accepted(credit_limit="0")["credit_limit"], D("0.00"))
        self.assertEqual(accepted(credit_limit="2500000")["credit_limit"], D("2500000.00"))
        self.assertEqual(accepted(credit_limit=" 40000000.00 ")["credit_limit"], D("40000000.00"))
        self.assertEqual(accepted(credit_limit="100.005")["credit_limit"], D("100.01"))  # rounded half up
        self.assertEqual(accepted(credit_limit="100.004")["credit_limit"], D("100.00"))
        self.assertEqual(str(accepted(credit_limit="-0")["credit_limit"]), "0.00")      # never "-0.00"
        self.assertEqual(accepted(credit_limit="999999999999.99")["credit_limit"], MAX_CREDIT_LIMIT)
        for too_large in ("1000000000000", "999999999999.995", "1e15", "1e999999"):
            refused("too large", credit_limit=too_large)
        refused("cannot be negative", credit_limit="-1")
        for not_a_number in ("abc", "NaN", "Infinity", "-Infinity", "1,000", "0x10", "12 34"):
            refused("must be a number", credit_limit=not_a_number)
        for value in ("0", "2500000", "100.005", "999999999999.99"):                    # always Decimal, never float
            self.assertIsInstance(accepted(credit_limit=value)["credit_limit"], D)

    def test_parse_amount_on_its_own(self):
        self.assertEqual(parse_amount("12.345", "Amount"), D("12.35"))
        self.assertEqual(parse_amount("0", "Amount", allow_zero=True), D("0.00"))
        with self.assertRaises(ValidationError):
            parse_amount("0", "Amount")
        with self.assertRaises(ValidationError):
            parse_amount("", "Amount", allow_zero=True)

    def test_status_on_the_edit_card(self):
        def edit(**typed):
            return clean_customer_form(dict({"name": "Acme Traders"}, **typed), editing=True)
        self.assertIs(edit(status="active")[0]["is_active"], True)
        self.assertIs(edit(status="inactive")[0]["is_active"], False)
        self.assertIsNone(edit()[0]["is_active"])                 # not sent: the customer stays as it is
        self.assertEqual(edit()[1], [])
        data, problems = edit(status="deleted")
        self.assertIsNone(data["is_active"])
        self.assertEqual(problems, ["Status must be Active or Inactive."])

    def test_all_mistakes_are_reported_together(self):
        data, problems = clean_customer_form({
            "name": "  ", "email": "nobody", "lic_expiry_date": "31-12-2026", "credit_limit": "-5", "ntn": "9" * 31})
        self.assertEqual(len(problems), 5, problems)
        self.assertEqual([problem.split(" ")[0] for problem in problems],
                         ["Customer", "NTN", "Email", "LIC", "Credit"], problems)

    def test_what_the_card_shows(self):
        blank = blank_form()
        self.assertEqual(set(blank), set(FORM_FIELDS))
        self.assertEqual((blank["partner_type"], blank["nature_of_supply"], blank["credit_limit"], blank["status"]),
                         ("company", "Supplies", "0.00", "active"))
        self.assertTrue(all(blank[field] == "" for field in blank
                            if field not in ("partner_type", "nature_of_supply", "credit_limit", "status")))

        customer = dict.fromkeys(CUSTOMER_COLUMNS)
        customer.update(customer_id=7, customer_no=3, name="Ravi Tanneries", is_company=False,
                        nature_of_supply="Services", credit_limit=D("1000000"),
                        lic_expiry_date=date(2026, 12, 31), territory="KASUR", is_active=False)
        shown = form_from_customer(customer)
        self.assertEqual(set(shown), set(FORM_FIELDS))
        self.assertEqual((shown["name"], shown["partner_type"], shown["nature_of_supply"], shown["credit_limit"],
                          shown["lic_expiry_date"], shown["territory"], shown["status"]),
                         ("Ravi Tanneries", "person", "Services", "1000000.00", "2026-12-31", "KASUR", "inactive"))
        self.assertEqual((shown["email"], shown["address"]), ("", ""))       # nothing saved shows empty, not "None"
        self.assertEqual(form_from_customer(dict(customer, is_active=None))["status"], "active")

        # a refused card comes back with every box as the user left it - also the wrong ones
        typed = form_as_typed({"name": "  New  Name ", "credit_limit": "abc", "lic_expiry_date": "31-12-2026",
                               "territory": "KSR"}, base=shown)
        self.assertEqual((typed["name"], typed["credit_limit"], typed["lic_expiry_date"], typed["territory"]),
                         ("  New  Name ", "abc", "31-12-2026", "KSR"))
        self.assertEqual((typed["partner_type"], typed["status"]), ("person", "inactive"))   # unsent boxes keep saved
        self.assertEqual(form_as_typed({"name": "X"})["nature_of_supply"], "Supplies")
        self.assertEqual(form_as_typed({"name": "A\x00B"})["name"], "AB")

    def test_how_the_list_writes_values(self):
        self.assertEqual(format_amount(D("1234567.5")), "1,234,567.50")
        self.assertEqual(format_amount(D("40000000")), "40,000,000.00")
        self.assertEqual((format_amount(0), format_amount(None)), ("0.00", "0.00"))
        self.assertEqual(format_amount(D("999999999999.99")), "999,999,999,999.99")
        self.assertEqual(format_date(date(2027, 3, 5)), "05 Mar 2027")
        self.assertEqual(format_date(date(2026, 12, 31)), "31 Dec 2026")
        self.assertEqual(format_date(None), "")

        customer = dict.fromkeys(CUSTOMER_COLUMNS)
        customer.update(customer_id=7, customer_no=10450, name="Sample Traders", credit_limit=D("40000000"),
                        lic_expiry_date=date(2027, 3, 5), address="Canal Road\nBlock B", is_active=True)
        row = list_row(customer)
        self.assertEqual((row["customer_id"], row["is_active"]), (7, True))
        self.assertEqual(row["cells"]["customer_no"], "10450")              # an ID is written plainly, no comma
        self.assertEqual(row["cells"]["credit_limit"], "40,000,000.00")
        self.assertEqual(row["cells"]["lic_expiry_date"], "05 Mar 2027")
        self.assertEqual(row["cells"]["address"], "Canal Road Block B")     # one line on the list
        self.assertEqual(row["cells"]["email"], "")                          # empty stays empty, never "None"
        self.assertEqual(set(row["cells"]), {column["key"] for column in LIST_COLUMNS})
        self.assertIs(list_row(dict(customer, is_active=False))["is_active"], False)

    def test_list_columns(self):
        keys = [column["key"] for column in LIST_COLUMNS]
        self.assertEqual(len(keys), len(set(keys)))                          # no column twice
        self.assertTrue(set(keys) <= set(CUSTOMER_COLUMNS))                  # every column is a real field
        self.assertEqual(keys[:2], ["customer_no", "name"])                  # ID first, then the name
        self.assertEqual([column["key"] for column in LIST_COLUMNS if column.get("fixed")], ["name"])
        self.assertTrue(all(column["kind"] in ("text", "number", "amount", "date", "name")
                            for column in LIST_COLUMNS))
        self.assertEqual([column["label"] for column in LIST_COLUMNS if column["shown"]], [
            "ID", "Name", "Credit Limit", "Email", "Phone", "Region", "Territory", "LIC Number",
            "LIC Expiry Date", "Category Parent", "Country"])
        self.assertEqual([column["label"] for column in LIST_COLUMNS if not column["shown"]], [
            "NTN", "Street", "City", "State", "STRN", "CNIC", "Nature of Supply", "Category"])
        self.assertEqual(sum(1 for column in LIST_COLUMNS if column["shown"]), 11)

    def test_search_text(self):
        self.assertEqual(clean_search("  kasur   traders "), "kasur traders")
        self.assertEqual((clean_search(None), clean_search("\x00")), ("", ""))
        self.assertEqual(len(clean_search("x" * 500)), 100)
        self.assertEqual(like_pattern("kasur"), "%kasur%")
        self.assertEqual(like_pattern("50%"), "%50\\%%")                     # % means itself
        self.assertEqual(like_pattern("a_b"), "%a\\_b%")                     # so does _
        self.assertEqual(like_pattern("back\\slash"), "%back\\\\slash%")

    def test_the_name_lock_looks_at_account_1100(self):
        # ZYRO marks Trade Receivables by its code, not by an account kind.
        self.assertEqual(TRADE_RECEIVABLE_CODE, "1100")
        self.assertIn("journal entry remembers its customer by name", NAME_LOCKED_MESSAGE)


    def test_tags(self):
        self.assertEqual(clean_tags("Punjab, VIP"), "Punjab, VIP")
        self.assertEqual(clean_tags("  Punjab ,VIP,  , Punjab "), "Punjab, VIP")   # tidied, typed twice kept once
        self.assertEqual(clean_tags("vip, VIP"), "vip")                            # the first spelling wins
        self.assertEqual(clean_tags("a,b,c"), "a, b, c")
        self.assertIsNone(clean_tags(""))                                          # empty box
        self.assertIsNone(clean_tags("  ,  , "))                                   # only commas
        self.assertIsNone(clean_tags(None))
        self.assertEqual(tag_list("Punjab, VIP"), ["Punjab", "VIP"])
        self.assertEqual(tag_list(None), [])
        # a tag too long, and too many tags, are refused with a message
        with self.assertRaises(ValidationError):
            clean_tags("x" * (TAG_MAX_LENGTH + 1))
        with self.assertRaises(ValidationError):
            clean_tags(", ".join(f"t{number}" for number in range(TAGS_MAX_COUNT + 1)))
        with self.assertRaises(ValidationError):                                   # together wider than the column
            clean_tags(", ".join(chr(65 + number) * TAG_MAX_LENGTH for number in range(TAGS_MAX_COUNT)))
        refused("at most 40 characters", tags="x" * (TAG_MAX_LENGTH + 1))
        refused("At most 20 tags", tags=", ".join(f"t{number}" for number in range(TAGS_MAX_COUNT + 1)))
        self.assertEqual(accepted(tags=" Punjab , VIP ")["tags"], "Punjab, VIP")

    def test_website(self):
        self.assertEqual(clean_website("  www.example.com "), "www.example.com")   # the cleaner on its own
        for good in ("www.example.com", "example.com", "www.example", "https://www.example.com/about?x=1",
                     "http://a.b"):
            self.assertEqual(accepted(website=good)["website"], good)
        self.assertIsNone(accepted(website="   ")["website"])
        for bad in ("example", "www .example.com", "www.example.", ".example.com", "not a site",
                    "example.com/ spaces"):
            refused("does not look like a web address", website=bad)
        refused("too long", website="www." + "x" * 260 + ".com")

    def test_currency(self):
        self.assertEqual(clean_currency("pkr"), "PKR")
        self.assertEqual(accepted(currency="pkr")["currency"], "PKR")          # capitals, as codes are written
        self.assertEqual(accepted(currency=" usd ")["currency"], "USD")
        self.assertIsNone(accepted(currency="")["currency"])                   # empty = the company's currency
        for bad in ("PK", "PKRX", "P K R", "12R", "روپیہ"):
            refused("three-letter code", currency=bad)

    def test_also_a_vendor_tick(self):
        for ticked in ("1", "on", "true", "yes", " TRUE "):
            self.assertIs(accepted(is_vendor=ticked)["is_vendor"], True)
        for unticked in ("", "0", "off", "no", None):
            self.assertIs(accepted(is_vendor=unticked)["is_vendor"], False)
        self.assertIs(clean_customer_form({"name": "A"})[0]["is_vendor"], False)   # box not sent at all

    def test_the_four_new_boxes_round_trip(self):
        blank = blank_form()
        for field in ("tags", "is_vendor", "currency", "website"):
            self.assertIn(field, FORM_FIELDS)
            self.assertIn(field, blank)
        self.assertEqual(blank["is_vendor"], "")
        customer = dict.fromkeys(CUSTOMER_COLUMNS)
        customer.update(customer_id=3, name="Tagged Traders", tags="Punjab, VIP", is_vendor=True,
                        currency="USD", website="www.example.com", is_active=True)
        shown = form_from_customer(customer)
        self.assertEqual((shown["tags"], shown["is_vendor"], shown["currency"], shown["website"]),
                         ("Punjab, VIP", "1", "USD", "www.example.com"))
        # a refused card keeps the tick as the user left it
        self.assertEqual(form_as_typed({"name": "X", "is_vendor": "1", "tags": "a,b"})["tags"], "a,b")
        self.assertEqual(form_as_typed({"name": "X", "is_vendor": "1"})["is_vendor"], "1")
        self.assertEqual(form_as_typed({"name": "X"})["is_vendor"], "")

    def test_notes(self):
        # the lines he typed stay as he typed them
        self.assertEqual(clean_notes("Pays by cheque.\n\nAsk for Mr. Imran."),
                         "Pays by cheque.\n\nAsk for Mr. Imran.")
        self.assertEqual(clean_notes("  two lines \r\n second   \r\n"), "two lines\n second")
        self.assertIsNone(clean_notes(""))                       # an empty box saves nothing
        self.assertIsNone(clean_notes("   \n  \n "))             # only spaces and lines
        self.assertIsNone(clean_notes(None))
        self.assertEqual(len(clean_notes("x" * NOTES_MAX_LENGTH)), NOTES_MAX_LENGTH)
        with self.assertRaises(ValidationError):
            clean_notes("x" * (NOTES_MAX_LENGTH + 1))

    def test_the_credit_position(self):
        totals = {"due": D("250000.00")}
        position = credit_position(totals, D("40000000"))
        self.assertEqual(position["limit"], D("40000000.00"))
        self.assertEqual(position["room"], D("39750000.00"))
        self.assertEqual(position["limit_text"], "40,000,000.00")
        self.assertEqual(position["room_text"], "39,750,000.00")
        self.assertTrue(position["has_limit"])
        # over the limit, and no limit at all
        over = credit_position({"due": D("500.00")}, D("100"))
        self.assertEqual(over["room"], D("-400.00"))
        self.assertTrue(over["has_limit"])
        nothing = credit_position({"due": D("0")}, None)
        self.assertEqual(nothing["limit"], D("0.00"))
        self.assertFalse(nothing["has_limit"])

    def test_a_contact_card(self):
        data, problems = clean_contact_form({"name": "  Ali   Raza "})
        self.assertEqual(problems, [])
        self.assertEqual(data["name"], "Ali Raza")
        for field, _label, _max_length in CONTACT_TEXT_FIELDS:
            self.assertIsNone(data[field], field)
        self.assertEqual(set(data), set(CONTACT_FORM_FIELDS))
        # a contact needs a name, and nothing more
        self.assertEqual(clean_contact_form({})[1], ["Contact name is required."])
        self.assertEqual(clean_contact_form({"name": "   "})[1], ["Contact name is required."])
        self.assertEqual(clean_contact_form({"name": "A" * 151})[1],
                         ["Contact name is too long (maximum 150 characters)."])
        problems = clean_contact_form({"name": "Ali", "email": "nobody", "phone": "call me",
                                       "city": "c" * 101, "job_position": "j" * 101})[1]
        self.assertEqual(len(problems), 4, problems)
        # the boxes of a contact, blank and filled from one
        self.assertEqual(blank_contact_form()["name"], "")
        contact = dict.fromkeys(CONTACT_FORM_FIELDS + ["contact_id", "is_primary"])
        contact.update(contact_id=9, name="Accounts Office", job_position="Manager", email="a@b.co",
                       city="Lahore", is_primary=True)
        shown = contact_form_from_contact(contact)
        self.assertEqual((shown["name"], shown["job_position"], shown["email"], shown["city"]),
                         ("Accounts Office", "Manager", "a@b.co", "Lahore"))
        self.assertEqual(shown["street"], "")
        self.assertEqual(contact_form_as_typed({"name": "  New  Name ", "city": "x" * 101}, base=shown)["name"],
                         "  New  Name ")

# ---------------------------------------------------------------------------
# The screens, on a real (test) database
# ---------------------------------------------------------------------------
@unittest.skipIf(not os.getenv("DATABASE_URL"), "no DATABASE_URL: database checks skipped")
class CustomerCardDatabaseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app = flask_app.app
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        from core.db import get_conn
        cls.org_name = f"TEST_CUSTOMER_CARD_{uuid.uuid4().hex[:8]}"
        cls.other_org_name = f"TEST_CUSTOMER_CARD_OTHER_{uuid.uuid4().hex[:8]}"
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (cls.org_name,))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') RETURNING organization_id",
                       (cls.other_org_name,))
        cls.other_org_id = cursor.fetchone()[0]
        # a real user, because every request is checked against the users table
        cls.user_email = f"test_{uuid.uuid4().hex[:8]}@customer-card.local"
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Customer Card Tester', %s, 'hash', TRUE) RETURNING user_id", (cls.user_email,))
        cls.user_id = cursor.fetchone()[0]
        # 1100 Trade Receivables, as the receipt and invoice screens expect
        for org_id in (cls.org_id, cls.other_org_id):
            cursor.execute("""
                INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active)
                VALUES (%s, '1100', 'Trade Receivables', 'Asset', TRUE)
            """, (org_id,))
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for org_id in (cls.org_id, cls.other_org_id):
            for table in ("customer_receipts", "customer_contacts"):
                cursor.execute(f"DELETE FROM {table} WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                           "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM customers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                           "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM chart_of_accounts WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organization_memberships WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (cls.user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    # -- helpers ---------------------------------------------------------
    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        # everything this class writes, in the order the foreign keys allow
        for org_id in (self.org_id, self.other_org_id):
            for table in ("customer_receipts", "customer_contacts"):
                cursor.execute(f"DELETE FROM {table} WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM sales_invoice_lines WHERE sales_invoice_id IN "
                           "(SELECT sales_invoice_id FROM sales_invoices WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM sales_invoices WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM journal_lines WHERE journal_header_id IN "
                           "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)", (org_id,))
            cursor.execute("DELETE FROM journal_headers WHERE organization_id = %s", (org_id,))
            cursor.execute("DELETE FROM customers WHERE organization_id = %s", (org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Customer Card Tester"
            flask_session["csrf_token"] = "test-csrf"

    def save(self, url="/customers/new", **typed):
        """Post the card and return the response."""
        form = {"csrf_token": "test-csrf", "name": "Acme Traders", "partner_type": "company",
                "nature_of_supply": "Supplies", "credit_limit": "0", "status": "active"}
        form.update(typed)
        return self.client.post(url, data=form, follow_redirects=False)

    def customers(self, org_id=None):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT customer_id, customer_no, name, is_active FROM customers "
                       "WHERE organization_id = %s ORDER BY customer_id", (org_id or self.org_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    # -- the ID ----------------------------------------------------------
    def test_a_new_customer_gets_an_id_and_the_card_shows_it(self):
        self.assertEqual(self.save(name="First Customer").status_code, 302)
        self.assertEqual(self.save(name="Second Customer").status_code, 302)
        rows = self.customers()
        self.assertEqual([row[1] for row in rows], [1, 2])                   # 1, 2 - a running number
        customer_id = rows[0][0]
        page = self.client.get(f"/customers/{customer_id}/edit").get_data(as_text=True)
        self.assertIn("Edit Customer", page)
        self.assertIn(">1<", page)                                          # the ID is on the card, read-only
        self.assertIn("Active", page)                                       # the Status box
        self.assertIn("Inactive", page)

    def test_the_id_is_per_company_not_shared(self):
        self.save(name="Our Only Customer")
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, 1, %s)",
                       (self.other_org_id, "Their Customer"))
        conn.commit()
        cursor.close()
        conn.close()
        # the other company's customer does not push our numbering along
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.other_org_id
        self.save(name="Their Second Customer")
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
        self.save(name="Our Second Customer")
        self.assertEqual([row[1] for row in self.customers()], [1, 2])
        self.assertEqual([row[1] for row in self.customers(self.other_org_id)], [1, 2])

    # -- what is refused ------------------------------------------------
    def test_a_card_with_a_mistake_is_shown_again_with_everything_typed(self):
        response = self.save(name="  ", credit_limit="abc", territory="KSR", email="nobody")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nothing was saved", page)
        self.assertIn("Customer name is required.", page)
        self.assertIn("Credit limit must be a number.", page)
        self.assertIn("Email does not look like an email address", page)
        self.assertIn('value="KSR"', page)                                  # nothing typed is lost
        self.assertEqual(self.customers(), [])                              # and nothing was saved

    def test_a_second_customer_with_the_same_name_is_refused(self):
        self.save(name="Ali Traders")
        response = self.save(name="  ali   TRADERS ")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("already exists", page)
        self.assertIn("ID 1", page)
        self.assertEqual(len(self.customers()), 1)

    def test_another_company_may_use_the_same_name(self):
        self.save(name="Ali Traders")
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.other_org_id
        response = self.save(name="Ali Traders")
        self.assertEqual(response.status_code, 302)                         # refused only inside one company

    # -- the name lock --------------------------------------------------
    def test_the_name_is_locked_once_the_customer_has_a_receipt(self):
        from core.db import get_conn
        self.save(name="Locked Traders")
        customer_id = self.customers()[0][0]
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date)
            VALUES (%s, %s, 100, CURRENT_DATE)
        """, (self.org_id, customer_id))
        conn.commit()
        cursor.close()
        conn.close()

        page = self.client.get(f"/customers/{customer_id}/edit").get_data(as_text=True)
        self.assertIn("readonly", page)                                     # the box cannot be typed in
        self.assertIn("cannot be changed", page)

        response = self.save(url=f"/customers/{customer_id}/edit", name="Renamed Traders")
        self.assertIn("cannot be changed", response.get_data(as_text=True))
        self.assertEqual(self.customers()[0][2], "Locked Traders")          # the name did not change

        # everything else on the card may still be changed
        response = self.save(url=f"/customers/{customer_id}/edit", name="Locked Traders",
                             territory="KASUR", is_active="true", status="inactive")
        self.assertEqual(response.status_code, 302)
        self.assertIs(self.customers()[0][3], False)                        # Inactive was saved

    def test_the_name_is_locked_by_a_journal_line_on_1100(self):
        from core.db import get_conn
        self.save(name="Ledger Traders")
        customer_id = self.customers()[0][0]
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO journal_headers (organization_id, accounting_date, journal_name) "
                       "VALUES (%s, CURRENT_DATE, 'Manual') RETURNING journal_header_id", (self.org_id,))
        header_id = cursor.fetchone()[0]
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1100'",
                       (self.org_id,))
        account_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, debit, credit, partner_name) "
                       "VALUES (%s, %s, 100, 0, %s)", (header_id, account_id, "Ledger Traders"))
        conn.commit()
        cursor.close()
        conn.close()
        response = self.save(url=f"/customers/{customer_id}/edit", name="Some Other Name")
        self.assertIn("cannot be changed", response.get_data(as_text=True))

    def test_a_name_that_is_not_changed_saves_without_the_lock(self):
        from core.db import get_conn
        self.save(name="Same Name Traders")
        customer_id = self.customers()[0][0]
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date)
            VALUES (%s, %s, 100, CURRENT_DATE)
        """, (self.org_id, customer_id))
        conn.commit()
        cursor.close()
        conn.close()
        # spaces only: the name counts as unchanged, and is stored exactly as it was
        response = self.save(url=f"/customers/{customer_id}/edit", name="  Same  Name Traders ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.customers()[0][2], "Same Name Traders")

    # -- the list and its search ----------------------------------------
    def test_the_list_shows_the_id_name_and_columns(self):
        self.save(name="Alpha Traders", region="Punjab", credit_limit="40000000")
        self.save(name="Beta Traders", region="Kasur")
        page = self.client.get("/customers/").get_data(as_text=True)
        self.assertIn("Alpha Traders", page)
        self.assertIn("Beta Traders", page)
        self.assertIn("Credit Limit", page)
        self.assertIn("40,000,000.00", page)                                # amounts with separators
        self.assertIn("Columns", page)                                      # the tick-box menu
        self.assertEqual(page.count('type="checkbox" data-col='),
                         len([c for c in LIST_COLUMNS if not c.get("fixed")]))   # one tick-box per column
        self.assertIn("2 customers", page)

    def test_the_search_finds_by_every_box(self):
        self.save(name="Alpha Traders", region="Punjab", city="Lahore", ntn="1234567-8",
                  phone="0300-7520665", email="alpha@traders.example", lic_number="LIC-77")
        self.save(name="Beta Tanneries", region="Kasur")
        for typed in ("alpha", "PUNJAB", "lahore", "1234567", "7520665", "alpha@traders", "LIC-77", "1"):
            page = self.client.get(f"/customers/?q={typed}").get_data(as_text=True)
            self.assertIn("Alpha Traders", page, typed)                  # by name, region, city, NTN, phone,
            self.assertNotIn("Beta Tanneries", page, typed)              # email, LIC number and ID
        page = self.client.get("/customers/?q=kasur").get_data(as_text=True)
        self.assertIn("Beta Tanneries", page)
        self.assertNotIn("Alpha Traders", page)
        # every word must be found
        page = self.client.get("/customers/?q=alpha+kasur").get_data(as_text=True)
        self.assertIn("No customer matches", page)
        # % typed by the user means itself, not "anything"
        page = self.client.get("/customers/?q=%25").get_data(as_text=True)
        self.assertIn("No customer matches", page)
        page = self.client.get("/customers/?q=nothing+matching+this").get_data(as_text=True)
        self.assertIn("No customer matches", page)

    def test_an_inactive_customer_stays_on_the_list_and_is_not_offered_on_a_receipt(self):
        self.save(name="Old Traders")
        customer_id = self.customers()[0][0]
        response = self.save(url=f"/customers/{customer_id}/edit", name="Old Traders", status="inactive")
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/customers/").get_data(as_text=True)
        self.assertIn("Old Traders", page)                                  # still on the list, with its history
        self.assertIn("Inactive", page)
        page = self.client.get("/customers/receipts/new").get_data(as_text=True)
        self.assertNotIn("Old Traders", page)                               # no longer offered

    def test_edit_of_a_customer_of_another_company_is_not_found(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, 1, %s) "
                       "RETURNING customer_id", (self.other_org_id, "Not Ours"))
        other_customer_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.client.get(f"/customers/{other_customer_id}/edit").status_code, 404)

    # -- the rest of the card: tags, vendor mark, currency -----------------
    def test_the_four_new_boxes_are_saved(self):
        self.save(name="Tagged Traders", tags=" Punjab , VIP , Punjab ", is_vendor="1",
                  currency="usd", website="www.example.com")
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT tags, is_vendor, currency, website FROM customers WHERE organization_id = %s",
                       (self.org_id,))
        self.assertEqual(cursor.fetchone(), ("Punjab, VIP", True, "USD", "www.example.com"))
        cursor.close()
        conn.close()
        customer_id = self.customers()[0][0]
        page = self.client.get(f"/customers/{customer_id}/edit").get_data(as_text=True)
        self.assertIn('value="Punjab, VIP"', page)
        self.assertIn(">Punjab<", page)                                     # shown as a pill as well
        self.assertIn('value="USD"', page)
        self.assertIn("checked", page)                                      # Also a vendor is ticked

    def test_a_bad_currency_or_website_is_refused_and_nothing_is_saved(self):
        response = self.save(name="Bad Boxes", currency="PKRX", website="not a site")
        page = response.get_data(as_text=True)
        self.assertIn("three-letter code", page)
        self.assertIn("does not look like a web address", page)
        self.assertIn('value="PKRX"', page)                                 # what was typed is kept
        self.assertEqual(self.customers(), [])

    # -- contacts ----------------------------------------------------------
    def contact(self, customer_id, **typed):
        form = {"csrf_token": "test-csrf", "name": "Accounts Office"}
        form.update(typed)
        return self.client.post(f"/customers/{customer_id}/contacts", data=form)

    def contacts(self, customer_id):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT contact_id, name, email, city, is_primary FROM customer_contacts "
                       "WHERE organization_id = %s AND customer_id = %s ORDER BY contact_id",
                       (self.org_id, customer_id))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    def test_the_first_contact_is_primary_and_a_second_is_not(self):
        self.save(name="Contacted Traders")
        customer_id = self.customers()[0][0]
        self.assertEqual(self.contact(customer_id, name="Ali Raza", job_position="Manager",
                                      email="ali@traders.example", phone="0300-1234567",
                                      city="Lahore").status_code, 302)
        self.contact(customer_id, name="Delivery Gate", city="Kasur")
        rows = self.contacts(customer_id)
        self.assertEqual([row[1] for row in rows], ["Ali Raza", "Delivery Gate"])
        self.assertEqual([row[4] for row in rows], [True, False])          # one primary, the first

        page = self.client.get(f"/customers/{customer_id}/edit?tab=contacts").get_data(as_text=True)
        self.assertIn("Contacts &amp; Addresses", page)
        self.assertIn("Ali Raza", page)
        self.assertIn("ali@traders.example", page)
        self.assertIn("Manager", page)
        self.assertIn("Primary", page)
        self.assertIn("Make primary", page)                                # offered on the second one

    def test_make_primary_moves_the_mark(self):
        self.save(name="Primary Traders")
        customer_id = self.customers()[0][0]
        self.contact(customer_id, name="First Person")
        self.contact(customer_id, name="Second Person")
        second_id = self.contacts(customer_id)[1][0]
        response = self.client.post(f"/customers/{customer_id}/contacts/{second_id}/primary",
                                    data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual([row[4] for row in self.contacts(customer_id)], [False, True])

    def test_removing_the_primary_hands_it_to_the_oldest_left(self):
        self.save(name="Removable Traders")
        customer_id = self.customers()[0][0]
        self.contact(customer_id, name="First Person")
        self.contact(customer_id, name="Second Person")
        self.contact(customer_id, name="Third Person")
        first_id = self.contacts(customer_id)[0][0]
        self.client.post(f"/customers/{customer_id}/contacts/{first_id}/delete", data={"csrf_token": "test-csrf"})
        rows = self.contacts(customer_id)
        self.assertEqual([row[1] for row in rows], ["Second Person", "Third Person"])
        self.assertEqual([row[4] for row in rows], [True, False])           # never without a primary

    def test_a_contact_with_a_mistake_is_refused_and_nothing_is_added(self):
        self.save(name="Mistake Traders")
        customer_id = self.customers()[0][0]
        response = self.contact(customer_id, name="  ", email="nobody", city="c" * 101)
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Contact name is required.", page)
        self.assertIn("Email does not look like an email address", page)
        self.assertIn("City is too long", page)
        self.assertIn('value="nobody"', page)                               # the typed values are kept
        self.assertEqual(self.contacts(customer_id), [])

    def test_editing_a_contact(self):
        self.save(name="Edited Traders")
        customer_id = self.customers()[0][0]
        self.contact(customer_id, name="Old Name", city="Lahore")
        contact_id = self.contacts(customer_id)[0][0]
        page = self.client.get(f"/customers/{customer_id}/edit?contact={contact_id}").get_data(as_text=True)
        self.assertIn("Change this contact", page)                          # the card is open for changing
        self.assertIn('value="Old Name"', page)
        response = self.client.post(f"/customers/{customer_id}/contacts/{contact_id}",
                                    data={"csrf_token": "test-csrf", "name": "New Name",
                                          "city": "Kasur", "email": "new@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.contacts(customer_id)[0][1:4], ("New Name", "new@example.com", "Kasur"))

    def test_a_contact_cannot_be_reached_through_another_customer_or_company(self):
        self.save(name="Ours")
        self.save(name="Also Ours")
        our_first, our_second = [row[0] for row in self.customers()]
        self.contact(our_first, name="Private Contact")
        contact_id = self.contacts(our_first)[0][0]
        # the same company, but the wrong customer: nothing happens
        self.client.post(f"/customers/{our_second}/contacts/{contact_id}/delete", data={"csrf_token": "test-csrf"})
        self.assertEqual(len(self.contacts(our_first)), 1)
        # another company's customer: not found
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) VALUES (%s, 1, %s) "
                       "RETURNING customer_id", (self.other_org_id, "Theirs"))
        their_customer = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.client.post(f"/customers/{their_customer}/contacts",
                                          data={"csrf_token": "test-csrf", "name": "Sneaky"}).status_code, 404)

    # -- the three figures at the top --------------------------------------
    def invoice(self, customer_id, total, status, day="2026-11-01"):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_invoices (organization_id, customer_id, invoice_date, status)
            VALUES (%s, %s, %s, %s) RETURNING sales_invoice_id
        """, (self.org_id, customer_id, day, status))
        invoice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_invoice_lines (sales_invoice_id, line_order, label, quantity, rate, total) "
                       "VALUES (%s, 0, 'Line', 1, %s, %s)", (invoice_id, total, total))
        conn.commit()
        cursor.close()
        conn.close()
        return invoice_id

    def receipt(self, customer_id, amount):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customer_receipts (organization_id, customer_id, amount, receipt_date) "
                       "VALUES (%s, %s, %s, '2026-11-02')", (self.org_id, customer_id, amount))
        conn.commit()
        cursor.close()
        conn.close()

    def test_the_three_figures(self):
        self.save(name="Figures Traders")
        customer_id = self.customers()[0][0]
        self.invoice(customer_id, "2000.00", "Posted")
        self.invoice(customer_id, "1000.00", "Draft")
        self.receipt(customer_id, "500.00")
        page = self.client.get(f"/customers/{customer_id}/edit").get_data(as_text=True)
        self.assertIn("3,000.00", page)                                    # Sales: everything entered
        self.assertIn("2,000.00", page)                                    # Invoiced: the posted one
        self.assertIn("1,500.00", page)                                    # Due: invoiced less received
        self.assertIn("2 invoices entered", page)
        self.assertIn("1 in the books (posted)", page)

    def test_the_figures_are_per_customer_and_show_a_negative_due(self):
        self.save(name="Owed Traders")
        self.save(name="Prepaid Traders")
        owed_id, prepaid_id = [row[0] for row in self.customers()]
        self.invoice(owed_id, "1000.00", "Posted")
        self.receipt(owed_id, "250.00")
        self.receipt(prepaid_id, "900.00")                                  # paid before being invoiced
        page = self.client.get(f"/customers/{owed_id}/edit").get_data(as_text=True)
        self.assertIn("750.00", page)                                      # 1,000 - 250
        self.assertNotIn("900.00", page)                                    # the other customer's receipt
        page = self.client.get(f"/customers/{prepaid_id}/edit").get_data(as_text=True)
        self.assertIn("-900.00", page)                                     # he is in advance
        self.assertIn("lp-total-value negative", page)

    # -- the Notes tab -----------------------------------------------------
    def note(self, customer_id, text):
        return self.client.post(f"/customers/{customer_id}/notes",
                                data={"csrf_token": "test-csrf", "notes": text})

    def stored_notes(self, customer_id):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT notes FROM customers WHERE customer_id = %s", (customer_id,))
        notes = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return notes

    def test_a_note_is_saved_as_typed_and_nothing_else_changes(self):
        self.save(name="Noted Traders", credit_limit="5000", tags="Punjab")
        customer_id = self.customers()[0][0]
        response = self.note(customer_id, "  Pays by cheque on Fridays.\r\n\r\nAsk for Mr. Imran.  ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored_notes(customer_id),
                         "Pays by cheque on Fridays.\n\nAsk for Mr. Imran.")
        # the rest of the card is exactly as it was
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT credit_limit, tags FROM customers WHERE customer_id = %s", (customer_id,))
        self.assertEqual(cursor.fetchone(), (D("5000.00"), "Punjab"))
        cursor.close()
        conn.close()
        page = self.client.get(f"/customers/{customer_id}/edit?tab=notes").get_data(as_text=True)
        self.assertIn("Pays by cheque on Fridays.", page)
        self.assertIn("Ask for Mr. Imran.", page)

    def test_an_empty_note_empties_the_tab(self):
        self.save(name="Emptied Traders")
        customer_id = self.customers()[0][0]
        self.note(customer_id, "Something")
        self.assertEqual(self.note(customer_id, "   ").status_code, 302)
        self.assertIsNone(self.stored_notes(customer_id))

    def test_a_note_that_is_too_long_is_refused_and_nothing_is_saved(self):
        self.save(name="Wordy Traders")
        customer_id = self.customers()[0][0]
        response = self.note(customer_id, "x" * (NOTES_MAX_LENGTH + 1))
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Notes are too long", page)
        self.assertIn("x" * 100, page)                      # what he typed is kept on the page
        self.assertIsNone(self.stored_notes(customer_id))

    def test_a_note_cannot_be_reached_through_another_company(self):
        self.save(name="Ours")
        customer_id = self.customers()[0][0]
        self.save(name="Theirs", url="/customers/new")      # same company, second customer
        other = self.customers()[1][0]
        self.note(other, "belongs to the second")
        self.assertIsNone(self.stored_notes(customer_id))
        # another company's customer: not found
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (organization_id, customer_no, name) "
                       "VALUES (%s, 1, 'Their Customer') RETURNING customer_id", (self.other_org_id,))
        theirs = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        self.assertEqual(self.note(theirs, "sneaky").status_code, 404)

        # -- Sales & Purchase --------------------------------------------------
    def test_the_sales_tab_lists_his_invoices_and_receipts(self):
        self.save(name="Buying Traders", credit_limit="100000")
        self.save(name="Quiet Traders")
        buying_id, quiet_id = [row[0] for row in self.customers()]
        posted = self.invoice(buying_id, "2000.00", "Posted", day="2026-11-01")
        self.invoice(buying_id, "1000.00", "Draft", day="2026-11-05")
        self.receipt(buying_id, "500.00")
        page = self.client.get(f"/customers/{buying_id}/edit?tab=sales").get_data(as_text=True)
        self.assertIn("Sales invoices", page)
        self.assertIn("3,000.00", page)                     # 2,000 + 1,000, drafts included
        self.assertIn("2,000.00", page)
        self.assertIn("1,000.00", page)
        self.assertIn("Posted", page)
        self.assertIn("Draft", page)
        self.assertIn("Receipts received", page)
        self.assertIn("500.00", page)
        self.assertIn(f'<td class="lp-nowrap">{posted}</td>', page)   # the invoice's own number
        # the quiet customer has nothing
        page = self.client.get(f"/customers/{quiet_id}/edit?tab=sales").get_data(as_text=True)
        self.assertIn("No sales invoice for this customer yet.", page)
        self.assertIn("Nothing received from this customer yet.", page)
        self.assertNotIn("2,000.00", page)                  # never another customer's documents

    # -- Accounting --------------------------------------------------------
    def journal_line(self, name, debit, credit, day="2026-11-05", reference="JV/2026/0001", status="Posted"):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = '1100'",
                       (self.org_id,))
        account_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_headers (organization_id, accounting_date, journal_name, reference, status) "
                       "VALUES (%s, %s, 'Miscellaneous Operations', %s, %s) RETURNING journal_header_id",
                       (self.org_id, day, reference, status))
        header_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO journal_lines (journal_header_id, account_id, debit, credit, partner_name, label) "
                       "VALUES (%s, %s, %s, %s, %s, 'Freight charged')",
                       (header_id, account_id, debit, credit, name))
        conn.commit()
        cursor.close()
        conn.close()

    def test_the_accounting_tab_shows_what_the_books_say_about_him(self):
        self.save(name="Ledger Traders")
        self.save(name="Other Ledger Traders")
        his_id, other_id = [row[0] for row in self.customers()]
        self.journal_line("Ledger Traders", "2500.00", "0")
        self.journal_line("ledger traders", "0", "500.00", day="2026-11-06", reference="JV/2026/0002")
        self.journal_line("Other Ledger Traders", "9000.00", "0", reference="JV/2026/0003")
        page = self.client.get(f"/customers/{his_id}/edit?tab=accounting").get_data(as_text=True)
        self.assertIn("JV/2026/0001", page)
        self.assertIn("JV/2026/0002", page)                 # his name, whatever the capitals
        self.assertIn("2,500.00", page)
        self.assertIn("500.00", page)
        self.assertIn("PKR 2,000.00", page)                 # the balance: debit less credit
        self.assertNotIn("9,000.00", page)                  # never the other customer's lines
        # the other customer sees only his own
        page = self.client.get(f"/customers/{other_id}/edit?tab=accounting").get_data(as_text=True)
        self.assertIn("9,000.00", page)
        self.assertNotIn("JV/2026/0001", page)

    def test_the_accounting_tab_is_empty_for_a_customer_with_nothing_posted(self):
        self.save(name="Fresh Traders")
        customer_id = self.customers()[0][0]
        page = self.client.get(f"/customers/{customer_id}/edit?tab=accounting").get_data(as_text=True)
        self.assertIn("Nothing has been posted to account 1100", page)

    # -- Credit Limit ------------------------------------------------------
    def test_the_credit_tab_works_out_the_room_left(self):
        self.save(name="Limit Traders", credit_limit="10000")
        customer_id = self.customers()[0][0]
        self.invoice(customer_id, "4000.00", "Posted")
        self.receipt(customer_id, "1000.00")
        page = self.client.get(f"/customers/{customer_id}/edit?tab=credit").get_data(as_text=True)
        self.assertIn("10,000.00", page)                    # the limit
        self.assertIn("3,000.00", page)                     # due now: 4,000 invoiced less 1,000 received
        self.assertIn("7,000.00", page)                     # room left: 10,000 less 3,000

    def test_a_customer_with_no_limit_says_so_and_can_be_over_it(self):
        self.save(name="No Limit Traders", credit_limit="0")
        customer_id = self.customers()[0][0]
        self.invoice(customer_id, "4000.00", "Posted")
        page = self.client.get(f"/customers/{customer_id}/edit?tab=credit").get_data(as_text=True)
        self.assertIn("No limit set on the card above", page)
        self.assertIn("Over the limit", page)
        self.assertIn("-4,000.00", page)

    # -- the tabs themselves ----------------------------------------------
    def test_the_five_tabs_and_which_one_opens(self):
        self.save(name="Tab Traders")
        customer_id = self.customers()[0][0]
        page = self.client.get(f"/customers/{customer_id}/edit").get_data(as_text=True)
        for name in ("Contacts &amp; Addresses", "Sales &amp; Purchase", "Accounting", "Notes", "Credit Limit"):
            self.assertIn(f">{name}", page)
        # nothing asked for: the contacts, as on his own screen
        self.assertIn('data-tab-target="tab-contacts" aria-selected="true"', page)
        self.assertIn('id="tab-sales" role="tabpanel" hidden', page)
        for tab in ("sales", "accounting", "notes", "credit"):
            page = self.client.get(f"/customers/{customer_id}/edit?tab={tab}").get_data(as_text=True)
            self.assertIn(f'id="tab-{tab}" role="tabpanel">', page, tab)
            self.assertIn(f'data-tab-target="tab-{tab}" aria-selected="true"', page, tab)
        # a tab that does not exist opens the contacts rather than nothing
        page = self.client.get(f"/customers/{customer_id}/edit?tab=nonsense").get_data(as_text=True)
        self.assertIn('data-tab-target="tab-contacts" aria-selected="true"', page)
        # the card itself is always on the page, in front of the tabs
        self.assertIn('name="credit_limit"', page)

    def test_the_new_card_and_the_field_suggestions_open(self):
        self.save(name="Suggestion Traders", territory="KASUR TOWN", city="Lahore")
        page = self.client.get("/customers/new").get_data(as_text=True)
        self.assertIn("New Customer", page)
        self.assertIn("Given automatically when you save", page)            # no ID yet
        self.assertIn('value="KASUR TOWN"', page)                           # offered while typing
        for field, _label, max_length in TEXT_FIELDS:
            self.assertIn(f'maxlength="{max_length}"', page, field)


if __name__ == "__main__":
    unittest.main()
