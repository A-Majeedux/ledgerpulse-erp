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
    CUSTOMER_COLUMNS, FORM_FIELDS, LIST_COLUMNS, MAX_CREDIT_LIMIT, NAME_LOCKED_MESSAGE,
    NAME_MAX_LENGTH, NATURE_OF_SUPPLY_OPTIONS, TEXT_FIELDS, TRADE_RECEIVABLE_CODE,
    ValidationError, blank_form, clean_block, clean_customer_form, clean_line, clean_search,
    form_as_typed, form_from_customer, format_amount, format_date, like_pattern, list_row,
    parse_amount, same_name,
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
        self.assertEqual(set(data), {"name", "is_company", "nature_of_supply", "credit_limit", "lic_expiry_date"}
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
            for table, column in (("customer_receipts", "organization_id"), ("sales_invoices", "organization_id"),
                                  ("customers", "organization_id")):
                cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (org_id,))
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
            for table in ("customer_receipts", "sales_invoices"):
                cursor.execute(f"DELETE FROM {table} WHERE organization_id = %s", (org_id,))
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
