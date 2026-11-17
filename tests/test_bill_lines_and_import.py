"""
tests/test_bill_lines_and_import.py

What a purchase line can say, and the two papers a purchase comes on.

The bill screen was asked for four things it did not have - a unit, a discount,
a sales tax, and a way to buy an asset instead of an expense - and for the
letter of credit to stop sitting in the middle of an everyday bill. So:

  * **unit, discount, tax** are on the line, and the money is worked out on the
    server: quantity x rate, less the discount, plus the tax charged on what is
    left. The tax lands in 1250 Input Sales Tax Recoverable, never in the
    expense, and lines that share a rate share one journal line;
  * **an asset line** debits the fixed-asset account it names and keeps the
    years it will be used - capitalised, not expensed;
  * **the import screen** (`/vendors/bills/new-import`) is a different paper:
    it has the LC, the recipient bank and the landed-cost kind, and a standard
    bill has none of them. Both write the same table, so the books cannot tell
    which screen made a bill - only the bill's kind;
  * **the bill page stacks its tabs under the bill**, which it did not: the
    strip and the three panels sat in one flex row, so the lines were squeezed
    beside the journal. The last test pins the rule that fixes it.

Run: python -m pytest tests/test_bill_lines_and_import.py -q
"""
import re
import unittest
import uuid

from _people import member


class _Purchases(unittest.TestCase):
    """One company, a chart with input tax and fixed assets, and one 18% rate."""

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
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') "
                       "RETURNING organization_id", (f"TEST_LINES_{uuid.uuid4().hex[:8]}",))
        cls.org_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) "
                       "VALUES ('Lines Tester', %s, 'hash', TRUE) RETURNING user_id",
                       (f"lines_{uuid.uuid4().hex[:8]}@x.local",))
        cls.user_id = cursor.fetchone()[0]
        member(cursor, cls.user_id, cls.org_id)

        cls.accounts = {}
        for code, name, kind in (("2100", "Trade Payables", "Liability"),
                                 ("1250", "Input Sales Tax Recoverable", "Asset"),
                                 ("1500", "Plant & Machinery", "Asset"),
                                 ("1520", "Office Equipment", "Asset"),
                                 ("5000", "Office Expense", "Expense")):
            cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type, is_active) "
                           "VALUES (%s, %s, %s, %s, TRUE) RETURNING account_id",
                           (cls.org_id, code, name, kind))
            cls.accounts[code] = cursor.fetchone()[0]

        cursor.execute("INSERT INTO sales_tax_rates (organization_id, label, rate_percent, is_active) "
                       "VALUES (%s, 'Sales Tax 18%%', 18, TRUE) RETURNING sales_tax_rate_id", (cls.org_id,))
        cls.tax_18 = cursor.fetchone()[0]

        # A rate in a company that is not his, to be refused.
        cursor.execute("INSERT INTO organizations (name, currency) VALUES (%s, 'PKR') "
                       "RETURNING organization_id", (f"TEST_OTHER_{uuid.uuid4().hex[:8]}",))
        cls.other_org = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sales_tax_rates (organization_id, label, rate_percent, is_active) "
                       "VALUES (%s, 'Their Tax 5%%', 5, TRUE) RETURNING sales_tax_rate_id", (cls.other_org,))
        cls.foreign_tax = cursor.fetchone()[0]

        cursor.execute("INSERT INTO chart_of_accounts (organization_id, code, name, account_type) "
                       "VALUES (%s, '5000', 'Their Expense', 'Expense') RETURNING account_id",
                       (cls.other_org,))
        cls.their_account = cursor.fetchone()[0]

        cursor.execute("INSERT INTO vendors (organization_id, name) VALUES (%s, 'Line Suppliers') "
                       "RETURNING vendor_id", (cls.org_id,))
        cls.vendor_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for statement in (
            "DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
            "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)",
            "DELETE FROM vendor_bills WHERE organization_id = %s",
            "DELETE FROM journal_lines WHERE journal_header_id IN "
            "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
            "DELETE FROM journal_headers WHERE organization_id = %s",
            "DELETE FROM vendors WHERE organization_id = %s",
            "DELETE FROM chart_of_accounts WHERE organization_id = ANY(%s)",
            "DELETE FROM sales_tax_rates WHERE organization_id = ANY(%s)",
            "DELETE FROM organization_memberships WHERE organization_id = ANY(%s)",
            "DELETE FROM organizations WHERE organization_id = ANY(%s)",
            "DELETE FROM users WHERE user_id = %s",
        ):
            params = (cls.user_id,) if statement.endswith("user_id = %s") else \
                     ([cls.org_id, cls.other_org],) if "ANY(%s)" in statement else (cls.org_id,)
            cursor.execute(statement, params)
        conn.commit()
        cursor.close()
        conn.close()
        cls.ctx.pop()

    def setUp(self):
        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        for statement in (
            "DELETE FROM vendor_bill_lines WHERE vendor_bill_id IN "
            "(SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)",
            "DELETE FROM vendor_bills WHERE organization_id = %s",
            "DELETE FROM journal_lines WHERE journal_header_id IN "
            "(SELECT journal_header_id FROM journal_headers WHERE organization_id = %s)",
            "DELETE FROM journal_headers WHERE organization_id = %s",
        ):
            cursor.execute(statement, (self.org_id,))
        conn.commit()
        cursor.close()
        conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["organization_id"] = self.org_id
            flask_session["user_id"] = self.user_id
            flask_session["user_name"] = "Lines Tester"
            flask_session["csrf_token"] = "test-csrf"

    # -- the form ---------------------------------------------------------

    def bill_form(self, **changes):
        form = {
            "csrf_token": "test-csrf",
            "vendor_id": self.vendor_id,
            "bill_date": "2026-11-05",
            "label[]": ["Cartons"],
            "account_id[]": [str(self.accounts["5000"])],
            "quantity[]": ["10"],
            "rate[]": ["1000.00"],
            "total[]": ["10000.00"],
            "product_id[]": [""],
            "line_kind[]": ["Goods"],
            "unit[]": ["Bag"],
            "discount_percent[]": ["0"],
            "tax_rate_id[]": [""],
        }
        form.update(changes)
        return form

    def save(self, **changes):
        return self.client.post("/vendors/bills/new", data=self.bill_form(**changes),
                                follow_redirects=False)

    def save_import(self, **changes):
        return self.client.post("/vendors/bills/new-import", data=self.bill_form(**changes),
                                follow_redirects=False)

    # -- reading it back --------------------------------------------------

    def db(self):
        from core.db import get_conn
        conn = get_conn()
        return conn, conn.cursor()

    def the_bill(self):
        conn, cursor = self.db()
        cursor.execute("SELECT vendor_bill_id, COALESCE(bill_kind, 'Standard'), amount_due, "
                       "COALESCE(tax_total, 0), journal_header_id FROM vendor_bills "
                       "WHERE organization_id = %s ORDER BY vendor_bill_id DESC LIMIT 1", (self.org_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row

    def the_lines(self):
        conn, cursor = self.db()
        cursor.execute("""
            SELECT label, quantity, rate, COALESCE(unit, 'Units'), COALESCE(discount_pct, 0),
                   COALESCE(discount_amount, 0), COALESCE(tax_amount, 0), total,
                   COALESCE(line_kind, 'Goods'), asset_life_years
            FROM vendor_bill_lines
            WHERE vendor_bill_id IN (SELECT vendor_bill_id FROM vendor_bills WHERE organization_id = %s)
            ORDER BY line_order
        """, (self.org_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    def the_entry(self, header_id=None):
        header_id = header_id or self.the_bill()[4]
        conn, cursor = self.db()
        cursor.execute("""
            SELECT coa.code, jl.label, jl.debit, jl.credit
            FROM journal_lines jl JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
            WHERE jl.journal_header_id = %s ORDER BY jl.line_order, jl.journal_line_id
        """, (header_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    def debit_of(self, code, header_id=None):
        return sum((row[2] for row in self.the_entry(header_id) if row[0] == code), 0)

    # -- the two papers ---------------------------------------------------

    def test_a_standard_bill_carries_no_letter_of_credit(self):
        """The LC belongs on the import paper, not in the middle of an expense."""
        page = self.client.get("/vendors/bills/new").get_data(as_text=True)
        self.assertNotIn("LC Reference", page)
        self.assertNotIn("Recipient Bank", page)
        self.assertIn("Import against an LC", page)          # the way across
        for column in ("Unit", "Disc %", "Tax", "Life (yrs)"):
            self.assertIn(column, page)
        self.assertIn("Asset", page)                          # a kind a line can be

    def test_the_import_screen_is_its_own_paper(self):
        page = self.client.get("/vendors/bills/new-import").get_data(as_text=True)
        self.assertIn("New Import Bill (LC)", page)
        self.assertIn("LC Reference", page)
        self.assertIn("Recipient Bank", page)
        self.assertIn("Spread LC charges", page)
        self.assertIn("Landed Cost", page)
        self.assertIn("Sales Tax 18%", page)                  # the company's own rates

    # -- the money on a line ----------------------------------------------

    def test_a_discount_reduces_the_line_and_the_posted_entry(self):
        self.save(**{"discount_percent[]": ["10"]})
        bill_id, kind, amount_due, tax_total, header = self.the_bill()
        _, _, _, unit, pct, discount_amount, tax_amount, total, _, _ = self.the_lines()[0]
        self.assertEqual(unit, "Bag")
        self.assertEqual(pct, 10)
        self.assertEqual(discount_amount, 1000)      # 10 x 1000 less 10%
        self.assertEqual(tax_amount, 0)
        self.assertEqual(total, 9000)
        self.assertEqual(amount_due, 9000)
        self.assertEqual(self.debit_of("5000", header), 9000)
        self.assertEqual(self.debit_of("1250", header), 0)

    def test_sales_tax_is_recovered_and_the_debt_grows_by_it(self):
        self.save(**{"tax_rate_id[]": [str(self.tax_18)]})
        bill_id, kind, amount_due, tax_total, header = self.the_bill()
        self.assertEqual(tax_total, 1800)
        self.assertEqual(amount_due, 11800)
        self.assertEqual(self.debit_of("5000", header), 10000)     # the expense is untaxed
        self.assertEqual(self.debit_of("1250", header), 1800)      # the tax is its own asset
        credits = sum((row[3] for row in self.the_entry(header) if row[0] == "2100"), 0)
        self.assertEqual(credits, 11800)                           # and we owe all of it

    def test_a_discount_and_a_tax_work_together_in_the_right_order(self):
        """10 x 1,000 less 10% = 9,000; 18% of 9,000 is 1,620 - not 1,800."""
        self.save(**{"discount_percent[]": ["10"], "tax_rate_id[]": [str(self.tax_18)]})
        _, _, amount_due, tax_total, header = self.the_bill()
        self.assertEqual(tax_total, 1620)
        self.assertEqual(amount_due, 10620)
        self.assertEqual(self.debit_of("1250", header), 1620)

    def test_two_lines_at_the_same_rate_share_one_tax_line(self):
        self.save(**{
            "label[]": ["Cartons", "Tape"],
            "account_id[]": [str(self.accounts["5000"]), str(self.accounts["5000"])],
            "quantity[]": ["10", "2"],
            "rate[]": ["1000.00", "500.00"],
            "total[]": ["10000.00", "1000.00"],
            "product_id[]": ["", ""],
            "line_kind[]": ["Goods", "Goods"],
            "unit[]": ["Bag", "Units"],
            "discount_percent[]": ["0", "0"],
            "tax_rate_id[]": [str(self.tax_18), str(self.tax_18)],
        })
        _, _, amount_due, tax_total, header = self.the_bill()
        tax_lines = [row for row in self.the_entry(header) if row[0] == "1250"]
        self.assertEqual(len(tax_lines), 1)                 # one rate, one line
        self.assertEqual(tax_lines[0][2], 1980)             # 18% of 11,000
        self.assertEqual(tax_total, 1980)
        self.assertEqual(amount_due, 12980)

    def test_another_company_s_tax_rate_is_refused(self):
        response = self.save(**{"tax_rate_id[]": [str(self.foreign_tax)]})
        self.assertEqual(response.status_code, 200)                 # the form again
        self.assertIn("not one of this company", response.get_data(as_text=True))
        conn, cursor = self.db()
        cursor.execute("SELECT COUNT(*) FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)                   # nothing was written
        cursor.close()
        conn.close()

    def test_the_unit_is_kept_and_shown_on_the_bill(self):
        self.save(**{"unit[]": ["Kg"]})
        bill_id, _, _, _, _ = self.the_bill()
        self.assertEqual(self.the_lines()[0][3], "Kg")
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("<td>Kg</td>", page)

    def test_an_account_from_another_company_is_refused(self):
        """The browser sends an account id; the server decides whose it is."""
        response = self.save(**{"account_id[]": [str(self.their_account)]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("not one of this company", response.get_data(as_text=True))
        conn, cursor = self.db()
        cursor.execute("SELECT COUNT(*) FROM vendor_bills WHERE organization_id = %s", (self.org_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.close()
        conn.close()

    def test_a_life_belongs_only_to_an_asset_line(self):
        """A short list in the form must not hang a van's five years on a carton."""
        self.save(**{
            "label[]": ["Cartons", "Tape"],
            "account_id[]": [str(self.accounts["5000"]), str(self.accounts["5000"])],
            "quantity[]": ["1", "1"], "rate[]": ["100.00", "50.00"], "total[]": ["100.00", "50.00"],
            "product_id[]": ["", ""], "line_kind[]": ["Goods", "Asset"],
            "unit[]": ["Bag", "Units"], "discount_percent[]": ["0", "0"],
            "tax_rate_id[]": ["", ""], "asset_life[]": ["5"],
        })
        lines = self.the_lines()
        self.assertIsNone(lines[0][9])              # the cartons are not an asset
        self.assertIsNone(lines[1][9])              # and the van got no figure either

    def test_the_printed_bill_shows_the_unit_and_the_tax(self):
        self.save(**{"discount_percent[]": ["10"], "tax_rate_id[]": [str(self.tax_18)]})
        bill_id, _, _, _, _ = self.the_bill()
        page = self.client.get(f"/vendors/bills/{bill_id}/print").get_data(as_text=True)
        self.assertIn("Bag", page)                  # the unit column
        self.assertIn("Sales Tax:", page)           # the tax is on the paper too
        self.assertIn("1,620.00", page)
        self.assertIn("10,620.00", page)

    # -- buying an asset ---------------------------------------------------

    def test_an_asset_line_is_capitalised_not_expensed(self):
        self.save(**{"account_id[]": [str(self.accounts["1520"])], "line_kind[]": ["Asset"],
                     "label[]": ["Delivery van"], "unit[]": ["Units"], "rate[]": ["2500000.00"],
                     "quantity[]": ["1"], "total[]": ["2500000.00"], "asset_life[]": ["5"]})
        bill_id, _, amount_due, _, header = self.the_bill()
        self.assertEqual(self.debit_of("1520", header), 2500000)
        self.assertEqual(self.debit_of("5000", header), 0)          # never an expense
        self.assertEqual(amount_due, 2500000)
        self.assertEqual(self.the_lines()[0][8], "Asset")
        self.assertEqual(float(self.the_lines()[0][9]), 5.0)                # a life is kept
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("Asset", page)
        self.assertIn("5.0 years of use", page)

    # -- the import paper --------------------------------------------------

    def test_an_import_bill_is_marked_as_one(self):
        self.save_import(**{
            "lc_reference": "LC 44/2026",
            "recipient_bank": "Meezan Bank",
            "label[]": ["Urea 50kg", "Freight and clearing"],
            "account_id[]": [str(self.accounts["5000"]), str(self.accounts["5000"])],
            "quantity[]": ["10", "1"],
            "rate[]": ["2500.00", "20000.00"],
            "total[]": ["25000.00", "20000.00"],
            "product_id[]": ["", ""],
            "line_kind[]": ["Goods", "Landed Cost"],
            "unit[]": ["Bag", "Units"],
            "discount_percent[]": ["0", "0"],
            "tax_rate_id[]": ["", str(self.tax_18)],
        })
        bill_id, kind, amount_due, tax_total, header = self.the_bill()
        self.assertEqual(kind, "Import")
        self.assertEqual(tax_total, 3600)                    # 18% of the 20,000 charge
        self.assertEqual(amount_due, 48600)
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("Import / LC", page)
        self.assertIn("Import against an LC", page)          # Other Info says what it is
        self.assertIn("LC 44/2026", page)
        self.assertIn("Landed cost", page)
        self.assertIn("What these goods really cost", page)

    def test_a_standard_bill_says_so(self):
        self.save()
        bill_id, kind, _, _, _ = self.the_bill()
        self.assertEqual(kind, "Standard")
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertIn("Standard purchase", page)
        self.assertNotIn("Import / LC", page)
        self.assertNotIn("What these goods really cost", page)

    def test_a_draft_import_bill_is_corrected_on_the_import_screen(self):
        self.save_import(**{"lc_reference": "LC 9/2026"})
        bill_id, _, _, _, _ = self.the_bill()
        page = self.client.get(f"/vendors/bills/{bill_id}/edit").get_data(as_text=True)
        self.assertIn("Correct import bill", page)
        self.assertIn("LC Reference", page)
        self.assertIn("LC 9/2026", page)

    def test_a_draft_standard_bill_is_corrected_on_the_standard_screen(self):
        self.save()
        bill_id, _, _, _, _ = self.the_bill()
        page = self.client.get(f"/vendors/bills/{bill_id}/edit").get_data(as_text=True)
        self.assertIn("Correct bill", page)
        self.assertNotIn("LC Reference", page)

    def test_a_correction_rewrites_the_tax_as_well_as_the_line(self):
        self.save()
        bill_id, _, _, _, header = self.the_bill()
        self.assertEqual(self.debit_of("1250", header), 0)
        self.client.post(f"/vendors/bills/{bill_id}/edit", data=self.bill_form(
            **{"tax_rate_id[]": [str(self.tax_18)]}), follow_redirects=True)
        _, _, amount_due, tax_total, header = self.the_bill()
        self.assertEqual(tax_total, 1800)
        self.assertEqual(amount_due, 11800)
        self.assertEqual(self.debit_of("1250", header), 1800)
        self.assertEqual(self.debit_of("5000", header), 10000)

    def test_the_list_can_show_only_the_imports(self):
        self.save()
        self.save_import(**{"lc_reference": "LC 77/2026"})
        imports = self.client.get("/vendors/bills?kind=Import").get_data(as_text=True)
        standard = self.client.get("/vendors/bills?kind=Standard").get_data(as_text=True)
        self.assertEqual(imports.count(">Import</span>"), 1)        # the pill on the row
        self.assertEqual(imports.count(">Standard</span>"), 0)
        self.assertEqual(standard.count(">Import</span>"), 0)
        self.assertEqual(standard.count(">Standard</span>"), 1)

    # -- the page's shape --------------------------------------------------

    def test_the_bill_page_stacks_its_tabs_under_the_bill(self):
        """The bug he saw: the strip and the panels shared one flex row, so the
        lines were squeezed beside the journal instead of under the bill."""
        self.save()
        bill_id, _, _, _, _ = self.the_bill()
        page = self.client.get(f"/vendors/bills/{bill_id}").get_data(as_text=True)
        self.assertEqual(page.count('class="lp-tab-strip"'), 1)
        self.assertEqual(page.count('class="lp-tab-panel"'), 3)
        self.assertLess(page.index('class="lp-tab-strip"'), page.index('class="lp-tab-panel"'))
        for tab in ("Invoice Lines", "Journal Items", "Other Info"):
            self.assertIn(tab, page)

        import os
        css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "static", "ui.css")
        with open(css_path, encoding="utf-8") as handle:
            css = handle.read()
        rule = re.search(r"\.lp-tabs\[data-lp-tabs\]\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "the rule that stacks a document's tabs is missing")
        self.assertIn("display: block", rule.group(1))


if __name__ == "__main__":
    unittest.main()
