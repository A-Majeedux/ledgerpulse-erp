"""
test_workflows.py

END-TO-END checks. Unlike test_money.py / test_auth.py, nothing here is
faked: this starts the real app on a real PostgreSQL database and uses it
the way a browser does - it opens each page, reads the HTML form the page
actually contains, fills it in and submits it - then looks at what landed
in the ledger.

That matters because the worst bugs so far lived BETWEEN the pieces: a form
template without its security token, a table created in the wrong order, a
new company with no Chart of Accounts. Tests that fake the database, or that
post straight to a route, cannot see any of those.

HOW TO RUN
    TEST_DATABASE_URL="postgresql://user:password@host/anydb" python3 test_workflows.py

IS IT SAFE FOR MY REAL DATA?  Yes, by construction:
  - It never uses DATABASE_URL. Without TEST_DATABASE_URL it does nothing.
  - On the server TEST_DATABASE_URL points to, it creates a BRAND-NEW empty
    database called lp_test_<random>, works only inside that, and deletes it
    at the end. It never reads or writes any database that already existed.
  - Before the first test it asks the app "which database are you connected
    to?" and stops unless the answer is the database it just created.
  Set KEEP_TEST_DATABASE=1 to keep the scratch database for inspection.

WHAT IT COVERS
   1. Every POST form in every template carries the CSRF token
   2. First start on an empty database, and a second start on the same one
   3. First-time setup, and the company name typed there
   4. Each transaction screen posts the correct, balanced journal entry
   5. Posting rules (posting twice; a bill's journal is posted from the bill)
   6. Wrong input is refused with a message - nothing saved, no crash page
   7. A second company: starter data, membership, switching, logo uploads
   8. One company can never read or write another company's records
   9. The dashboard shows exactly what is in the ledger - posted entries
      only, the right dates, the right company, no invented percentages
  10. Account kinds: payments and receipts offer bank and cash accounts
      only; a journal entry has a General account column and a Partner
      account column; an older database is upgraded without losing anything
  11. The customer card: every customer gets an ID, the card's boxes are
      saved and can be edited, wrong input is refused without losing what
      was typed, a name with entries is locked, the list and its search,
      and an older database is upgraded without losing anything
  12. The ledger as a whole still obeys its rules at the end
"""
import json
import os
import re
import secrets
import subprocess
import sys
from decimal import Decimal as D
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

if not TEST_DATABASE_URL:
    print("SKIPPED - nothing was tested.")
    print("These checks need a PostgreSQL server where they may create (and then")
    print("delete) their own empty scratch database. Run them like this:")
    print('    TEST_DATABASE_URL="postgresql://user:password@host/anydb" python3 test_workflows.py')
    sys.exit(0)

import psycopg2  # noqa: E402  (imported after the skip message on purpose)


# ---------------------------------------------------------------------
# 0. A scratch database of our own
# ---------------------------------------------------------------------
SCRATCH_NAME = "lp_test_" + secrets.token_hex(6)


def _url_for_database(base_url, database_name):
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database_name, parts.query, parts.fragment))


def _admin(sql):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    try:
        conn.cursor().execute(sql)
    finally:
        conn.close()


_admin(f'CREATE DATABASE "{SCRATCH_NAME}"')   # fails loudly if it somehow exists
SCRATCH_URL = _url_for_database(TEST_DATABASE_URL, SCRATCH_NAME)

# Point the app at the scratch database BEFORE it is imported. core/db.py
# loads .env but never overrides a variable that is already set.
os.environ["DATABASE_URL"] = SCRATCH_URL
os.environ.pop("TESTING", None)            # we WANT the real table creation
os.environ["FLASK_ENV"] = "development"
os.environ.setdefault("SECRET_KEY", secrets.token_hex(16))
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))


def _drop_scratch_database():
    if os.environ.get("KEEP_TEST_DATABASE") == "1":
        print(f"\n(kept scratch database {SCRATCH_NAME} for inspection)")
        return
    try:
        _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_NAME}" WITH (FORCE)')
    except Exception as error:  # an old server without WITH (FORCE)
        print(f"\n(could not drop scratch database {SCRATCH_NAME}: {error})")


# ---------------------------------------------------------------------
# Tiny reporting helpers
# ---------------------------------------------------------------------
FAILURES = []
PASSED = 0


def check(description, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ✓ {description}")
    else:
        FAILURES.append(description)
        print(f"  ✗ {description}" + (f"\n        -> {detail}" if detail != "" else ""))
    return bool(condition)


def section(title):
    print(f"\n{title}")


def q(sql, args=()):
    """Run SQL directly on the scratch database (to look at what the app wrote)."""
    conn = psycopg2.connect(SCRATCH_URL)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, args)
        rows = cursor.fetchall() if cursor.description else None
        conn.commit()
        return rows
    finally:
        conn.close()


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0][0] if rows else None


# ---------------------------------------------------------------------
# A very small "browser": reads the real <form> a page rendered
# ---------------------------------------------------------------------
class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self._form, self._select, self._textarea = [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"action": a.get("action"), "method": (a.get("method") or "GET").upper(),
                          "fields": [], "names": set()}
            self.forms.append(self._form)
        elif self._form is None:
            return
        elif tag == "input" and a.get("name"):
            kind = (a.get("type") or "text").lower()
            self._form["names"].add(a["name"])
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self._form["fields"].append((a["name"], a.get("value", "on")))
            elif kind not in ("submit", "button", "image", "file"):
                self._form["fields"].append((a["name"], a.get("value", "")))
        elif tag == "select":
            self._select = {"name": a.get("name"), "options": [], "selected": None}
        elif tag == "option" and self._select is not None:
            self._select["options"].append(a.get("value"))
            if "selected" in a:
                self._select["selected"] = a.get("value")
        elif tag == "textarea":
            self._textarea = {"name": a.get("name"), "text": ""}

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea["text"] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select" and self._select is not None:
            s, self._select = self._select, None
            if self._form is not None and s["name"]:
                value = s["selected"] if s["selected"] is not None else (s["options"][0] if s["options"] else "")
                self._form["fields"].append((s["name"], value or ""))
                self._form["names"].add(s["name"])
        elif tag == "textarea" and self._textarea is not None:
            t, self._textarea = self._textarea, None
            if self._form is not None and t["name"]:
                self._form["fields"].append((t["name"], t["text"]))
                self._form["names"].add(t["name"])


def forms_in(html):
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


class Browser:
    def __init__(self, flask_app):
        self.client = flask_app.test_client()

    def get(self, path):
        return self.client.get(path)

    def token(self):
        """The CSRF token of the current session (read from any rendered form)."""
        for form in forms_in(self.get("/organizations/new").get_data(as_text=True)):
            for name, value in form["fields"]:
                if name == "csrf_token":
                    return value
        raise AssertionError("no CSRF token found on /organizations/new")

    def submit(self, page, data=None, rows=None, has=None, action_contains=None, action=None):
        """
        Open `page`, take one of its forms EXACTLY as rendered (hidden fields,
        defaults, the CSRF token if - and only if - the template has it),
        overlay what the user types, and submit it.
          has             : pick the form that contains this field name
          action_contains : pick the form whose action contains this text
          rows            : extra (name, value) pairs for the line grids that
                            the page's JavaScript builds (account_id[], ...)
          action          : where to post, for the one form whose action is
                            set by JavaScript (the organization switcher)
        """
        from werkzeug.datastructures import MultiDict
        response = self.get(page)
        assert response.status_code == 200, f"GET {page} -> {response.status_code}"
        forms = forms_in(response.get_data(as_text=True))
        if action_contains is not None:
            forms = [f for f in forms if f["action"] and action_contains in f["action"]]
        elif has is not None:
            forms = [f for f in forms if has in f["names"]]
        else:  # the page's own form, not the organization switcher in the top bar
            forms = [f for f in forms if "org_id" not in f["names"]]
        assert forms, f"no matching <form> on {page}"
        form = forms[0]

        data = dict(data or {})
        fields, used = [], set()
        for name, value in form["fields"]:
            if name in data:
                if name not in used and data[name] is not None:
                    fields.append((name, data[name]))
                used.add(name)
            else:
                fields.append((name, value))
        fields += [(n, v) for n, v in data.items() if n not in used and v is not None]
        fields += list(rows or [])
        return self.client.post(action or form["action"] or page, data=MultiDict(fields))

    def post_raw(self, path, fields):
        """Post fields straight to a route WITH a valid token - what a stale
        tab or a script does, skipping every check the browser would make."""
        from werkzeug.datastructures import MultiDict
        return self.client.post(path, data=MultiDict([("csrf_token", self.token())] + list(fields)))


def error_box(response):
    """Text of the red error box a form re-renders with (None if there is none)."""
    match = re.search(r"var\(--lp-coral-bg\)[^>]*>(.*?)</div>", response.get_data(as_text=True), re.S)
    if not match:
        return None
    import html
    return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())


WATCHED_TABLES = [
    "organizations", "organization_memberships", "chart_of_accounts", "tax_rates", "sales_tax_rates",
    "journal_headers", "journal_lines", "vendors", "vendor_bills", "vendor_bill_lines", "vendor_payments",
    "customers", "customer_receipts", "products", "sales_invoices", "sales_invoice_lines",
]


def row_counts():
    return {table: one(f"SELECT count(*) FROM {table}") for table in WATCHED_TABLES}


def account(code, organization_id):
    return str(one("SELECT account_id FROM chart_of_accounts WHERE organization_id = %s AND code = %s",
                   (organization_id, code)))


def journal(journal_header_id):
    """[(account code, debit, credit), ...] plus the entry's status."""
    lines = q("""
        SELECT c.code, l.debit, l.credit FROM journal_lines l
        JOIN chart_of_accounts c ON c.account_id = l.account_id
        WHERE l.journal_header_id = %s ORDER BY l.line_order, l.journal_line_id
    """, (journal_header_id,))
    status = one("SELECT status FROM journal_headers WHERE journal_header_id = %s", (journal_header_id,))
    return lines, status


def check_journal(description, journal_header_id, expected_lines, expected_status):
    if journal_header_id is None:
        return check(description, False, "no journal entry was created")
    lines, status = journal(journal_header_id)
    got = sorted((code, D(str(debit)), D(str(credit))) for code, debit, credit in lines)
    want = sorted((code, D(debit), D(credit)) for code, debit, credit in expected_lines)
    shown = "  ".join(f"{'Dr' if debit else 'Cr'} {code} {debit or credit:,.2f}" for code, debit, credit in lines)
    return check(f"{description}:  {shown}  [{status}]", got == want and status == expected_status,
                 f"expected {expected_lines} [{expected_status}]")


# ---------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------
def test_1_every_post_form_has_the_csrf_token():
    section("1. Every POST form in every template carries the CSRF token")
    opening = re.compile(r"<form\b[^>]*>", re.I | re.S)
    missing, total = [], 0
    for template in sorted((REPO_ROOT / "templates").rglob("*.html")):
        text = template.read_text(encoding="utf-8")
        for match in opening.finditer(text):
            if not re.search(r'method\s*=\s*["\']?post', match.group(0), re.I):
                continue
            total += 1
            end = text.find("</form>", match.end())
            if "csrf_field()" not in text[match.end(): end if end != -1 else len(text)]:
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{template.relative_to(REPO_ROOT)}:{line}")
    check(f"all {total} POST forms contain {{{{ csrf_field() }}}}", not missing and total > 0,
          "missing in: " + ", ".join(missing))


def test_2_first_start(lp):
    section("2. First start on an empty database, and a second start on the same one")
    check("the app is connected to the scratch database (and nothing else)",
          one("SELECT current_database()") == SCRATCH_NAME)
    check("all tables were created on a brand-new database",
          one("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'") >= 25)
    before = (one("SELECT count(*) FROM organizations"), one("SELECT count(*) FROM chart_of_accounts"),
              one("SELECT count(*) FROM tax_rates"), one("SELECT count(*) FROM sales_tax_rates"),
              one("SELECT count(*) FROM roles"), one("SELECT count(*) FROM permissions"))
    check("the first company has its starter data (14 accounts, 8 WHT rates, 5 sales-tax rates)",
          before[1:4] == (14, 8, 5), f"got {before[1:4]}")
    restart = subprocess.run([sys.executable, "-c", "import app"], cwd=REPO_ROOT, capture_output=True, text=True,
                             env={**os.environ, "DATABASE_URL": SCRATCH_URL})
    check("starting the app a second time works", restart.returncode == 0, restart.stderr[-400:])
    after = (one("SELECT count(*) FROM organizations"), one("SELECT count(*) FROM chart_of_accounts"),
             one("SELECT count(*) FROM tax_rates"), one("SELECT count(*) FROM sales_tax_rates"),
             one("SELECT count(*) FROM roles"), one("SELECT count(*) FROM permissions"))
    check("...and creates nothing twice", before == after, f"before {before}, after {after}")


def test_3_setup(browser):
    section("3. First-time setup")
    response = browser.get("/")
    check("with no users yet, the app sends you to the setup screen",
          response.status_code == 302 and response.headers["Location"].endswith("/auth/setup"))
    response = browser.submit("/auth/setup", {
        "name": "Abdul Majeed", "email": "abdul@example.com", "password": "Passw0rd!xyz",
        "organization_name": "Hextar Test Co"}, has="email")
    check("the setup form submits", response.status_code == 302, f"status {response.status_code}")
    check("the company is named what was typed, not 'My Organization'",
          one("SELECT name FROM organizations WHERE organization_id = 1") == "Hextar Test Co",
          one("SELECT name FROM organizations WHERE organization_id = 1"))
    check("the first administrator is a member of that company",
          one("SELECT count(*) FROM organization_memberships WHERE organization_id = 1") == 1)
    check("the dashboard opens", browser.get("/").status_code == 200)
    response = browser.client.post("/vendors/new", data={"name": "No Token Ltd"})
    check("a form sent WITHOUT the security token is refused (400)", response.status_code == 400)
    response = browser.get("/auth/setup")
    check("setup cannot be run a second time", response.status_code == 302)


def test_4_workflows(browser, ids):
    section("4. Each transaction screen posts the correct journal entry")
    org = 1
    # -- manual journal entry ------------------------------------------------
    response = browser.submit("/accounting/journal-entries/new", {"reference": "Office rent", "accounting_date": "2026-11-13"}, rows=[
        ("account_id[]", account("6000", org)), ("partner_name[]", ""), ("label[]", "Rent"), ("debit[]", "500"), ("credit[]", "0"),
        ("account_id[]", account("1000", org)), ("partner_name[]", ""), ("label[]", "Rent"), ("debit[]", "0"), ("credit[]", "500")])
    check("journal entry: the form saves a draft", response.status_code == 302, error_box(response) or response.status_code)
    ids["je"] = one("SELECT max(journal_header_id) FROM journal_headers")
    check_journal("journal entry", ids["je"], [("6000", "500", "0"), ("1000", "0", "500")], "Draft")
    response = browser.submit(f"/accounting/journal-entries/{ids['je']}", action_contains="/post")
    check("journal entry: 'Post' moves it to Posted",
          response.status_code == 302 and journal(ids["je"])[1] == "Posted")

    # -- vendor bill ---------------------------------------------------------
    browser.submit("/vendors/new", {"name": "Pak Consulting", "nature_of_purchase": "Services"})
    ids["vendor"] = str(one("SELECT vendor_id FROM vendors WHERE name = 'Pak Consulting'"))
    response = browser.submit("/vendors/bills/new", {"vendor_id": ids["vendor"], "bill_date": "2026-11-13", "due_date": "2026-12-13"},
                              has="vendor_id", rows=[
        ("label[]", "Consulting"), ("account_id[]", account("6000", org)), ("quantity[]", "2"), ("rate[]", "25000"), ("total[]", "50000.00"),
        ("label[]", "Travel"), ("account_id[]", account("6000", org)), ("quantity[]", "1"), ("rate[]", "10000"), ("total[]", "10000.00")])
    check("vendor bill: the form saves a draft", response.status_code == 302, error_box(response) or response.status_code)
    row = q("SELECT vendor_bill_id, journal_header_id FROM vendor_bills ORDER BY 1 DESC LIMIT 1")
    ids["bill"], ids["bill_je"] = row[0] if row else (None, None)
    expected = [("6000", "50000", "0"), ("6000", "10000", "0"), ("2000", "0", "60000")]
    check_journal("vendor bill (draft)", ids["bill_je"], expected, "Draft")
    browser.submit(f"/vendors/bills/{ids['bill']}", action_contains="/post")
    check_journal("vendor bill (posted)", ids["bill_je"], expected, "Posted")
    check("vendor bill: the bill itself says Posted too",
          one("SELECT status FROM vendor_bills WHERE vendor_bill_id = %s", (ids["bill"],)) == "Posted")

    # -- vendor payment with WHT ---------------------------------------------
    response = browser.submit("/vendors/payments/new", {
        "vendor_id": ids["vendor"], "amount": "60000", "payment_date": "2026-11-13", "has_wht": "on", "wht_rate": "10.00",
        "paid_from_account_id": account("1010", org), "payment_method": "Manual"}, has="vendor_id")
    check("vendor payment: the form saves", response.status_code == 302, error_box(response) or response.status_code)
    row = q("SELECT vendor_payment_id, journal_header_id FROM vendor_payments ORDER BY 1 DESC LIMIT 1")
    ids["payment"], payment_je = row[0] if row else (None, None)
    check_journal("vendor payment, 10% WHT, Services vendor", payment_je,
                  [("2000", "60000", "0"), ("2110", "0", "6000"), ("1010", "0", "54000")], "Posted")

    # -- cheque payment ------------------------------------------------------
    cheque = {"vendor_id": ids["vendor"], "amount": "10000", "payment_date": "2026-11-13", "paid_from_account_id": account("1010", org),
              "payment_method": "Cheque", "cheque_number": "000123", "cheque_date": "2026-11-13"}
    response = browser.submit("/vendors/payments/new", cheque, has="vendor_id")
    check("cheque payment: saves with its cheque number",
          response.status_code == 302 and one("SELECT count(*) FROM vendor_payments WHERE cheque_number = '000123'") == 1)
    before = row_counts()
    response = browser.submit("/vendors/payments/new", cheque, has="vendor_id")
    check("cheque payment: the same cheque number on the same bank is refused with a message",
          response.status_code == 200 and "already been used" in (error_box(response) or "") and row_counts() == before,
          error_box(response))
    response = browser.submit("/vendors/payments/new", {
        "vendor_id": ids["vendor"], "amount": "100", "payment_date": "2026-11-13", "paid_from_account_id": account("1010", org),
        "payment_method": "Manual", "cheque_number": "LEFTOVER-9"}, has="vendor_id")
    check("a non-cheque payment does not keep a cheque number left in the hidden box",
          response.status_code == 302 and one("SELECT count(*) FROM vendor_payments WHERE cheque_number = 'LEFTOVER-9'") == 0)

    # -- customer receipt with WHT -------------------------------------------
    browser.submit("/customers/new", {"name": "Acme Traders", "nature_of_supply": "Supplies"})
    ids["customer"] = str(one("SELECT customer_id FROM customers WHERE name = 'Acme Traders'"))
    response = browser.submit("/customers/receipts/new", {
        "customer_id": ids["customer"], "amount": "118000", "receipt_date": "2026-11-13", "has_wht": "on", "wht_rate": "4.00",
        "deposit_to_account_id": account("1010", org)}, has="customer_id")
    check("customer receipt: the form saves", response.status_code == 302, error_box(response) or response.status_code)
    row = q("SELECT customer_receipt_id, journal_header_id FROM customer_receipts ORDER BY 1 DESC LIMIT 1")
    ids["receipt"], receipt_je = row[0] if row else (None, None)
    check_journal("customer receipt, 4% WHT, Supplies customer", receipt_je,
                  [("1010", "113280", "0"), ("1150", "4720", "0"), ("1100", "0", "118000")], "Posted")

    # -- sales invoice -------------------------------------------------------
    browser.submit("/sales/products/new", {"name": "Widget", "unit": "Units", "default_account_id": account("4000", org)})
    ids["product"] = str(one("SELECT product_id FROM products WHERE name = 'Widget'"))
    ids["sales_tax"] = str(one("SELECT sales_tax_rate_id FROM sales_tax_rates WHERE organization_id = 1 AND rate_percent = 18"))
    response = browser.submit("/sales/invoices/new", {"customer_id": ids["customer"], "invoice_date": "2026-11-13"}, has="customer_id", rows=[
        ("product_id[]", ids["product"]), ("label[]", "Widgets"), ("account_id[]", account("4000", org)), ("unit[]", "Units"),
        ("quantity[]", "10"), ("rate[]", "1000"), ("discount_percent[]", "10"), ("sales_tax_rate_id[]", ids["sales_tax"])])
    check("sales invoice: the form saves a draft", response.status_code == 302, error_box(response) or response.status_code)
    row = q("SELECT sales_invoice_id, journal_header_id FROM sales_invoices ORDER BY 1 DESC LIMIT 1")
    ids["invoice"], ids["invoice_je"] = row[0] if row else (None, None)
    expected = [("1100", "10620", "0"), ("4000", "0", "9000"), ("2200", "0", "1620")]
    check_journal("sales invoice 10 x 1,000 less 10%, plus 18% tax (draft)", ids["invoice_je"], expected, "Draft")
    browser.submit(f"/sales/invoices/{ids['invoice']}", action_contains="/post")
    check_journal("sales invoice (posted)", ids["invoice_je"], expected, "Posted")

    # -- every list / detail page opens --------------------------------------
    pages = ["/", "/accounting/chart-of-accounts", "/accounting/journal-entries", f"/accounting/journal-entries/{ids['je']}",
             "/vendors/", "/vendors/bills", f"/vendors/bills/{ids['bill']}", "/vendors/payments", f"/vendors/payments/{ids['payment']}",
             "/customers/", "/customers/receipts", f"/customers/receipts/{ids['receipt']}", "/sales/products", "/sales/tax-rates",
             "/sales/invoices", f"/sales/invoices/{ids['invoice']}", "/tax-rates/", "/organizations/manage", "/organizations/pick",
             "/organizations/1/edit", "/organizations/subscription/renew", "/auth/admin/users", "/auth/admin/payments"]
    bodies = {page: browser.get(page) for page in pages}
    broken = [f"{page} -> {response.status_code}" for page, response in bodies.items() if response.status_code != 200]
    check(f"all {len(pages)} list and detail pages open", not broken, ", ".join(broken))
    # The layout once printed its sidebar key ("vendors", "journals", ...) as
    # loose text at the very top of every page.
    stray = [page for page, response in bodies.items()
             if not re.search(r"<body[^>]*>\s*<", response.get_data(as_text=True))]
    check("no page prints stray text above the layout", not stray, ", ".join(stray))


def test_5_posting_rules(browser, ids):
    section("5. Posting rules")
    org = 1
    stamp = one("SELECT posted_at FROM journal_headers WHERE journal_header_id = %s", (ids["je"],))
    page = browser.get(f"/accounting/journal-entries/{ids['je']}").get_data(as_text=True)
    check("a posted journal entry no longer shows a Post button",
          not any(f["action"] and "/post" in f["action"] for f in forms_in(page)))
    response = browser.post_raw(f"/accounting/journal-entries/{ids['je']}/post", [])
    check("posting an already-posted entry again (double click, stale tab) changes nothing",
          response.status_code == 302
          and one("SELECT posted_at FROM journal_headers WHERE journal_header_id = %s", (ids["je"],)) == stamp)

    # a NEW draft bill: its journal must only be postable from the bill
    browser.submit("/vendors/bills/new", {"vendor_id": ids["vendor"], "bill_date": "2026-11-13"}, has="vendor_id", rows=[
        ("label[]", "Stationery"), ("account_id[]", account("6000", org)), ("quantity[]", "1"), ("rate[]", "100"), ("total[]", "100.00")])
    bill, bill_je = q("SELECT vendor_bill_id, journal_header_id FROM vendor_bills ORDER BY 1 DESC LIMIT 1")[0]
    page = browser.get(f"/accounting/journal-entries/{bill_je}").get_data(as_text=True)
    check("the draft journal behind a bill shows NO Post button on the Journal Entries screen",
          not any(f["action"] and "/post" in f["action"] for f in forms_in(page)))
    check("...and links to the bill it belongs to instead", f'href="/vendors/bills/{bill}"' in page)
    response = browser.post_raw(f"/accounting/journal-entries/{bill_je}/post", [])
    check("posting that journal directly is refused, and bill and journal both stay Draft",
          response.status_code == 409 and journal(bill_je)[1] == "Draft"
          and one("SELECT status FROM vendor_bills WHERE vendor_bill_id = %s", (bill,)) == "Draft",
          f"status {response.status_code}, journal {journal(bill_je)[1]}")
    browser.submit(f"/vendors/bills/{bill}", action_contains="/post")
    check("posting the bill posts both together",
          journal(bill_je)[1] == "Posted" and one("SELECT status FROM vendor_bills WHERE vendor_bill_id = %s", (bill,)) == "Posted")
    stamp = one("SELECT posted_at FROM journal_headers WHERE journal_header_id = %s", (bill_je,))
    browser.post_raw(f"/vendors/bills/{bill}/post", [])
    check("posting the bill a second time keeps the original posting time",
          one("SELECT posted_at FROM journal_headers WHERE journal_header_id = %s", (bill_je,)) == stamp)
    ids["draft_invoice_owner"] = None


def refused(browser, description, path, fields, message_contains=None):
    """Post `fields`; pass only if nothing was saved, nothing crashed, and the user was told."""
    before = row_counts()
    response = browser.post_raw(path, fields)
    after = row_counts()
    saved = {t: after[t] - before[t] for t in WATCHED_TABLES if after[t] != before[t]}
    message = error_box(response) if response.status_code == 200 else None
    if response.status_code in (400, 403, 404, 409):
        match = re.search(r"<p>(.*?)</p>", response.get_data(as_text=True), re.S)
        message = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    told = (response.status_code == 200 and message) or response.status_code in (400, 403, 404, 409)
    ok = response.status_code < 500 and not saved and bool(told)
    if ok and message_contains:
        ok = message_contains.lower() in (message or "").lower()
    shown = f'"{message}"' if message else f"HTTP {response.status_code}"
    return check(f"{description}  ->  {shown}", ok,
                 f"status {response.status_code}, saved {saved or 'nothing'}, message {message!r}")


def test_6_wrong_input_is_refused(browser, ids):
    section("6. Wrong input is refused with a message - nothing saved, no crash page")
    org = 1
    bank, expense, cash, revenue = account("1010", org), account("6000", org), account("1000", org), account("4000", org)
    pay = [("vendor_id", ids["vendor"]), ("payment_date", "2026-11-13"), ("paid_from_account_id", bank), ("payment_method", "Manual")]
    refused(browser, "vendor payment of zero", "/vendors/payments/new", pay + [("amount", "0")], "greater than zero")
    refused(browser, "vendor payment of -500", "/vendors/payments/new", pay + [("amount", "-500")], "negative")
    refused(browser, "vendor payment of 'abc'", "/vendors/payments/new", pay + [("amount", "abc")], "must be a number")
    refused(browser, "vendor payment of 'NaN'", "/vendors/payments/new", pay + [("amount", "NaN")], "must be a number")
    refused(browser, "vendor payment with no date", "/vendors/payments/new",
            [("vendor_id", ids["vendor"]), ("payment_date", ""), ("paid_from_account_id", bank), ("amount", "1000")], "Date is required")
    refused(browser, "WHT ticked but no rate picked", "/vendors/payments/new", pay + [("amount", "1000"), ("has_wht", "on"), ("wht_rate", "")], "pick the WHT rate")
    refused(browser, "WHT rate 99% (not on the Tax Rates list)", "/vendors/payments/new", pay + [("amount", "1000"), ("has_wht", "on"), ("wht_rate", "99")], "not an active rate")
    refused(browser, "WHT rate 150%", "/vendors/payments/new", pay + [("amount", "1000"), ("has_wht", "on"), ("wht_rate", "150")], "between 0 and 100")
    refused(browser, "cheque payment with no cheque number", "/vendors/payments/new",
            [("vendor_id", ids["vendor"]), ("payment_date", "2026-11-13"), ("paid_from_account_id", bank), ("amount", "1000"),
             ("payment_method", "Cheque"), ("cheque_number", "  ")], "cheque number")

    # a rate that has been RETIRED on the Tax Rates screen can no longer be used
    retired = one("SELECT tax_rate_id FROM tax_rates WHERE organization_id = 1 AND rate_percent = 8")
    browser.post_raw(f"/tax-rates/{retired}/toggle", [])
    refused(browser, "WHT rate 8% after it was retired", "/vendors/payments/new", pay + [("amount", "1000"), ("has_wht", "on"), ("wht_rate", "8.00")], "not an active rate")
    browser.post_raw(f"/tax-rates/{retired}/toggle", [])

    receipt = [("customer_id", ids["customer"]), ("receipt_date", "2026-11-13"), ("deposit_to_account_id", bank)]
    refused(browser, "customer receipt of zero", "/customers/receipts/new", receipt + [("amount", "0")], "greater than zero")
    refused(browser, "customer receipt of -500", "/customers/receipts/new", receipt + [("amount", "-500")], "negative")
    refused(browser, "receipt: WHT ticked but no rate picked", "/customers/receipts/new", receipt + [("amount", "1000"), ("has_wht", "on")], "pick the WHT rate")
    refused(browser, "receipt: WHT rate 99% (not on the list)", "/customers/receipts/new", receipt + [("amount", "1000"), ("has_wht", "on"), ("wht_rate", "99")], "not an active rate")

    head = [("reference", "t"), ("accounting_date", "2026-11-13")]
    line = lambda acc, debit, credit: [("account_id[]", acc), ("partner_name[]", ""), ("label[]", ""), ("debit[]", debit), ("credit[]", credit)]
    refused(browser, "journal entry with no lines", "/accounting/journal-entries/new", head, "at least two lines")
    refused(browser, "journal entry with one line only", "/accounting/journal-entries/new", head + line(expense, "100", ""), "at least two lines")
    refused(browser, "journal entry whose lines are all zero", "/accounting/journal-entries/new", head + line(expense, "0", "0") + line(cash, "0", "0"), "debit or a credit amount")
    refused(browser, "journal entry with negative amounts", "/accounting/journal-entries/new", head + line(expense, "-100", "") + line(cash, "", "-100"), "negative")
    refused(browser, "journal line with a debit AND a credit", "/accounting/journal-entries/new", head + line(expense, "100", "100") + line(cash, "50", ""), "not both")
    refused(browser, "journal entry that does not balance", "/accounting/journal-entries/new", head + line(expense, "500", "") + line(cash, "", "400"), "does not balance")
    refused(browser, "journal line with an amount but no account", "/accounting/journal-entries/new", head + line("", "100", "") + line(cash, "", "100"), "select an account")
    refused(browser, "journal entry with amount 'abc'", "/accounting/journal-entries/new", head + line(expense, "abc", "") + line(cash, "", "5"), "must be a number")
    refused(browser, "journal entry with no date", "/accounting/journal-entries/new", [("reference", "t"), ("accounting_date", "")] + line(expense, "5", "") + line(cash, "", "5"), "Date is required")
    response = browser.post_raw("/accounting/journal-entries/new", head + line(expense, "750", "") + line(cash, "", "700"))
    page = response.get_data(as_text=True)
    check("a refused journal entry comes back with its lines still filled in",
          '"debit": "750"' in page and '"credit": "700"' in page)

    refused(browser, "vendor bill with no lines", "/vendors/bills/new", [("vendor_id", ids["vendor"]), ("bill_date", "2026-11-13")], "at least one line")
    refused(browser, "vendor bill line with a negative amount", "/vendors/bills/new", [("vendor_id", ids["vendor"]), ("bill_date", "2026-11-13"),
            ("label[]", "x"), ("account_id[]", expense), ("quantity[]", "1"), ("rate[]", "-100"), ("total[]", "-100")], "negative")
    invoice = lambda qty, discount: [("customer_id", ids["customer"]), ("invoice_date", "2026-11-13"), ("product_id[]", ids["product"]), ("label[]", ""),
                                     ("account_id[]", revenue), ("unit[]", "Units"), ("quantity[]", qty), ("rate[]", "100"),
                                     ("discount_percent[]", discount), ("sales_tax_rate_id[]", ids["sales_tax"])]
    refused(browser, "sales invoice with no lines", "/sales/invoices/new", [("customer_id", ids["customer"]), ("invoice_date", "2026-11-13")], "at least one line")
    refused(browser, "sales invoice with a 150% discount", "/sales/invoices/new", invoice("1", "150"), "between 0 and 100")
    refused(browser, "sales invoice with quantity -5", "/sales/invoices/new", invoice("-5", "0"), "negative")

    refused(browser, "a second account with code 1000", "/accounting/chart-of-accounts/new", [("code", "1000"), ("name", "Duplicate"), ("account_type", "Asset")], "already used")
    refused(browser, "an account of type 'Banana'", "/accounting/chart-of-accounts/new", [("code", "7777"), ("name", "X"), ("account_type", "Banana")], "Type must be one of")
    refused(browser, "a WHT rate of 450%", "/tax-rates/new", [("label", "Typo"), ("rate_percent", "450")], "between 0 and 100")
    refused(browser, "a sales-tax rate of -5%", "/sales/tax-rates/new", [("label", "Typo"), ("rate_percent", "-5")], "between 0 and 100")
    refused(browser, "a company whose name is only spaces", "/organizations/new", [("name", "   ")], "name is required")
    refused(browser, "a company with fiscal month 'x'", "/organizations/new", [("name", "Bad Co"), ("fiscal_year_start_month", "x")], "between 1 and 12")
    refused(browser, "renaming the company to blank", "/organizations/1/edit", [("name", "  ")], "name is required")

    # The journal service itself: the last line of defence for EVERY segment.
    from accounting.services import create_journal_entry, JournalEntryError
    conn = psycopg2.connect(SCRATCH_URL)
    cursor = conn.cursor()
    e, c = int(expense), int(cash)
    cases = {
        "no lines": [],
        "a negative amount": [{"account_id": e, "debit": D("-10"), "credit": 0}, {"account_id": c, "debit": 0, "credit": D("-10")}],
        "debit and credit on one line": [{"account_id": e, "debit": 10, "credit": 10}],
        "NaN": [{"account_id": e, "debit": D("NaN"), "credit": 0}, {"account_id": c, "debit": 0, "credit": D("NaN")}],
        "Infinity": [{"account_id": e, "debit": D("Infinity"), "credit": 0}, {"account_id": c, "debit": 0, "credit": D("Infinity")}],
        "a line with no account": [{"account_id": None, "debit": 10, "credit": 0}, {"account_id": c, "debit": 0, "credit": 10}],
        "an unbalanced entry": [{"account_id": e, "debit": 10, "credit": 0}, {"account_id": c, "debit": 0, "credit": 9}],
    }
    slipped = []
    for name, lines in cases.items():
        try:
            create_journal_entry(cursor, organization_id=1, reference="x", accounting_date="2026-11-13", lines=lines)
            slipped.append(name)
        except JournalEntryError:
            pass
        conn.rollback()
    conn.close()
    check("create_journal_entry() itself refuses: " + ", ".join(cases), not slipped, f"accepted: {slipped}")


def test_7_second_company(browser, ids):
    section("7. A second company")
    response = browser.submit("/organizations/new", {"name": "Second Co", "nature_of_business": "Trading"})
    check("'New Organization' creates it", response.status_code == 302, response.status_code)
    org2 = one("SELECT organization_id FROM organizations WHERE name = 'Second Co'")
    ids["org2"] = org2
    counts = (one("SELECT count(*) FROM chart_of_accounts WHERE organization_id = %s", (org2,)),
              one("SELECT count(*) FROM tax_rates WHERE organization_id = %s", (org2,)),
              one("SELECT count(*) FROM sales_tax_rates WHERE organization_id = %s", (org2,)))
    check("it has its starter data immediately, without a restart (14 accounts, 8 WHT rates, 5 sales-tax rates)",
          counts == (14, 8, 5), f"got {counts}")
    check("its creator is a member of it",
          one("SELECT count(*) FROM organization_memberships WHERE organization_id = %s", (org2,)) == 1)
    browser.submit("/vendors/new", {"name": "Vendor Two"})
    ids["vendor2"] = str(one("SELECT vendor_id FROM vendors WHERE name = 'Vendor Two'"))
    response = browser.submit("/vendors/payments/new", {"vendor_id": ids["vendor2"], "amount": "1000", "payment_date": "2026-11-13",
                                                        "paid_from_account_id": account("1010", org2)}, has="vendor_id")
    check("it can record a payment straight away", response.status_code == 302, error_box(response) or response.status_code)
    response = browser.submit("/", {"org_id": "1"}, has="org_id", action="/organizations/switch/1")
    check("the top-bar switcher goes back to the first company", response.status_code == 302, response.status_code)
    response = browser.submit("/", {"org_id": str(org2)}, has="org_id", action=f"/organizations/switch/{org2}")
    check("...and back into the second one (the creator is not locked out)", response.status_code == 302, response.status_code)

    # a company nobody in this session belongs to
    q("INSERT INTO organizations (name) VALUES ('Somebody Elses Co')")
    stranger = one("SELECT organization_id FROM organizations WHERE name = 'Somebody Elses Co'")
    pages = "".join(browser.get(p).get_data(as_text=True) for p in ("/", "/organizations/pick", "/organizations/manage"))
    check("a company you are not a member of is not listed anywhere", "Somebody Elses Co" not in pages)
    response = browser.post_raw(f"/organizations/switch/{stranger}", [])
    check("...and cannot be switched into (403)", response.status_code == 403, response.status_code)

    # ---- logo uploads (stored in a temporary folder for this test, not in static/) ----
    import io
    import tempfile
    import organizations.routes as organization_routes
    real_folder = organization_routes.UPLOAD_FOLDER
    organization_routes.UPLOAD_FOLDER = tempfile.mkdtemp(prefix="lp_test_uploads_")
    try:
        def new_company_with_logo(name, filename, content):
            return browser.client.post("/organizations/new", content_type="multipart/form-data", data={
                "csrf_token": browser.token(), "name": name, "logo": (io.BytesIO(content), filename)})

        before = row_counts()
        response = new_company_with_logo("Web Page Co", "page.html", b"<script>alert(1)</script>")
        check("a web page (page.html) uploaded as a 'logo' is refused, and nothing is saved",
              response.status_code == 400 and row_counts() == before and not os.listdir(organization_routes.UPLOAD_FOLDER),
              f"status {response.status_code}")
        response = new_company_with_logo("Drawing Co", "logo.svg", b"<svg onload='alert(1)'/>")
        check("so is an .svg (it can carry a script too)", response.status_code == 400 and row_counts() == before)
        new_company_with_logo("Logo Co A", "logo.png", b"AAAA")
        new_company_with_logo("Logo Co B", "logo.png", b"BBBB")
        path_a = one("SELECT logo_path FROM organizations WHERE name = 'Logo Co A'")
        path_b = one("SELECT logo_path FROM organizations WHERE name = 'Logo Co B'")
        check("two companies that both upload 'logo.png' each keep their own file",
              bool(path_a) and bool(path_b) and path_a != path_b
              and Path(path_a).read_bytes() == b"AAAA" and Path(path_b).read_bytes() == b"BBBB",
              f"{path_a} / {path_b}")
        # go back to working in "Second Co" for the tests that follow
        browser.submit("/", {"org_id": str(org2)}, has="org_id", action=f"/organizations/switch/{org2}")
    finally:
        import shutil
        shutil.rmtree(organization_routes.UPLOAD_FOLDER, ignore_errors=True)
        organization_routes.UPLOAD_FOLDER = real_folder


def test_8_isolation(browser, ids):
    section("8. One company can never read or write another company's records")
    org2 = ids["org2"]   # the session is working in the second company now
    for label, path in [("journal entry", f"/accounting/journal-entries/{ids['je']}"), ("vendor bill", f"/vendors/bills/{ids['bill']}"),
                        ("vendor payment", f"/vendors/payments/{ids['payment']}"), ("customer receipt", f"/customers/receipts/{ids['receipt']}"),
                        ("sales invoice", f"/sales/invoices/{ids['invoice']}")]:
        check(f"company 2 cannot open company 1's {label} (404)", browser.get(path).status_code == 404)

    bank2, expense2, revenue2 = account("1010", org2), account("6000", org2), account("4000", org2)
    browser.submit("/customers/new", {"name": "Customer Two"})
    browser.submit("/sales/products/new", {"name": "Product Two", "unit": "Units"})
    customer2 = str(one("SELECT customer_id FROM customers WHERE name = 'Customer Two'"))
    product2 = str(one("SELECT product_id FROM products WHERE name = 'Product Two'"))
    tax2 = str(one("SELECT sales_tax_rate_id FROM sales_tax_rates WHERE organization_id = %s AND rate_percent = 18", (org2,)))
    bank1, expense1, revenue1, cash1 = account("1010", 1), account("6000", 1), account("4000", 1), account("1000", 1)
    bill = lambda vendor, acc: [("vendor_id", vendor), ("bill_date", "2026-11-13"), ("label[]", "x"), ("account_id[]", acc),
                                ("quantity[]", "1"), ("rate[]", "100"), ("total[]", "100")]
    invoice = lambda customer, product, acc, tax: [("customer_id", customer), ("invoice_date", "2026-11-13"), ("product_id[]", product), ("label[]", ""),
                                                   ("account_id[]", acc), ("unit[]", "Units"), ("quantity[]", "1"), ("rate[]", "100"),
                                                   ("discount_percent[]", "0"), ("sales_tax_rate_id[]", tax)]
    attempts = [
        ("bill for company 1's vendor", "/vendors/bills/new", bill(ids["vendor"], expense2)),
        ("bill to company 1's expense account", "/vendors/bills/new", bill(ids["vendor2"], expense1)),
        ("payment to company 1's vendor", "/vendors/payments/new", [("vendor_id", ids["vendor"]), ("amount", "100"), ("payment_date", "2026-11-13"), ("paid_from_account_id", bank2)]),
        ("payment from company 1's bank", "/vendors/payments/new", [("vendor_id", ids["vendor2"]), ("amount", "100"), ("payment_date", "2026-11-13"), ("paid_from_account_id", bank1)]),
        ("receipt from company 1's customer", "/customers/receipts/new", [("customer_id", ids["customer"]), ("amount", "100"), ("receipt_date", "2026-11-13"), ("deposit_to_account_id", bank2)]),
        ("receipt into company 1's bank", "/customers/receipts/new", [("customer_id", customer2), ("amount", "100"), ("receipt_date", "2026-11-13"), ("deposit_to_account_id", bank1)]),
        ("invoice to company 1's customer", "/sales/invoices/new", invoice(ids["customer"], product2, revenue2, tax2)),
        ("invoice for company 1's product", "/sales/invoices/new", invoice(customer2, ids["product"], revenue2, tax2)),
        ("invoice to company 1's revenue account", "/sales/invoices/new", invoice(customer2, product2, revenue1, tax2)),
        ("invoice with company 1's sales-tax rate", "/sales/invoices/new", invoice(customer2, product2, revenue2, ids["sales_tax"])),
        ("product pointing at company 1's account", "/sales/products/new", [("name", "PX"), ("unit", "Units"), ("default_account_id", revenue1)]),
        ("journal entry on company 1's accounts", "/accounting/journal-entries/new", [("reference", "x"), ("accounting_date", "2026-11-13"),
            ("account_id[]", expense1), ("partner_name[]", ""), ("label[]", ""), ("debit[]", "10"), ("credit[]", ""),
            ("account_id[]", cash1), ("partner_name[]", ""), ("label[]", ""), ("debit[]", ""), ("credit[]", "10")]),
        ("posting company 1's bill", f"/vendors/bills/{ids['bill']}/post", []),
        ("posting company 1's invoice", f"/sales/invoices/{ids['invoice']}/post", []),
        ("posting company 1's journal entry", f"/accounting/journal-entries/{ids['je']}/post", []),
        ("retiring company 1's WHT rate", f"/tax-rates/{one('SELECT min(tax_rate_id) FROM tax_rates WHERE organization_id = 1')}/toggle", []),
        ("retiring company 1's sales-tax rate", f"/sales/tax-rates/{ids['sales_tax']}/toggle", []),
        ("editing company 1's details", "/organizations/1/edit", [("name", "Hijacked")]),
    ]
    snapshot = lambda: (q("SELECT name FROM organizations WHERE organization_id = 1"),
                        q("SELECT tax_rate_id, is_active FROM tax_rates WHERE organization_id = 1 ORDER BY 1"),
                        q("SELECT sales_tax_rate_id, is_active FROM sales_tax_rates WHERE organization_id = 1 ORDER BY 1"),
                        q("SELECT journal_header_id, status, posted_at FROM journal_headers WHERE organization_id = 1 ORDER BY 1"))
    company_one_before = snapshot()
    for description, path, fields in attempts:
        refused(browser, f"from company 2: {description}", path, fields)
    check("after all of that, company 1 is exactly as it was", snapshot() == company_one_before)


# ---------------------------------------------------------------------
# 9. The dashboard
# ---------------------------------------------------------------------
def dashboard_figures(page):
    """{name: (exact value, text shown on screen)} for every figure on the dashboard."""
    found = {}
    for name, value, inner in re.findall(r'data-figure="([^"]+)" data-value="([^"]*)"[^>]*>(.*?)</div>', page, re.S):
        found[name] = (D(value), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip())
    return found


def dashboard_changes(page):
    """{name: (direction, figure compared with, change, text shown, colour of the badge)} -
    the 'how it moved' line of each stat card. The colour is good / bad / neutral, or None with no badge."""
    found = {}
    pattern = r'data-delta="([^"]+)" data-direction="([^"]+)" data-previous="([^"]*)" data-change="([^"]*)">(.*?)</div>'
    for name, direction, previous, change, inner in re.findall(pattern, page, re.S):
        badge = re.search(r'class="lp-trend (\w+)"', inner)
        found[name] = (direction, D(previous), D(change), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip(),
                       badge.group(1) if badge else None)
    return found


def dashboard_attention(page):
    """{kind: (number in the badge, text)} for each row of 'Needs attention'."""
    rows = {}
    for kind, inner in re.findall(r'data-attention="([^"]+)">(.*?)</a>', page, re.S):
        badge = re.search(r'class="lp-count[^"]*">(\d+)<', inner)
        rows[kind] = (int(badge.group(1)) if badge else None, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip())
    return rows


def posted_total(organization_id, which_accounts, upto, since=None):
    """Debits minus credits of POSTED lines of one company - read straight from the tables."""
    sql = f"""SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM journal_lines l
              JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
              JOIN chart_of_accounts c ON c.account_id = l.account_id
              WHERE h.organization_id = %s AND h.status = 'Posted' AND ({which_accounts})
                AND h.accounting_date <= %s"""
    args = [organization_id, upto]
    if since:
        sql += " AND h.accounting_date >= %s"
        args.append(since)
    return one(sql, tuple(args))


def test_9_dashboard(browser, ids):
    section("9. The dashboard shows exactly what is in the ledger")
    import dashboard.routes as dashboard_routes
    from datetime import date
    real_today = dashboard_routes._today
    # Every date below is written around "today is 13 Nov 2026", so the
    # checks give the same answer on whatever day they are run.
    dashboard_routes._today = lambda: date(2026, 11, 13)
    try:
        _dashboard_checks(browser, ids)
    finally:
        dashboard_routes._today = real_today


def _dashboard_checks(browser, ids):
    def open_dashboard(period=None):
        response = browser.get("/" if period is None else f"/?period={period}")
        assert response.status_code == 200, f"dashboard -> {response.status_code}"
        page = response.get_data(as_text=True)
        return page, dashboard_figures(page), dashboard_changes(page)

    def shows(figures, name, expected):
        """The exact figure is right AND the same amount is what is printed on the page."""
        value, text = figures.get(name, (None, ""))
        return value == D(expected) and f"{D(expected):,.2f}" in text

    def explain(figures, names):
        return ", ".join(f"{n}={figures[n][0] if n in figures else 'missing'}" for n in names)

    # ---- a brand-new company: nothing recorded yet ---------------------------
    response = browser.submit("/organizations/new", {"name": "Dashboard Co"})
    org = one("SELECT organization_id FROM organizations WHERE name = 'Dashboard Co'")
    check("a new company 'Dashboard Co' is created for these checks", response.status_code == 302 and org is not None)
    page, figures, changes = open_dashboard()
    six = ["cash", "receivables", "payables", "revenue", "expenses", "profit"]
    check("an empty company shows 0.00 for all six headline figures",
          all(shows(figures, name, "0") for name in six), explain(figures, six))
    check("...with no percentage and no up/down arrow anywhere (the reference showed 'Rs0 revenue, +12.8%')",
          all(changes[name][0] == "flat" and "%" not in changes[name][3] for name in six)
          and 'class="lp-trend' not in page, str({name: changes[name][3] for name in six}))
    check("...and offers the 'Start your books' steps instead of an empty chart",
          'data-panel="getting-started"' in page and 'data-panel="trend"' not in page)
    check("...and says nothing is waiting to be posted", "Nothing is waiting to be posted" in page)

    # ---- record a known set of transactions through the real screens ---------
    acc = lambda code: account(code, org)
    browser.submit("/customers/new", {"name": "Dash Customer", "nature_of_supply": "Supplies"})
    browser.submit("/vendors/new", {"name": "Dash Vendor", "nature_of_purchase": "Services"})
    browser.submit("/sales/products/new", {"name": "Dash Product", "unit": "Units", "default_account_id": acc("4000")})
    customer = str(one("SELECT customer_id FROM customers WHERE organization_id = %s AND name = 'Dash Customer'", (org,)))
    vendor = str(one("SELECT vendor_id FROM vendors WHERE organization_id = %s AND name = 'Dash Vendor'", (org,)))
    product = str(one("SELECT product_id FROM products WHERE organization_id = %s AND name = 'Dash Product'", (org,)))
    tax = {rate: str(one("SELECT min(sales_tax_rate_id) FROM sales_tax_rates WHERE organization_id = %s AND rate_percent = %s",
                         (org, rate))) for rate in (0, 18)}
    problems = []

    def journal_entry(day, debit_code, credit_code, amount, post=True, reference=None):
        response = browser.submit("/accounting/journal-entries/new", {"reference": reference or f"Dash {day}", "accounting_date": day}, rows=[
            ("account_id[]", acc(debit_code)), ("partner_name[]", ""), ("label[]", ""), ("debit[]", amount), ("credit[]", ""),
            ("account_id[]", acc(credit_code)), ("partner_name[]", ""), ("label[]", ""), ("debit[]", ""), ("credit[]", amount)])
        if response.status_code != 302:
            problems.append(f"journal entry {day}: {error_box(response) or response.status_code}")
            return None
        entry = one("SELECT max(journal_header_id) FROM journal_headers WHERE organization_id = %s", (org,))
        if post:
            browser.submit(f"/accounting/journal-entries/{entry}", action_contains="/post")
        return entry

    def invoice(day, amount, tax_percent=0, post=True):
        response = browser.submit("/sales/invoices/new", {"customer_id": customer, "invoice_date": day}, has="customer_id", rows=[
            ("product_id[]", product), ("label[]", "Dash"), ("account_id[]", acc("4000")), ("unit[]", "Units"),
            ("quantity[]", "1"), ("rate[]", amount), ("discount_percent[]", "0"), ("sales_tax_rate_id[]", tax[tax_percent])])
        if response.status_code != 302:
            problems.append(f"invoice {day}: {error_box(response) or response.status_code}")
            return None
        document = one("SELECT max(sales_invoice_id) FROM sales_invoices WHERE organization_id = %s", (org,))
        if post:
            browser.submit(f"/sales/invoices/{document}", action_contains="/post")
        return document

    def bill(day, amount, post=True):
        response = browser.submit("/vendors/bills/new", {"vendor_id": vendor, "bill_date": day}, has="vendor_id", rows=[
            ("label[]", "Dash"), ("account_id[]", acc("6000")), ("quantity[]", "1"), ("rate[]", amount), ("total[]", amount)])
        if response.status_code != 302:
            problems.append(f"bill {day}: {error_box(response) or response.status_code}")
            return None
        document = one("SELECT max(vendor_bill_id) FROM vendor_bills WHERE organization_id = %s", (org,))
        if post:
            browser.submit(f"/vendors/bills/{document}", action_contains="/post")
        return document

    journal_entry("2026-06-15", "1010", "3000", "1000000")      # owner pays 1,000,000 into the bank
    journal_entry("2026-06-15", "1000", "3000", "50000")        # ... and 50,000 into the cash box
    invoice("2026-10-05", "120000")                             # October, inside 1-13 Oct
    invoice("2026-10-20", "80000")                              # October, after the 13th
    invoice("2026-11-03", "150000", tax_percent=18)             # November: 150,000 + 27,000 sales tax
    bill("2026-10-10", "30000")                                 # October, inside 1-13 Oct
    bill("2026-11-04", "40000")                                 # November
    # dates sitting exactly ON the edges of the periods (cash expenses)
    journal_entry("2026-10-13", "6000", "1000", "600")          # last day of the comparison (1-13 Oct): counted there
    journal_entry("2026-10-14", "6000", "1000", "700")          # one day after it: NOT in the comparison
    journal_entry("2026-10-31", "6000", "1000", "2000")         # the day before the period: opening balance only
    journal_entry("2026-11-01", "6000", "1000", "3000")         # first day of the period: counted
    journal_entry("2026-11-13", "6000", "1000", "1000")         # today: counted
    journal_entry("2026-11-14", "6000", "1000", "500")          # tomorrow: posted, but not counted yet
    journal_entry("2026-12-25", "6000", "1000", "7000")         # next month: posted, but not counted yet
    response = browser.submit("/customers/receipts/new", {
        "customer_id": customer, "amount": "100000", "receipt_date": "2026-11-10", "has_wht": "on", "wht_rate": "4.00",
        "deposit_to_account_id": acc("1010")}, has="customer_id")       # bank +96,000, WHT receivable +4,000
    if response.status_code != 302:
        problems.append(f"receipt: {error_box(response) or response.status_code}")
    response = browser.submit("/vendors/payments/new", {
        "vendor_id": vendor, "amount": "30000", "payment_date": "2026-11-11", "has_wht": "on", "wht_rate": "10.00",
        "paid_from_account_id": acc("1010"), "payment_method": "Manual"}, has="vendor_id")   # bank -27,000, WHT payable +3,000
    if response.status_code != 302:
        problems.append(f"payment: {error_box(response) or response.status_code}")
    # DRAFTS - deliberately huge, so that one leaking into a figure cannot be missed
    invoice("2026-11-12", "999999", post=False)
    draft_bill = bill("2026-11-12", "55555", post=False)
    journal_entry("2026-11-12", "6000", "1000", "11111", post=False, reference="<script>alert(1)</script>")
    recorded = (one("SELECT count(*) FROM journal_headers WHERE organization_id = %s AND status = 'Posted'", (org,)),
                one("SELECT count(*) FROM journal_headers WHERE organization_id = %s AND status = 'Draft'", (org,)))
    check("the test transactions were all recorded (16 posted entries, 3 drafts)",
          not problems and recorded == (16, 3), "; ".join(problems) or f"posted, drafts = {recorded}")

    # ---- "This month" on 13 Nov 2026 = 1-13 Nov, compared with 1-13 Oct ------
    page, figures, changes = open_dashboard()
    check("the page says which days it covers: 1 – 13 Nov 2026, compared with 1 – 13 Oct 2026",
          "1 – 13 Nov 2026" in page and "compared with 1 – 13 Oct 2026" in page and "Balances as at 13 Nov 2026" in page)
    #   bank 1,000,000 + 96,000 - 27,000 = 1,069,000 ; cash 50,000 - 600 - 700 - 2,000 - 3,000 - 1,000 = 42,700
    check("Cash & bank is 1,111,700.00 (bank 1,069,000 + cash 42,700; entries dated 14 Nov and 25 Dec are left out)",
          shows(figures, "cash", "1111700"), explain(figures, ["cash"]))
    check("...up 65,000.00 since 31 Oct 2026 (it was 1,046,700.00 the evening before the period)",
          changes["cash"][:3] == ("up", D("1046700"), D("65000")) and "65,000.00" in changes["cash"][3]
          and "since 31 Oct 2026" in changes["cash"][3], str(changes["cash"]))
    #   invoices 120,000 + 80,000 + 177,000 (incl. tax) - receipt 100,000
    check("Trade receivables is 277,000.00 (three posted invoices less the receipt; the 999,999 draft is not in it)",
          shows(figures, "receivables", "277000") and changes["receivables"][:3] == ("up", D("200000"), D("77000")),
          explain(figures, ["receivables"]) + f" {changes['receivables']}")
    check("Trade payables is 40,000.00 (bills 30,000 + 40,000 less the 30,000 payment; the 55,555 draft is not in it)",
          shows(figures, "payables", "40000") and changes["payables"][:3] == ("up", D("30000"), D("10000")),
          explain(figures, ["payables"]) + f" {changes['payables']}")
    check("Revenue for 1-13 Nov is 150,000.00 - sales tax is not revenue, and the draft invoice is not counted",
          shows(figures, "revenue", "150000"), explain(figures, ["revenue"]))
    check("...up 25.0% on 120,000.00 for 1-13 Oct (like for like: the 80,000 invoice of 20 Oct is NOT in the comparison)",
          changes["revenue"][:3] == ("up", D("120000"), D("30000")) and "25.0%" in changes["revenue"][3]
          and "120,000.00" in changes["revenue"][3] and "1 – 13 Oct 2026" in changes["revenue"][3], str(changes["revenue"]))
    #   40,000 bill + 3,000 (1 Nov) + 1,000 (13 Nov) ; comparison 30,000 bill + 600 (13 Oct)
    check("Expenses for 1-13 Nov is 44,000.00 (1 Nov and 13 Nov are counted; 31 Oct, 14 Nov and the drafts are not)",
          shows(figures, "expenses", "44000"), explain(figures, ["expenses"]))
    check("...up 43.8% on 30,600.00 for 1-13 Oct (13 Oct is counted there, 14 Oct is not)",
          changes["expenses"][:3] == ("up", D("30600"), D("13400")) and "43.8%" in changes["expenses"][3], str(changes["expenses"]))
    check("Net profit is 106,000.00, up 18.6% on 89,400.00",
          shows(figures, "profit", "106000") and changes["profit"][:3] == ("up", D("89400"), D("16600"))
          and "18.6%" in changes["profit"][3], explain(figures, ["profit"]) + f" {changes['profit']}")
    colours = {name: changes[name][4] for name in six}
    check("a rise in revenue, profit or cash is coloured as good news, a rise in expenses as bad news, "
          "and receivables / payables are not judged either way",
          colours == {"cash": "good", "receivables": "neutral", "payables": "neutral",
                      "revenue": "good", "expenses": "bad", "profit": "good"}, str(colours))

    tax_ledgers = {code: D(value) for code, value in re.findall(r'data-tax-account="(\d+)" data-value="([^"]*)"', page)}
    check("tax ledgers: WHT payable (services) 3,000.00, WHT receivable (supplies) 4,000.00, output sales tax 27,000.00",
          tax_ledgers == {"2100": D("0"), "2110": D("3000"), "1150": D("4000"), "1160": D("0"), "2200": D("27000")},
          str(tax_ledgers))
    cash_rows = re.findall(r'data-cash-account="(\d+)".*?class="amount[^"]*">([^<]*)<', page, re.S)
    check("the cash panel lists exactly the accounts it counted: 1000 at 42,700.00 and 1010 at 1,069,000.00",
          cash_rows == [("1000", "42,700.00"), ("1010", "1,069,000.00")], str(cash_rows))
    partners = re.findall(r'data-partner="(\w+)" data-value="([^"]*)">.*?class="name">([^<]*)<', page, re.S)
    check("largest open balances: Dash Customer owes 277,000.00; Dash Vendor is owed 40,000.00",
          partners == [("receivable", "277000.00", "Dash Customer"), ("payable", "40000.00", "Dash Vendor")], str(partners))
    #   assets 1,111,700 + 277,000 + 4,000 ; liabilities 40,000 + 3,000 + 27,000
    #   equity 1,050,000 + profit to date (revenue 350,000 - expenses 77,300)
    check("Assets 1,392,700.00 = Liabilities 70,000.00 + Equity 1,322,700.00 (profit to date is counted inside equity)",
          shows(figures, "assets", "1392700") and shows(figures, "liabilities", "70000") and shows(figures, "equity", "1322700")
          and "272,700.00 profit to date" in figures["equity"][1]
          and figures["assets"][0] == figures["liabilities"][0] + figures["equity"][0],
          explain(figures, ["assets", "liabilities", "equity"]))
    check("...and the page says the books balance, with no warning",
          'data-ledger="balanced"' in page and "data-alert=" not in page)
    attention = dashboard_attention(page)
    check("'Needs attention' counts the drafts: 1 invoice (999,999.00), 1 bill (55,555.00), 1 journal entry",
          sorted(attention) == ["draft-bills", "draft-invoices", "draft-journals", "future-dated"]
          and attention["draft-invoices"][0] == 1 and "999,999.00 not yet posted" in attention["draft-invoices"][1]
          and attention["draft-bills"][0] == 1 and "55,555.00 not yet posted" in attention["draft-bills"][1]
          and attention["draft-journals"][0] == 1, str(attention))
    check("...and the 2 posted entries dated after today, so nobody wonders where they went",
          attention.get("future-dated", (None, ""))[0] == 2, str(attention.get("future-dated")))
    months = re.findall(r'data-month="([^"]+)" data-revenue="([^"]*)" data-expenses="([^"]*)"', page)
    check("the six-month chart: nothing for Jun-Sep, October 200,000 / 33,300, November so far 150,000 / 44,000",
          months == [("Jun 2026", "0.00", "0.00"), ("Jul 2026", "0.00", "0.00"), ("Aug 2026", "0.00", "0.00"),
                     ("Sep 2026", "0.00", "0.00"), ("Oct 2026", "200000.00", "33300.00"), ("Nov 2026", "150000.00", "44000.00")],
          str(months))
    bars = re.findall(r'class="lp-trend-bar (?:revenue|expenses)[^"]*" style="height: (\d+)%"', page)
    check("...with bars drawn to one scale (tallest = 200,000), and no bar at all for a month with nothing",
          bars == ["0", "0"] * 4 + ["100", "17", "75", "22"] and "Tallest bar = 200,000.00" in page, str(bars))
    spark = re.search(r'data-kpi="cash".*?class="lp-spark[^>]*>(.*?)</div>', page, re.S)
    spark = re.findall(r'title="([^"]*)"', spark.group(1)) if spark else []
    check("the small bars on the cash card are the month-end balances (1,050,000 from June, 1,046,700 at end of October)",
          spark == ["Jun 2026: 1,050,000.00", "Jul 2026: 1,050,000.00", "Aug 2026: 1,050,000.00",
                    "Sep 2026: 1,050,000.00", "Oct 2026: 1,046,700.00", "Nov 2026: 1,111,700.00"], str(spark))
    check("the recent-entries list shows drafts as Draft and marks entries dated after today",
          page.count('<span class="lp-pill draft">Draft</span>') == 3 and page.count("Future-dated") == 2)
    check("the 'Liabilitys' spelling mistake of the design reference is not repeated", "Liabilitys" not in page)
    check("text typed by a user (a reference of '<script>...') is shown as text, never run as code",
          "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page)

    # ---- every figure again, worked out a second way: straight from the tables
    today = "2026-11-13"
    straight = {
        "cash": posted_total(org, "c.code IN ('1000', '1010')", today),
        "receivables": posted_total(org, "c.code = '1100'", today),
        "payables": -posted_total(org, "c.code = '2000'", today),
        "revenue": -posted_total(org, "c.account_type = 'Revenue'", today, "2026-11-01"),
        "expenses": posted_total(org, "c.account_type = 'Expense'", today, "2026-11-01"),
        "assets": posted_total(org, "c.account_type = 'Asset'", today),
        "liabilities": -posted_total(org, "c.account_type = 'Liability'", today),
        "equity": -posted_total(org, "c.account_type IN ('Equity', 'Revenue', 'Expense')", today),
    }
    check("the same figures come out when they are added up directly from the journal tables",
          all(figures[name][0] == value for name, value in straight.items()),
          str({name: (figures[name][0], value) for name, value in straight.items() if figures[name][0] != value}))

    # ---- the other periods ---------------------------------------------------
    page, figures, changes = open_dashboard("last_month")
    check("'Last month' is the whole of October against the whole of September",
          "1 – 31 Oct 2026" in page and "compared with 1 – 30 Sep 2026" in page and "Balances as at 31 Oct 2026" in page)
    check("...revenue 200,000.00 (both October invoices), expenses 33,300.00, profit 166,700.00",
          shows(figures, "revenue", "200000") and shows(figures, "expenses", "33300") and shows(figures, "profit", "166700"),
          explain(figures, ["revenue", "expenses", "profit"]))
    check("...with NO percentage, because September had nothing to compare with - only the two amounts",
          all("%" not in changes[name][3] and changes[name][1] == 0 and "0.00 in 1 – 30 Sep 2026" in changes[name][3]
              for name in ("revenue", "expenses", "profit")), str({n: changes[n][3] for n in ("revenue", "expenses", "profit")}))
    check("...and balances as at 31 Oct: cash & bank 1,046,700.00 (down 3,300.00 since 30 Sep), receivables 200,000.00",
          shows(figures, "cash", "1046700") and changes["cash"][:3] == ("down", D("1050000"), D("-3300"))
          and "since 30 Sep 2026" in changes["cash"][3] and shows(figures, "receivables", "200000")
          and shows(figures, "payables", "30000"), explain(figures, ["cash", "receivables", "payables"]) + f" {changes['cash']}")
    page, figures, changes = open_dashboard("quarter")
    check("'This quarter' follows the fiscal year (July start): 1 Oct – 13 Nov 2026, Q2 of FY 2026-27, against 1 Jul – 13 Aug",
          "1 Oct – 13 Nov 2026" in page and "Q2 · FY 2026-27" in page and "compared with 1 Jul – 13 Aug 2026" in page)
    check("...revenue 350,000.00, expenses 77,300.00, profit 272,700.00",
          shows(figures, "revenue", "350000") and shows(figures, "expenses", "77300") and shows(figures, "profit", "272700"),
          explain(figures, ["revenue", "expenses", "profit"]))
    page, figures, changes = open_dashboard("fiscal_year")
    check("'This fiscal year' is 1 Jul – 13 Nov 2026 against the same days of 2025",
          "1 Jul – 13 Nov 2026" in page and "FY 2026-27" in page and "compared with 1 Jul – 13 Nov 2025" in page)
    check("...revenue 350,000.00; cash & bank up 61,700.00 since 30 Jun 2026 (the June capital is opening balance, not movement)",
          shows(figures, "revenue", "350000") and shows(figures, "cash", "1111700")
          and changes["cash"][:3] == ("up", D("1050000"), D("61700")) and "since 30 Jun 2026" in changes["cash"][3],
          explain(figures, ["revenue", "cash"]) + f" {changes['cash']}")
    q("UPDATE organizations SET fiscal_year_start_month = 1 WHERE organization_id = %s", (org,))
    page, figures, changes = open_dashboard("fiscal_year")
    check("a company whose year starts in January gets 1 Jan – 13 Nov 2026 (FY 2026) instead",
          "1 Jan – 13 Nov 2026" in page and "FY 2026" in page and "FY 2026-27" not in page
          and changes["cash"][:3] == ("up", D("0"), D("1111700")), str(changes["cash"]))
    q("UPDATE organizations SET fiscal_year_start_month = 7 WHERE organization_id = %s", (org,))
    page, figures, changes = open_dashboard("no-such-period")
    check("a period the app does not know is treated as 'This month', not as an error",
          shows(figures, "revenue", "150000") and "1 – 13 Nov 2026" in page)

    # ---- the dashboard follows the ledger: post a draft, the numbers move ----
    browser.submit(f"/vendors/bills/{draft_bill}", action_contains="/post")
    page, figures, changes = open_dashboard()
    check("posting the 55,555 draft bill moves expenses to 99,555.00 and payables to 95,555.00",
          shows(figures, "expenses", "99555") and shows(figures, "payables", "95555") and shows(figures, "profit", "50445"),
          explain(figures, ["expenses", "payables", "profit"]))
    check("...profit is now DOWN on last month's 89,400.00, shown as a fall of 43.6%",
          changes["profit"][:3] == ("down", D("89400"), D("-38955")) and "43.6%" in changes["profit"][3]
          and changes["profit"][4] == "bad", str(changes["profit"]))
    attention = dashboard_attention(page)
    check("...and the bill has left 'Needs attention' (the other rows are unchanged)",
          sorted(attention) == ["draft-invoices", "draft-journals", "future-dated"]
          and (attention["draft-invoices"][0], attention["draft-journals"][0], attention["future-dated"][0]) == (1, 1, 2),
          str(attention))

    # ---- nothing on the page is a dead end, and looking changes nothing -----
    links = sorted(set(re.findall(r'href="(/[^"#]*)"', page)))
    broken = [f"{link} -> {browser.get(link).status_code}" for link in links if browser.get(link).status_code != 200]
    check(f"all {len(links)} links on the dashboard open", len(links) >= 25 and not broken, ", ".join(broken))
    before = row_counts()
    for period in (None, "last_month", "quarter", "fiscal_year"):
        open_dashboard(period)
    check("opening the dashboard (all four periods) writes nothing to the database", row_counts() == before)

    # ---- warnings that must not stay hidden ---------------------------------
    header = one("""INSERT INTO journal_headers (organization_id, accounting_date, reference, status)
                    VALUES (%s, '2026-11-05', 'one-sided (inserted by the test)', 'Posted') RETURNING journal_header_id""", (org,))
    q("INSERT INTO journal_lines (journal_header_id, account_id, debit, credit) VALUES (%s, %s, 123.45, 0)", (header, acc("6000")))
    try:
        page, figures, changes = open_dashboard()
        check("if the ledger is ever out of balance, the dashboard says so in red, with the amount (123.45)",
              'data-alert="out-of-balance"' in page and "out of balance by 123.45" in page
              and 'data-ledger="out-of-balance"' in page and 'data-ledger="balanced"' not in page)
        browser.post_raw("/organizations/switch/1", [("org_id", "1")])
        other = browser.get("/").get_data(as_text=True)
        browser.post_raw(f"/organizations/switch/{org}", [("org_id", str(org))])
        check("...but only for THAT company: the first company's dashboard still says its books balance",
              'data-ledger="balanced"' in other and "data-alert=" not in other)
    finally:
        q("DELETE FROM journal_lines WHERE journal_header_id = %s", (header,))
        q("DELETE FROM journal_headers WHERE journal_header_id = %s", (header,))
    q("UPDATE chart_of_accounts SET account_type = 'Income' WHERE account_id = %s", (acc("4000"),))
    try:
        page, figures, changes = open_dashboard("fiscal_year")
        check("an account with an unrecognised type ('Income') is reported with its 350,000.00, never silently dropped",
              'data-alert="unclassified"' in page and "350,000.00 sits on accounts with an unrecognised type (Income)" in page
              and shows(figures, "revenue", "0") and 'data-ledger="balanced"' in page)
    finally:
        q("UPDATE chart_of_accounts SET account_type = 'Revenue' WHERE account_id = %s", (acc("4000"),))
    page, figures, changes = open_dashboard()
    check("with both of those put back, the warnings are gone and the figures are as before",
          "data-alert=" not in page and shows(figures, "revenue", "150000") and shows(figures, "expenses", "99555"))

    # ---- each company sees only its own numbers ------------------------------
    browser.post_raw("/organizations/switch/1", [("org_id", "1")])
    page, figures, changes = open_dashboard()
    own = {
        "cash": posted_total(1, "c.code IN ('1000', '1010')", today),
        "receivables": posted_total(1, "c.code = '1100'", today),
        "payables": -posted_total(1, "c.code = '2000'", today),
        "revenue": -posted_total(1, "c.account_type = 'Revenue'", today, "2026-11-01"),
        "expenses": posted_total(1, "c.account_type = 'Expense'", today, "2026-11-01"),
    }
    check("switching to the first company shows ITS ledger: " + ", ".join(f"{name} {value:,.2f}" for name, value in own.items()),
          all(figures[name][0] == value for name, value in own.items())
          and own["revenue"] == D("9000") and own["cash"] != D("1111700"),
          str({name: (figures[name][0], value) for name, value in own.items()}))
    check("...and nothing of 'Dashboard Co' appears on it", "Dash Customer" not in page and "Dash Vendor" not in page
          and "Dashboard Co</option>" in page and "Welcome back, Abdul Majeed" in page)
    attention = {kind: row[0] for kind, row in dashboard_attention(page).items()}
    own_drafts = {
        "draft-invoices": one("SELECT count(*) FROM sales_invoices WHERE organization_id = 1 AND status = 'Draft'"),
        "draft-bills": one("SELECT count(*) FROM vendor_bills WHERE organization_id = 1 AND status = 'Draft'"),
        "draft-journals": one("""SELECT count(*) FROM journal_headers WHERE organization_id = 1 AND status = 'Draft'
                                 AND source_type NOT IN ('VendorBill', 'SalesInvoice')"""),
    }
    check("...its 'Needs attention' counts only its own drafts, and none of Dashboard Co's future-dated entries",
          attention == {kind: count for kind, count in own_drafts.items() if count}, f"page {attention}, tables {own_drafts}")

    # ---- a company that has disappeared from under the session ---------------
    ghost = one("INSERT INTO organizations (name) VALUES ('Ghost Co') RETURNING organization_id")
    q("""INSERT INTO organization_memberships (organization_id, user_id, role_id)
         SELECT %s, user_id, role_id FROM organization_memberships WHERE organization_id = 1 LIMIT 1""", (ghost,))
    switched = browser.post_raw(f"/organizations/switch/{ghost}", [("org_id", str(ghost))])
    q("DELETE FROM organization_memberships WHERE organization_id = %s", (ghost,))
    q("DELETE FROM organizations WHERE organization_id = %s", (ghost,))
    response = browser.get("/")
    check("if the active company no longer exists, the dashboard sends you to the company picker instead of crashing",
          switched.status_code == 302 and response.status_code == 302
          and response.headers["Location"].endswith("/organizations/pick"),
          f"switch {switched.status_code}, dashboard {response.status_code}")
    browser.post_raw(f"/organizations/switch/{ids['org2']}", [("org_id", str(ids["org2"]))])
    check("...and after picking a company again the dashboard opens", browser.get("/").status_code == 200)


# ---------------------------------------------------------------------
# 10. Account kinds
# ---------------------------------------------------------------------
def select_choices(page, field):
    """The codes a server-rendered <select> offers, in order (the empty placeholder left out)."""
    block = re.search(r'<select name="%s".*?</select>' % re.escape(field), page, re.S)
    if not block:
        return None
    return [text.split(" - ")[0] for value, text in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', block.group(0)) if value]


def journal_entry_choices(browser):
    """What the two account boxes of the journal entry grid offer, read from the page:
    (codes in the General account box, {group title: [(value, label), ...]} of the Partner account box, raw data)."""
    page = browser.get("/accounting/journal-entries/new").get_data(as_text=True)
    data = json.loads(re.search(r'<script type="application/json" id="je-options">(.*?)</script>', page, re.S).group(1))
    general = [choice["label"].split(" - ")[0] for group in data["general"] for choice in group["options"]]
    partner = {group["title"]: [(choice["value"], choice["label"]) for choice in group["options"]] for group in data["partner"]}
    return general, partner, data


def test_10_account_kinds(browser, ids):
    section("10. Account kinds: where each account can be picked")
    import dashboard.routes as dashboard_routes
    from datetime import date
    real_today = dashboard_routes._today
    dashboard_routes._today = lambda: date(2026, 11, 13)     # the entries below are dated around this day
    try:
        _account_kind_checks(browser, ids)
    finally:
        dashboard_routes._today = real_today
    _upgrade_of_an_older_database()


def _account_kind_checks(browser, ids):
    response = browser.submit("/organizations/new", {"name": "Kinds Co"})
    org = one("SELECT organization_id FROM organizations WHERE name = 'Kinds Co'")
    check("a new company 'Kinds Co' is created for these checks", response.status_code == 302 and org is not None)
    acc = lambda code: account(code, org)
    kind = lambda code: one("SELECT account_kind FROM chart_of_accounts WHERE organization_id = %s AND code = %s", (org, code))
    kinds = dict(q("SELECT code, account_kind FROM chart_of_accounts WHERE organization_id = %s AND account_kind <> 'General'", (org,)))
    check("its starter accounts are already marked: 1000 Cash, 1010 Bank, 1100 Trade receivable, 2000 Trade payable; the other ten are General",
          kinds == {"1000": "Cash", "1010": "Bank", "1100": "Trade receivable", "2000": "Trade payable"}
          and one("SELECT count(*) FROM chart_of_accounts WHERE organization_id = %s AND account_kind = 'General'", (org,)) == 10, str(kinds))

    browser.submit("/customers/new", {"name": "Kind Customer", "nature_of_supply": "Supplies"})
    browser.submit("/vendors/new", {"name": "Kind Vendor", "nature_of_purchase": "Services"})
    customer = str(one("SELECT customer_id FROM customers WHERE organization_id = %s AND name = 'Kind Customer'", (org,)))
    vendor = str(one("SELECT vendor_id FROM vendors WHERE organization_id = %s AND name = 'Kind Vendor'", (org,)))

    def money_boxes():
        """(codes offered as 'Paid from' on a vendor payment, codes offered as 'Deposit to' on a customer receipt)"""
        return (select_choices(browser.get("/vendors/payments/new").get_data(as_text=True), "paid_from_account_id"),
                select_choices(browser.get("/customers/receipts/new").get_data(as_text=True), "deposit_to_account_id"))

    # ---- payments and receipts: bank and cash accounts only -------------------
    check("'Paid from' on a vendor payment and 'Deposit to' on a customer receipt list ONLY 1010 Bank Account and 1000 Cash in Hand",
          money_boxes() == (["1010", "1000"], ["1010", "1000"]), str(money_boxes()))
    pay = lambda paid_from: [("vendor_id", vendor), ("amount", "100"), ("payment_date", "2026-11-10"),
                             ("paid_from_account_id", paid_from), ("payment_method", "Manual")]
    take = lambda deposit_to: [("customer_id", customer), ("amount", "100"), ("receipt_date", "2026-11-10"), ("deposit_to_account_id", deposit_to)]
    refused(browser, "a payment 'paid from' 6000 General Expenses (sent by a stale tab or a script)", "/vendors/payments/new", pay(acc("6000")), "must be a bank or cash account")
    refused(browser, "a payment 'paid from' 2000 Trade Creditors", "/vendors/payments/new", pay(acc("2000")), "must be a bank or cash account")
    refused(browser, "a payment with the account box left empty", "/vendors/payments/new", pay(""), "bank or cash")
    refused(browser, "a payment with 'abc' as its account", "/vendors/payments/new", pay("abc"), "bank or cash")
    refused(browser, "a receipt 'deposited to' 4000 Sales Revenue", "/customers/receipts/new", take(acc("4000")), "must be a bank or cash account")
    refused(browser, "a receipt 'deposited to' 1100 Trade Receivables", "/customers/receipts/new", take(acc("1100")), "must be a bank or cash account")
    refused(browser, "a receipt with the account box left empty", "/customers/receipts/new", take(""), "bank or cash")

    # ---- new accounts carry a kind ---------------------------------------------
    new_account = lambda code, name, account_type, account_kind=None: browser.submit(
        "/accounting/chart-of-accounts/new", {"code": code, "name": name, "account_type": account_type, "account_kind": account_kind}, has="code")
    made = [new_account("1250", "Faysal Bank - Current", "Asset", "Bank").status_code,
            new_account("1050", "Security Deposit", "Asset", "General").status_code,
            new_account("1300", "Advance to Employee - Bilal Ahmed", "Asset", "Non-trading receivable").status_code,
            new_account("2300", "Loan from Director - Kamran Sheikh", "Liability", "Non-trading payable").status_code]
    check("the New Account form saves a Bank account (1250), a General one (1050) and one account each for an employee (1300) and a director (2300)",
          made == [302] * 4 and (kind("1250"), kind("1050"), kind("1300"), kind("2300"))
          == ("Bank", "General", "Non-trading receivable", "Non-trading payable"), f"{made} {[kind(c) for c in ('1250', '1050', '1300', '2300')]}")
    response = browser.post_raw("/accounting/chart-of-accounts/new", [("code", "6900"), ("name", "Sundry"), ("account_type", "Expense")])
    check("an account created with no kind at all (as older code would send it) is General", response.status_code == 302 and kind("6900") == "General")
    form = lambda code, account_type, account_kind: [("code", code), ("name", "X"), ("account_type", account_type), ("account_kind", account_kind)]
    refused(browser, "an Expense account marked Bank", "/accounting/chart-of-accounts/new", form("6910", "Expense", "Bank"), "not available for an account of type Expense")
    refused(browser, "a Liability marked Cash", "/accounting/chart-of-accounts/new", form("2910", "Liability", "Cash"), "not available")
    refused(browser, "a Liability marked Non-trading receivable", "/accounting/chart-of-accounts/new", form("2920", "Liability", "Non-trading receivable"), "not available")
    refused(browser, "an Asset marked Non-trading payable", "/accounting/chart-of-accounts/new", form("1920", "Asset", "Non-trading payable"), "not available")
    refused(browser, "a second 'Trade receivable' account", "/accounting/chart-of-accounts/new", form("1190", "Asset", "Trade receivable"), "cannot be given to another account")
    refused(browser, "an account of kind 'Banana'", "/accounting/chart-of-accounts/new", form("1930", "Asset", "Banana"), "Kind must be one of")
    check("the new bank account 1250 (a code outside 1000-1099) is offered on payments and receipts; 1050 Security Deposit (inside that range, but General) is not",
          money_boxes() == (["1010", "1250", "1000"], ["1010", "1250", "1000"]), str(money_boxes()))
    from accounting.services import list_cash_and_bank_accounts
    conn = psycopg2.connect(SCRATCH_URL)
    listed = [(code, account_kind) for _, code, _, account_kind in list_cash_and_bank_accounts(conn.cursor(), org)]
    conn.close()
    check("the ONE list every money screen takes its accounts from holds exactly: 1000 Cash, 1010 Bank, 1250 Bank",
          listed == [("1000", "Cash"), ("1010", "Bank"), ("1250", "Bank")], str(listed))
    page = browser.get("/accounting/chart-of-accounts").get_data(as_text=True)
    shown = dict(re.findall(r'data-account="([^"]+)" data-kind="([^"]+)"', page))
    check("the Chart of Accounts shows every account's kind and an Edit button",
          shown == dict(q("SELECT code, account_kind FROM chart_of_accounts WHERE organization_id = %s", (org,)))
          and page.count(">Edit</a>") == len(shown) == 19, f"{len(shown)} accounts, {page.count('>Edit</a>')} edit buttons")

    # ---- journal entry: General account column / Partner account column --------
    general, partner, data = journal_entry_choices(browser)
    check("journal entry, General account box: every ordinary account incl. bank and cash - but NOT 1100, 2000 or the director / employee accounts",
          general == ["1000", "1010", "1050", "1150", "1160", "1250", "2100", "2110", "2200", "3000", "3100", "4000", "5000", "6000", "6900"], str(general))
    check("journal entry, Partner account box: customers and vendors BY NAME, then one line per director / employee account",
          partner == {"Customers": [(f"customer:{customer}", "Kind Customer")], "Vendors": [(f"vendor:{vendor}", "Kind Vendor")],
                      "Non-trading partners (directors, employees)": [(f"account:{acc('1300')}", "1300 - Advance to Employee - Bilal Ahmed"),
                                                                    (f"account:{acc('2300')}", "2300 - Loan from Director - Kamran Sheikh")]}, str(partner))
    check("...and the page says where a customer or vendor line will be posted (1100 - Trade Receivables / 2000 - Trade Creditors)",
          [group["posts_to"] for group in data["partner"]] == ["1100 - Trade Receivables", "2000 - Trade Creditors", None])

    def entry(reference, *lines):
        """lines: (general account id or '', partner choice or '', debit, credit)"""
        rows = []
        for general_id, partner_choice, debit, credit in lines:
            rows += [("account_id[]", general_id), ("partner_account[]", partner_choice), ("label[]", reference), ("debit[]", debit), ("credit[]", credit)]
        return [("reference", reference), ("accounting_date", "2026-11-10")] + rows

    def saved(reference, *lines):
        response = browser.submit("/accounting/journal-entries/new", {"reference": reference, "accounting_date": "2026-11-10"}, rows=entry(reference, *lines)[2:])
        if response.status_code != 302:
            return None, error_box(response) or response.status_code
        header = one("SELECT max(journal_header_id) FROM journal_headers WHERE organization_id = %s", (org,))
        written = q("""SELECT c.code, l.partner_name, l.debit, l.credit FROM journal_lines l JOIN chart_of_accounts c ON c.account_id = l.account_id
                       WHERE l.journal_header_id = %s ORDER BY l.line_order""", (header,))
        browser.submit(f"/accounting/journal-entries/{header}", action_contains="/post")
        return header, written

    header, written = saved("Price difference charged", ("", f"customer:{customer}", "5000", ""), (acc("4000"), "", "", "5000"))
    check("a line with the customer picked is posted to 1100 Trade Receivables with the customer's name on it:  Dr 1100 [Kind Customer] 5,000 / Cr 4000 5,000",
          written == [("1100", "Kind Customer", D("5000"), D("0")), ("4000", None, D("0"), D("5000"))], str(written))
    page = browser.get(f"/accounting/journal-entries/{header}").get_data(as_text=True)
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip() for cell in re.findall(r'<td data-column="partner">(.*?)</td>', page, re.S)]
    check("...and the saved entry shows it under Partner account: 'Kind Customer - Customer, posted to 1100 — Trade Receivables'",
          cells == ["Kind Customer Customer · posted to 1100 — Trade Receivables"] and 'data-column="general">4000 — Sales Revenue<' in page, str(cells))
    header, written = saved("Expense claim accrued", (acc("6000"), "", "3000", ""), ("", f"vendor:{vendor}", "", "3000"))
    check("a line with the vendor picked is posted to 2000 Trade Creditors with the vendor's name:  Dr 6000 3,000 / Cr 2000 [Kind Vendor] 3,000",
          written == [("6000", None, D("3000"), D("0")), ("2000", "Kind Vendor", D("0"), D("3000"))], str(written))
    header, written = saved("Loan received from director", (acc("1250"), "", "100000", ""), ("", f"account:{acc('2300')}", "", "100000"))
    check("a director's account is picked under Partner account and posted to itself:  Dr 1250 100,000 / Cr 2300 100,000",
          written == [("1250", None, D("100000"), D("0")), ("2300", None, D("0"), D("100000"))], str(written))
    page = browser.get(f"/accounting/journal-entries/{header}").get_data(as_text=True)
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip() for cell in re.findall(r'<td data-column="partner">(.*?)</td>', page, re.S)]
    check("...and is shown under Partner account as '2300 — Loan from Director - Kamran Sheikh, Non-trading payable'",
          cells == ["2300 — Loan from Director - Kamran Sheikh Non-trading payable"], str(cells))
    header, written = saved("Salary advance paid in cash", ("", f"account:{acc('1300')}", "2000", ""), (acc("1000"), "", "", "2000"))
    check("an employee's account likewise:  Dr 1300 2,000 / Cr 1000 2,000",
          written == [("1300", None, D("2000"), D("0")), ("1000", None, D("0"), D("2000"))], str(written))
    header, written = saved("Security deposit paid", (acc("1050"), "", "500", ""), (acc("1010"), "", "", "500"))
    check("an entry between two general accounts still works as before:  Dr 1050 500 / Cr 1010 500",
          written == [("1050", None, D("500"), D("0")), ("1010", None, D("0"), D("500"))], str(written))

    other_customer = ids["customer"]          # belongs to the first company
    je = "/accounting/journal-entries/new"
    refused(browser, "journal line with 1100 Trade Receivables picked directly as the general account", je,
            entry("x", (acc("1100"), "", "10", ""), (acc("4000"), "", "", "10")), "pick the customer under Partner account")
    refused(browser, "journal line with 2000 Trade Creditors picked directly as the general account", je,
            entry("x", (acc("6000"), "", "10", ""), (acc("2000"), "", "", "10")), "pick the vendor under Partner account")
    refused(browser, "journal line with the director's account in the General account box", je,
            entry("x", (acc("6000"), "", "10", ""), (acc("2300"), "", "", "10")), "Pick it under Partner account")
    refused(browser, "journal line with BOTH boxes filled", je,
            entry("x", (acc("6000"), f"vendor:{vendor}", "10", ""), (acc("1000"), "", "", "10")), "one in each box")
    refused(browser, "journal line for a customer of ANOTHER company", je,
            entry("x", ("", f"customer:{other_customer}", "10", ""), (acc("4000"), "", "", "10")), "not one of this company's active customers")
    refused(browser, "journal line for vendor number 999999", je,
            entry("x", (acc("6000"), "", "10", ""), ("", "vendor:999999", "", "10")), "not one of this company's active vendors")
    refused(browser, "journal line whose partner is 'banana'", je,
            entry("x", ("", "banana", "10", ""), (acc("4000"), "", "", "10")), "pick the partner from the Partner account list")
    refused(browser, "journal line with 6000 General Expenses sent in the Partner account box", je,
            entry("x", ("", f"account:{acc('6000')}", "10", ""), (acc("1000"), "", "", "10")), "not a partner account")
    refused(browser, "journal line with 1100 sent in the Partner account box as an account", je,
            entry("x", ("", f"account:{acc('1100')}", "10", ""), (acc("4000"), "", "", "10")), "by name under Partner account")
    refused(browser, "journal line with another company's director account", je,
            entry("x", ("", f"account:{account('1010', 1)}", "10", ""), (acc("1000"), "", "", "10")), "Partner account list")
    response = browser.post_raw(je, entry("kept", ("", f"customer:{customer}", "750", ""), (acc("4000"), "", "", "700")))
    page = response.get_data(as_text=True)
    check("a refused entry comes back with its partner choice still filled in",
          f'"partner_account": "customer:{customer}"' in page and '"debit": "750"' in page)

    # ---- the dashboard counts cash by kind, and receivables by partner ---------
    def dashboard():
        page = browser.get("/").get_data(as_text=True)
        cash_rows = re.findall(r'data-cash-account="(\d+)".*?class="amount[^"]*">([^<]*)<', page, re.S)
        partners = re.findall(r'data-partner="(\w+)" data-value="([^"]*)">.*?class="name">([^<]*)<', page, re.S)
        return dashboard_figures(page), cash_rows, partners

    figures, cash_rows, partners = dashboard()
    check("dashboard Cash & bank counts the accounts marked Bank or Cash - 1000, 1010 and 1250 = 97,500.00 - and not 1050, although its code is 'in the cash range'",
          figures["cash"][0] == D("97500") and cash_rows == [("1000", "-2,000.00"), ("1010", "-500.00"), ("1250", "100,000.00")], f"{figures['cash'][0]} {cash_rows}")
    check("...and what was posted through the Partner account column shows up by name: Kind Customer owes 5,000.00, Kind Vendor is owed 3,000.00",
          partners == [("receivable", "5000.00", "Kind Customer"), ("payable", "3000.00", "Kind Vendor")]
          and figures["receivables"][0] == D("5000") and figures["payables"][0] == D("3000"), str(partners))

    # ---- editing an account -----------------------------------------------------
    edit = lambda code, **changes: browser.submit(f"/accounting/chart-of-accounts/{acc(code)}/edit", changes, has="name")
    response = edit("1050", account_kind="Bank")
    figures, cash_rows, partners = dashboard()
    check("Edit: marking 1050 as Bank puts it on payments and receipts, and into Cash & bank (now 98,000.00)",
          response.status_code == 302 and kind("1050") == "Bank" and money_boxes()[0] == ["1010", "1050", "1250", "1000"]
          and figures["cash"][0] == D("98000"), f"{kind('1050')} {money_boxes()[0]} {figures['cash'][0]}")
    response = edit("1050", account_kind="General", name="Security Deposit - Landlord")
    check("Edit: setting it back to General (and renaming it) takes it out again",
          response.status_code == 302 and kind("1050") == "General" and money_boxes()[0] == ["1010", "1250", "1000"]
          and one("SELECT name FROM chart_of_accounts WHERE account_id = %s", (acc("1050"),)) == "Security Deposit - Landlord")
    response = edit("1250", status="inactive")
    figures, cash_rows, partners = dashboard()
    check("Edit: an INACTIVE bank account is no longer offered on payments, receipts or journal entries - but its 100,000.00 is still counted",
          response.status_code == 302 and money_boxes() == (["1010", "1000"], ["1010", "1000"])
          and "1250" not in journal_entry_choices(browser)[0] and figures["cash"][0] == D("97500"),
          f"{money_boxes()} {figures['cash'][0]}")
    refused(browser, "a payment 'paid from' the inactive bank account 1250", "/vendors/payments/new", pay(acc("1250")), "is inactive")
    refused(browser, "a journal line on the inactive bank account 1250", je, entry("x", (acc("1250"), "", "10", ""), (acc("1000"), "", "", "10")), "is inactive")
    edit("1250", status="active")
    edit("2300", status="inactive")
    check("Edit: an inactive director account disappears from the Partner account box",
          [label for value, label in journal_entry_choices(browser)[1]["Non-trading partners (directors, employees)"]] == ["1300 - Advance to Employee - Bilal Ahmed"])
    refused(browser, "a journal line on the inactive director account", je, entry("x", (acc("6000"), "", "10", ""), ("", f"account:{acc('2300')}", "", "10")), "is inactive")
    edit("2300", status="active")
    check("...and both are back once made active again",
          money_boxes()[0] == ["1010", "1250", "1000"] and len(journal_entry_choices(browser)[1]["Non-trading partners (directors, employees)"]) == 2)
    response = browser.post_raw(f"/accounting/chart-of-accounts/{acc('1100')}/edit",
                                [("name", "Trade Debtors"), ("account_kind", "General"), ("status", "inactive")])
    check("Edit: 1100 can be renamed, but it stays 'Trade receivable' and active whatever is sent",
          response.status_code == 302 and q("SELECT name, account_kind, is_active FROM chart_of_accounts WHERE account_id = %s", (acc("1100"),))
          == [("Trade Debtors", "Trade receivable", True)])
    edit_path = f"/accounting/chart-of-accounts/{acc('6000')}/edit"
    refused(browser, "Edit: marking 6000 General Expenses as Bank", edit_path, [("name", "General Expenses"), ("account_kind", "Bank"), ("status", "active")], "not available")
    refused(browser, "Edit: marking 6000 as Trade payable", edit_path, [("name", "General Expenses"), ("account_kind", "Trade payable"), ("status", "active")], "cannot be given to another account")
    refused(browser, "Edit: a blank name", edit_path, [("name", "  "), ("account_kind", "General"), ("status", "active")], "Name is required")
    refused(browser, "Edit: a status that is neither Active nor Inactive", edit_path, [("name", "General Expenses"), ("account_kind", "General"), ("status", "maybe")], "Active or Inactive")
    theirs = account("1010", 1)
    before = q("SELECT name, account_kind, is_active FROM chart_of_accounts WHERE account_id = %s", (theirs,))
    response = browser.post_raw(f"/accounting/chart-of-accounts/{theirs}/edit", [("name", "Hijacked"), ("account_kind", "General"), ("status", "inactive")])
    check("Edit: another company's account cannot be opened or changed (404)",
          response.status_code == 404 and browser.get(f"/accounting/chart-of-accounts/{theirs}/edit").status_code == 404
          and q("SELECT name, account_kind, is_active FROM chart_of_accounts WHERE account_id = %s", (theirs,)) == before)
    browser.post_raw(f"/organizations/switch/{ids['org2']}", [("org_id", str(ids["org2"]))])


def _upgrade_of_an_older_database():
    """A database made BEFORE accounts had a kind is started with today's code."""
    old_name = SCRATCH_NAME + "_old"
    old_url = _url_for_database(TEST_DATABASE_URL, old_name)

    def old(sql, args=()):
        conn = psycopg2.connect(old_url)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, args)
            rows = cursor.fetchall() if cursor.description else None
            conn.commit()
            return rows
        finally:
            conn.close()

    def start_the_app():
        return subprocess.run([sys.executable, "-c", "import app"], cwd=REPO_ROOT, capture_output=True, text=True,
                              env={**os.environ, "DATABASE_URL": old_url})

    ledger = "SELECT count(*), sum(debit), sum(credit), md5(string_agg(journal_line_id || ':' || account_id || ':' || debit || ':' || credit, '|' ORDER BY journal_line_id)) FROM journal_lines"
    _admin(f'CREATE DATABASE "{old_name}" TEMPLATE "{SCRATCH_NAME}"')
    try:
        # exactly the shape an older database has: the same tables, without the new column
        old("ALTER TABLE chart_of_accounts DROP COLUMN account_kind")
        old("""INSERT INTO chart_of_accounts (organization_id, code, name, account_type) VALUES
               (1, '1005', 'Petty Cash', 'Asset'), (1, '1020', 'Meezan Bank - Current', 'Asset'), (1, '1030', 'Cash at Bank - HBL', 'Asset'),
               (1, '1210', 'Allied Bank - Savings', 'Asset'), (1, '1040', 'Suspense', 'Liability'), (1, '10100', 'Long code', 'Asset')""")
        old("UPDATE chart_of_accounts SET name = 'HBL Main Account' WHERE organization_id = 1 AND code = '1010'")
        # everything about every account EXCEPT its kind, and the whole ledger
        accounts = "SELECT account_id, organization_id, code, name, account_type, allow_reconciliation, is_active FROM chart_of_accounts WHERE account_id <= %s ORDER BY 1"
        last_account = old("SELECT max(account_id) FROM chart_of_accounts")[0][0]
        had_accounts = [row[0] for row in old("SELECT DISTINCT organization_id FROM chart_of_accounts")]
        before = (old(ledger), old(accounts, (last_account,)), old("SELECT count(*) FROM journal_headers"))
        started = start_the_app()
        kinds = dict(old("SELECT code, account_kind FROM chart_of_accounts WHERE organization_id = 1 AND account_kind <> 'General'"))
        check("an older database (no kinds yet) starts with today's code", started.returncode == 0, started.stderr[-400:])
        check("...its control accounts are recognised by code: 1100 Trade receivable, 2000 Trade payable", (kinds.get("1100"), kinds.get("2000")) == ("Trade receivable", "Trade payable"), str(kinds))
        check("...the accounts the dashboard used to count as cash & bank (Asset, codes 1000-1099) are marked: "
              "1000 Cash in Hand and 1005 Petty Cash as Cash; 1010 (renamed 'HBL Main Account'), 1020 and 1030 'Cash at Bank - HBL' as Bank",
              {code: kinds.get(code) for code in ("1000", "1005", "1010", "1020", "1030")}
              == {"1000": "Cash", "1005": "Cash", "1010": "Bank", "1020": "Bank", "1030": "Bank"}, str(kinds))
        check("...nothing else is guessed: a bank account kept under another code (1210), a liability coded 1040 and code 10100 stay General",
              sorted(kinds) == ["1000", "1005", "1010", "1020", "1030", "1100", "2000"], str(sorted(kinds)))
        check("...every company in it is treated the same way",
              old("SELECT count(DISTINCT organization_id) FROM chart_of_accounts WHERE account_kind = 'Trade receivable'")
              == old("SELECT count(DISTINCT organization_id) FROM chart_of_accounts WHERE code = '1100'"))
        check("...and the upgrade changed no journal entry, no amount, and nothing about any existing account except its kind",
              (old(ledger), old(accounts, (last_account,)), old("SELECT count(*) FROM journal_headers")) == before)
        # (starting the app also gives a company that had NO accounts its starter chart - that is not new)
        check("...and added no account to a company that already had its accounts",
              old("SELECT count(*) FROM chart_of_accounts WHERE account_id > %s AND organization_id = ANY(%s)", (last_account, had_accounts)) == [(0,)])
        old("UPDATE chart_of_accounts SET account_kind = 'General' WHERE organization_id = 1 AND code = '1030'")
        old("UPDATE chart_of_accounts SET account_kind = 'Bank' WHERE organization_id = 1 AND code = '1210'")
        again = start_the_app()
        check("a kind changed by hand afterwards is NOT put back at the next start (the marking happens once)",
              again.returncode == 0 and dict(old("SELECT code, account_kind FROM chart_of_accounts WHERE organization_id = 1 AND code IN ('1030', '1210')"))
              == {"1030": "General", "1210": "Bank"})
    finally:
        try:
            _admin(f'DROP DATABASE IF EXISTS "{old_name}" WITH (FORCE)')
        except Exception as error:
            print(f"\n(could not drop scratch database {old_name}: {error})")


# ---------------------------------------------------------------------
# 11. The customer card
# ---------------------------------------------------------------------
CARD_FIELDS = ["customer_no", "name", "is_company", "nature_of_supply", "ntn", "strn", "cnic", "address", "street2", "city",
               "state", "zip_code", "country", "region", "territory", "category_parent", "category", "lic_number",
               "lic_expiry_date", "email", "phone", "credit_limit", "is_active"]
LEDGER_FINGERPRINT = """SELECT count(*), sum(debit), sum(credit),
                               md5(string_agg(journal_line_id || ':' || account_id || ':' || debit || ':' || credit || ':' || coalesce(partner_name, ''),
                                              '|' ORDER BY journal_line_id))
                        FROM journal_lines"""


def customer_list(browser, search=None):
    """The customer list as the page shows it: (rows, the count line, tick-boxes, the page).
    rows       [{column key: text, 'inactive': bool, 'link': the Edit address}], top to bottom
    tick-boxes [(column key, label, ticked)] of the Columns menu, top to bottom"""
    import html
    from urllib.parse import quote
    page = browser.get("/customers/" + ("?q=" + quote(search) if search is not None else "")).get_data(as_text=True)
    text = lambda fragment: html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()
    rows = []
    body = re.search(r"<tbody>(.*?)</tbody>", page, re.S)
    for row in re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S) if body else []:
        cells = {key: text(inner) for key, inner in re.findall(r'<td[^>]*data-col="([a-z_]+)"[^>]*>(.*?)</td>', row, re.S)}
        name = re.search(r'<a href="([^"]+)" class="lp-clip"[^>]*>(.*?)</a>', row, re.S)
        cells.update(name=text(name.group(2)), link=name.group(1), inactive="Inactive</span>" in row)
        rows.append(cells)
    count = re.search(r'id="customer-count"[^>]*>(.*?)</span>', page, re.S)
    boxes = [(key, label.strip(), bool(ticked)) for key, ticked, label in
             re.findall(r'<label><input type="checkbox" data-col="([a-z_]+)"\s*(checked)?\s*>\s*([^<]+?)\s*</label>', page)]
    return rows, (text(count.group(1)) if count else None), boxes, page


def customer_form(browser, path):
    """{box: value} of the customer form on a page, plus the page itself."""
    page = browser.get(path).get_data(as_text=True)
    form = [f for f in forms_in(page) if "partner_type" in f["names"]]
    return (dict(form[0]["fields"]) if form else None), page


def test_11_customer_card(browser, ids):
    section("11. The customer card: the ID, the boxes, Edit, the list and its search")
    _customer_card_checks(browser, ids)
    _customers_of_an_older_database(ids)


def _customer_card_checks(browser, ids):
    from datetime import date
    from customers.services import LIST_COLUMNS, NAME_LOCKED_MESSAGE, NAME_MAX_LENGTH, TEXT_FIELDS

    response = browser.submit("/organizations/new", {"name": "Card Co"})
    org = one("SELECT organization_id FROM organizations WHERE name = 'Card Co'")
    check("a new company 'Card Co' is created for these checks", response.status_code == 302 and org is not None)
    ids["card_org"] = org
    ledger_at_the_start = q(LEDGER_FINGERPRINT)

    def card(name, organization=None):
        rows = q("SELECT customer_id, " + ", ".join(CARD_FIELDS) + " FROM customers WHERE organization_id = %s AND name = %s ORDER BY customer_id",
                 (organization or org, name))
        return dict(zip(["customer_id"] + CARD_FIELDS, rows[0])) if rows else None

    def card_by_id(customer_id):
        rows = q("SELECT customer_id, organization_id, created_at, " + ", ".join(CARD_FIELDS) + " FROM customers WHERE customer_id = %s", (customer_id,))
        return rows[0] if rows else None

    def new(name, **boxes):
        response = browser.submit("/customers/new", dict({"name": name}, **boxes), has="partner_type")
        return response, card(name)

    def edit(customer_id, **boxes):
        return browser.submit(f"/customers/{customer_id}/edit", boxes, has="partner_type")

    # ---- the form ------------------------------------------------------------
    boxes, page = customer_form(browser, "/customers/new")
    wanted = {"name", "partner_type", "email", "phone", "address", "street2", "city", "state", "zip_code", "country", "region", "territory",
              "lic_number", "lic_expiry_date", "ntn", "strn", "cnic", "category_parent", "category", "nature_of_supply", "credit_limit"}
    check("New Customer has every box of the card: Person / Company, name, email, phone, the address in parts, Region, Territory, "
          "LIC Number, LIC Expiry Date, NTN, STRN, CNIC, Category Parent, Category, Nature of supply, Credit limit",
          boxes is not None and wanted <= set(boxes) and "status" not in boxes, str(sorted(wanted - set(boxes or {}))))
    check("...it starts as a Company with Supplies and a credit limit of 0.00, and says the ID is given automatically",
          boxes is not None and (boxes.get("partner_type"), boxes.get("nature_of_supply"), boxes.get("credit_limit")) == ("company", "Supplies", "0.00")
          and "Given automatically when you save" in page)

    # ---- saving ----------------------------------------------------------------
    full = {"partner_type": "company", "email": "accounts@kasurtraders.example", "phone": "0300-5550101",
            "address": "Canal Road", "street2": "Near Grain Market", "city": "Kasur", "state": "Punjab", "zip_code": "55050",
            "country": "Pakistan", "region": "KSR", "territory": "KASUR TOWN", "category_parent": "Distribution", "category": "Dealer",
            "lic_number": "L/101/PW/TST", "lic_expiry_date": "2027-03-05", "ntn": "1234567-8", "strn": "1100223344556",
            "cnic": "35202-1234567-1", "nature_of_supply": "Services", "credit_limit": "40000000"}
    response, first = new("Kasur Traders", **full)
    expected = dict(full, name="Kasur Traders", is_company=True, lic_expiry_date=date(2027, 3, 5), credit_limit=D("40000000.00"),
                    customer_no=1, is_active=True)
    del expected["partner_type"]
    check("a customer saved with every box filled keeps every value, and is given ID 1 (the company's first customer)",
          response.status_code == 302 and first is not None and {key: first[key] for key in expected} == expected,
          error_box(response) or str(first))
    response, second = new("  Multan   Agro  Centre ", partner_type="person", credit_limit="")
    second = card("Multan Agro Centre")
    check("a customer saved with only a name is fine too: stray spaces in the name are tidied, Person is kept, every empty box is saved as empty, "
          "the credit limit is 0.00, and the ID is 2",
          response.status_code == 302 and second is not None and second["customer_no"] == 2 and second["is_company"] is False
          and second["credit_limit"] == D("0.00") and second["is_active"] is True and second["nature_of_supply"] == "Supplies"
          and all(second[field] is None for field, _label, _limit in TEXT_FIELDS), str(second))
    response, third = new("Lahore Seed House", region="LHR", territory="LAHORE", credit_limit="2500000.555", lic_expiry_date="2026-12-31")
    check("the third customer is ID 3, and its credit limit 2,500,000.555 is saved as 2,500,000.56 (rounded like every amount)",
          third is not None and third["customer_no"] == 3 and third["credit_limit"] == D("2500000.56"), str(third))
    numbering = q("SELECT organization_id, array_agg(customer_no ORDER BY customer_id) FROM customers GROUP BY 1 ORDER BY 1")
    check(f"in every one of the {len(numbering)} companies that has customers, the IDs run 1, 2, 3... in the order the customers were created "
          "(each company counts for itself)",
          all(numbers == list(range(1, len(numbers) + 1)) for _organization, numbers in numbering), str(numbering))

    # ---- the list --------------------------------------------------------------
    rows, count, tick_boxes, page = customer_list(browser)
    check("the list shows the three customers by name, each with its ID:  1 Kasur Traders,  3 Lahore Seed House,  2 Multan Agro Centre",
          [(row["customer_no"], row["name"]) for row in rows] == [("1", "Kasur Traders"), ("3", "Lahore Seed House"), ("2", "Multan Agro Centre")]
          and count == "3 customers", f"{[(row.get('customer_no'), row['name']) for row in rows]} / {count}")
    kasur = rows[0] if rows else {}
    check("...amounts carry thousands separators and 2 decimals (40,000,000.00) and dates read 05 Mar 2027",
          (kasur.get("credit_limit"), kasur.get("lic_expiry_date")) == ("40,000,000.00", "05 Mar 2027"), str(kasur))
    check("...and the other boxes are shown as saved: region KSR, territory KASUR TOWN, LIC number, category parent, country, email, phone",
          [kasur.get(key) for key in ("region", "territory", "lic_number", "category_parent", "country", "email", "phone")]
          == ["KSR", "KASUR TOWN", "L/101/PW/TST", "Distribution", "Pakistan", "accounts@kasurtraders.example", "0300-5550101"], str(kasur))
    check("...an empty box is shown empty (never the word None)",
          rows and all(rows[2].get(key) == "" for key in ("email", "phone", "region", "territory", "lic_number", "lic_expiry_date")) and ">None<" not in page)
    headers = re.findall(r'<th class="[^"]*"\s*(?:data-col="([a-z_]+)"(\s+hidden)?)?\s*>([^<]+)</th>', page)
    shown = [label for _key, hidden, label in headers if not hidden]
    check("the columns shown at first are: ID, Name, Credit Limit, Email, Phone, Region, Territory, LIC Number, LIC Expiry Date, Category Parent, Country",
          shown == ["ID", "Name", "Credit Limit", "Email", "Phone", "Region", "Territory", "LIC Number", "LIC Expiry Date", "Category Parent", "Country"],
          str(shown))
    expected_boxes = [(column["key"], column["label"], column["shown"]) for column in LIST_COLUMNS if not column.get("fixed")]
    check(f"the tick-box menu has one box for each of the other {len(expected_boxes)} columns, ticked exactly for the ones shown "
          "(NTN, Street, City, State, STRN, CNIC, Nature of Supply and Category start unticked); Name has no box - it always shows",
          tick_boxes == expected_boxes and len(expected_boxes) == 18 and "name" not in [key for key, _label, _ticked in tick_boxes], str(tick_boxes))
    hidden_cells = set(re.findall(r'<td data-col="([a-z_]+)" hidden>', page))
    check("...and every row carries the unticked columns too, hidden, so ticking a box can show them without loading the page again",
          hidden_cells == {key for key, _label, ticked in expected_boxes if not ticked}, str(sorted(hidden_cells)))
    check("each name on the list opens that customer's Edit screen",
          [row["link"] for row in rows] == [f"/customers/{c['customer_id']}/edit" for c in (first, third, second)], str([row["link"] for row in rows]))

    # ---- search ----------------------------------------------------------------
    found = lambda text: ([row["name"] for row in customer_list(browser, text)[0]], customer_list(browser, text)[1])
    check("search finds part of a name, whatever the capitals:  'SEED'  ->  Lahore Seed House (1 of 3 customers)",
          found("SEED") == (["Lahore Seed House"], "1 of 3 customers"), str(found("SEED")))
    check("...a territory or a region:  'kasur town'  ->  Kasur Traders;   'lhr'  ->  Lahore Seed House",
          found("kasur town")[0] == ["Kasur Traders"] and found("lhr")[0] == ["Lahore Seed House"], f"{found('kasur town')} {found('lhr')}")
    check("...or an ID:  '2'  ->  Multan Agro Centre, whose only 2 is its ID (and Kasur Traders, whose NTN has a 2 in it) - not Lahore Seed House",
          found("2")[0] == ["Kasur Traders", "Multan Agro Centre"], str(found("2")))
    check("...and every typed word must be found:  'traders nosuchword' -> nobody;  'traders distribution' -> Kasur Traders (its name and its category parent)",
          found("traders nosuchword")[0] == [] and found("traders distribution")[0] == ["Kasur Traders"],
          f"{found('traders nosuchword')} {found('traders distribution')}")
    nothing = customer_list(browser, "zzzz")
    check("a search that finds nobody says so, and offers Clear",
          nothing[0] == [] and "No customer matches" in nothing[3] and ">Clear</a>" in nothing[3] and nothing[1] == "0 of 3 customers")
    check("the characters % and _ are searched as themselves ('%' and '_' find nobody, instead of everybody)",
          found("%")[0] == [] and found("_")[0] == [] and found("a_a")[0] == [], f"{found('%')} {found('_')}")
    hostile = [browser.get("/customers/?q=" + text).status_code for text in
               ("x" * 5000, "%25" * 300, "%00", "%27%3B%20DROP%20TABLE%20customers%3B--", "a+b+c+d+e+f+g+h+i+j", "%5C", "%F0%9F%98%80")]
    check(f"odd search text never crashes the list ({len(hostile)} tries: 5,000 letters, 300 percent signs, a NUL, SQL text, ten words, a backslash, an emoji)",
          hostile == [200] * len(hostile) and one("SELECT count(*) FROM customers WHERE organization_id = %s", (org,)) == 3, str(hostile))

    # ---- wrong input is refused, nothing is saved, nothing typed is lost ------------
    refused(browser, "new customer with no name", "/customers/new", [("name", "")], "Customer name is required")
    refused(browser, "new customer whose name is only spaces", "/customers/new", [("name", "     ")], "Customer name is required")
    refused(browser, f"a name of {NAME_MAX_LENGTH + 1} characters", "/customers/new", [("name", "N" * (NAME_MAX_LENGTH + 1))], "too long")
    refused(browser, "an NTN of 31 characters (this used to end in 'Internal Server Error')", "/customers/new",
            [("name", "Long NTN"), ("ntn", "9" * 31)], "NTN is too long (maximum 30")
    over = []
    for field, label, limit in TEXT_FIELDS:
        before = row_counts()
        response = browser.post_raw("/customers/new", [("name", f"Too long {field}"), (field, "7" * (limit + 1))])
        if not (response.status_code == 200 and f"{label} is too long (maximum {limit} characters)" in (error_box(response) or "") and row_counts() == before):
            over.append(field)
    check(f"each of the {len(TEXT_FIELDS)} text boxes refuses one character more than its column holds, with a message naming the box", not over, str(over))
    refused(browser, "email 'nobody'", "/customers/new", [("name", "Bad Email"), ("email", "nobody")], "does not look like an email")
    refused(browser, "phone 'call me'", "/customers/new", [("name", "Bad Phone"), ("phone", "call me")], "Phone must contain a number")
    refused(browser, "LIC Expiry Date 31-12-2026 (not a date the form can send)", "/customers/new",
            [("name", "Bad Date"), ("lic_expiry_date", "31-12-2026")], "not a valid date")
    refused(browser, "LIC Expiry Date in the year 9999", "/customers/new", [("name", "Far Date"), ("lic_expiry_date", "9999-12-31")], "between the years 1900 and 2200")
    refused(browser, "credit limit -1", "/customers/new", [("name", "Bad Limit"), ("credit_limit", "-1")], "cannot be negative")
    refused(browser, "credit limit 'abc'", "/customers/new", [("name", "Bad Limit"), ("credit_limit", "abc")], "must be a number")
    refused(browser, "credit limit 'NaN'", "/customers/new", [("name", "Bad Limit"), ("credit_limit", "NaN")], "must be a number")
    refused(browser, "credit limit 1,000,000,000,000 (13 digits - more than the column holds)", "/customers/new",
            [("name", "Bad Limit"), ("credit_limit", "1000000000000")], "too large")
    refused(browser, "credit limit 1e999999", "/customers/new", [("name", "Bad Limit"), ("credit_limit", "1e999999")], "too large")
    refused(browser, "neither Person nor Company", "/customers/new", [("name", "Bad Type"), ("partner_type", "robot")], "Person or Company")
    refused(browser, "nature of supply 'Goods'", "/customers/new", [("name", "Bad Nature"), ("nature_of_supply", "Goods")], "Supplies or Services")

    before = row_counts()
    response = browser.post_raw("/customers/new", [
        ("name", "Half Typed Customer"), ("partner_type", "person"), ("nature_of_supply", "Services"), ("email", "not-an-email"),
        ("territory", "SAHIWAL"), ("address", "Plot 9\nIndustrial Area"), ("lic_expiry_date", "2027-01-31"), ("credit_limit", "-5"), ("ntn", "7654321-0")])
    back = [f for f in forms_in(response.get_data(as_text=True)) if "partner_type" in f["names"]]
    back = dict(back[0]["fields"]) if back else {}
    check("a refused form comes back with BOTH mistakes listed and everything that was typed still in its box "
          "(name, Person, Services, the wrong email, territory, both address lines, the date, the wrong credit limit, NTN)",
          response.status_code == 200 and row_counts() == before
          and "does not look like an email" in (error_box(response) or "") and "cannot be negative" in (error_box(response) or "")
          and [back.get(key) for key in ("name", "partner_type", "nature_of_supply", "email", "territory", "address", "lic_expiry_date", "credit_limit", "ntn")]
          == ["Half Typed Customer", "person", "Services", "not-an-email", "SAHIWAL", "Plot 9\nIndustrial Area", "2027-01-31", "-5", "7654321-0"],
          f"{error_box(response)} / {back}")

    # ---- two customers cannot share a name (yet) --------------------------------------
    refused(browser, "a second 'Kasur Traders'", "/customers/new", [("name", "Kasur Traders")], "already exists (ID 1)")
    refused(browser, "...also as 'kasur   TRADERS ' (capitals and spaces do not make it a different customer)", "/customers/new",
            [("name", "kasur   TRADERS ")], "already exists (ID 1)")
    browser.post_raw("/organizations/switch/1", [("org_id", "1")])
    highest = one("SELECT max(customer_no) FROM customers WHERE organization_id = 1")
    response = browser.submit("/customers/new", {"name": "Kasur Traders", "region": "ONLY-IN-COMPANY-1"}, has="partner_type")
    other = card("Kasur Traders", 1)
    check("another company may have its own 'Kasur Traders'; it gets that company's next ID, not Card Co's",
          response.status_code == 302 and other is not None and other["customer_no"] == highest + 1 and other["customer_id"] != first["customer_id"],
          error_box(response) or str(other))
    browser.post_raw(f"/organizations/switch/{org}", [("org_id", str(org))])

    # ---- Edit ----------------------------------------------------------------------
    boxes, page = customer_form(browser, f"/customers/{first['customer_id']}/edit")
    check("Edit Customer opens with every box as it was saved, the ID shown, and a Status box",
          boxes is not None and dict(full, name="Kasur Traders", credit_limit="40000000.00", status="active").items() <= boxes.items()
          and re.search(r"<strong>1</strong>", page) is not None and "readonly" not in page, str(boxes))
    response = edit(first["customer_id"], name="Kasur Traders & Sons", partner_type="person", territory="KASUR CITY", credit_limit="1500000",
                    lic_expiry_date="", email="", category="Wholesaler", nature_of_supply="Supplies")
    changed = card("Kasur Traders & Sons")
    check("changes are saved: the new name (this customer has no entries yet), Person, territory, credit limit 1,500,000.00, category, nature; "
          "the emptied boxes (email, LIC expiry date) are empty; the boxes not touched keep their values",
          response.status_code == 302 and changed is not None and card("Kasur Traders") is None
          and (changed["is_company"], changed["territory"], changed["credit_limit"], changed["category"], changed["nature_of_supply"],
               changed["email"], changed["lic_expiry_date"]) == (False, "KASUR CITY", D("1500000.00"), "Wholesaler", "Supplies", None, None)
          and (changed["ntn"], changed["lic_number"], changed["city"], changed["phone"]) == ("1234567-8", "L/101/PW/TST", "Kasur", "0300-5550101"),
          error_box(response) or str(changed))
    check("...and the customer is still the same customer: same ID 1, same record",
          changed is not None and (changed["customer_no"], changed["customer_id"]) == (1, first["customer_id"]))
    kept = card_by_id(first["customer_id"])
    for description, fields, message in [
            ("Edit with the name emptied", [("name", " ")], "Customer name is required"),
            ("Edit with an NTN of 31 characters", [("name", "Kasur Traders & Sons"), ("ntn", "9" * 31)], "NTN is too long"),
            ("Edit with credit limit 'abc'", [("name", "Kasur Traders & Sons"), ("credit_limit", "abc")], "must be a number"),
            ("Edit with Status 'deleted'", [("name", "Kasur Traders & Sons"), ("status", "deleted")], "Status must be Active or Inactive"),
            ("Edit renaming it to the name of customer 3", [("name", "lahore seed house")], "already exists (ID 3)")]:
        refused(browser, description, f"/customers/{first['customer_id']}/edit", fields, message)
    response = browser.post_raw(f"/customers/{first['customer_id']}/edit", [
        ("name", "Kasur Traders & Sons"), ("territory", "TYPED TERRITORY"), ("phone", "0300-0000000"), ("credit_limit", "abc"), ("status", "inactive")])
    back = [f for f in forms_in(response.get_data(as_text=True)) if "partner_type" in f["names"]]
    back = dict(back[0]["fields"]) if back else {}
    check("a refused Edit comes back with what was just typed (territory, phone, the wrong credit limit, Inactive) - not with the saved values - "
          "and the boxes that were not sent still show what is saved (NTN, city)",
          response.status_code == 200 and "must be a number" in (error_box(response) or "")
          and [back.get(key) for key in ("territory", "phone", "credit_limit", "status", "ntn", "city")]
          == ["TYPED TERRITORY", "0300-0000000", "abc", "inactive", "1234567-8", "Kasur"], f"{error_box(response)} / {back}")
    check("...and after those six refusals the customer is exactly as it was", card_by_id(first["customer_id"]) == kept)
    response = edit(first["customer_id"], name="KASUR Traders & Sons")
    check("a name may be re-typed with different capitals (it is still the only customer of that name)",
          response.status_code == 302 and card("KASUR Traders & Sons") is not None, error_box(response))
    edit(first["customer_id"], name="Kasur Traders")
    check("everything up to here - new customers, edits, renames, refusals - wrote nothing to the journal: every journal line is as it was",
          q(LEDGER_FINGERPRINT) == ledger_at_the_start)

    # ---- a name with entries is locked ----------------------------------------------
    bank, revenue = account("1010", org), account("4000", org)
    browser.submit("/sales/products/new", {"name": "Card Product", "unit": "Units", "default_account_id": revenue})
    product = str(one("SELECT product_id FROM products WHERE organization_id = %s AND name = 'Card Product'", (org,)))
    tax = str(one("SELECT sales_tax_rate_id FROM sales_tax_rates WHERE organization_id = %s ORDER BY rate_percent LIMIT 1", (org,)))
    lines_named = lambda name: one("""SELECT count(*) FROM journal_lines l JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
                                      WHERE h.organization_id = %s AND l.partner_name = %s""", (org, name))

    def locked(customer_id):
        boxes, page = customer_form(browser, f"/customers/{customer_id}/edit")
        return NAME_LOCKED_MESSAGE in page and re.search(r'name="name"[^>]*\breadonly\b', page) is not None

    _, by_receipt = new("Receipt Customer", territory="OKARA")
    _, by_invoice = new("Draft Invoice Customer")
    _, by_journal = new("Journal Customer")
    _, free = new("No Entries Customer")
    check("a customer with no entries can still be renamed: the name box is open and there is no warning",
          not any(locked(c["customer_id"]) for c in (by_receipt, by_invoice, by_journal, free)))
    response = browser.submit("/customers/receipts/new", {"customer_id": str(by_receipt["customer_id"]), "amount": "1000", "receipt_date": "2026-11-13",
                                                          "deposit_to_account_id": bank}, has="customer_id")
    check("after a receipt from the customer, its name box is locked and the screen says why",
          response.status_code == 302 and lines_named("Receipt Customer") > 0 and locked(by_receipt["customer_id"]), error_box(response))
    response = browser.submit("/sales/invoices/new", {"customer_id": str(by_invoice["customer_id"]), "invoice_date": "2026-11-13"}, has="customer_id", rows=[
        ("product_id[]", product), ("label[]", "Card"), ("account_id[]", revenue), ("unit[]", "Units"),
        ("quantity[]", "1"), ("rate[]", "500"), ("discount_percent[]", "0"), ("sales_tax_rate_id[]", tax)])
    draft_status = one("SELECT status FROM sales_invoices WHERE organization_id = %s AND customer_id = %s", (org, by_invoice["customer_id"]))
    check("after an invoice that is still a DRAFT the name is locked too (its draft journal entry already carries the name)",
          response.status_code == 302 and draft_status == "Draft" and locked(by_invoice["customer_id"]), f"{error_box(response)} / {draft_status}")
    response = browser.submit("/accounting/journal-entries/new", {"reference": "Card lock", "accounting_date": "2026-11-13"}, rows=[
        ("account_id[]", ""), ("partner_account[]", f"customer:{by_journal['customer_id']}"), ("label[]", "x"), ("debit[]", "750"), ("credit[]", ""),
        ("account_id[]", revenue), ("partner_account[]", ""), ("label[]", "x"), ("debit[]", ""), ("credit[]", "750")])
    check("after a journal entry made with the customer in the Partner account column, the name is locked too",
          response.status_code == 302 and lines_named("Journal Customer") == 1 and locked(by_journal["customer_id"]), error_box(response))
    check("...while the customer without entries is still free", not locked(free["customer_id"]))

    kept = card_by_id(by_receipt["customer_id"])
    ledger_with_the_entries = q(LEDGER_FINGERPRINT)
    for customer, label in ((by_receipt, "a receipt"), (by_invoice, "a draft invoice"), (by_journal, "a journal entry")):
        refused(browser, f"renaming the customer that has {label} (sent straight to the server, past the locked box)",
                f"/customers/{customer['customer_id']}/edit", [("name", customer["name"] + " Renamed")], "cannot be changed")
    refused(browser, "...not even a change of capitals", f"/customers/{by_receipt['customer_id']}/edit", [("name", "RECEIPT CUSTOMER")], "cannot be changed")
    check("...the name is unchanged, and no journal line was renamed",
          card_by_id(by_receipt["customer_id"]) == kept and lines_named("Receipt Customer") > 0 and lines_named("Receipt Customer Renamed") == 0)
    response = edit(by_receipt["customer_id"], territory="SAHIWAL", phone="0300-5550202", credit_limit="900000")
    after = card("Receipt Customer")
    check("everything else on a locked customer can still be changed (territory, phone, credit limit)",
          response.status_code == 302 and (after["territory"], after["phone"], after["credit_limit"]) == ("SAHIWAL", "0300-5550202", D("900000.00")),
          error_box(response) or str(after))
    response = browser.post_raw(f"/customers/{by_receipt['customer_id']}/edit", [("name", "  Receipt   Customer "), ("city", "Sahiwal")])
    check("...and stray spaces around a locked name are not mistaken for a new name",
          response.status_code == 302 and card("Receipt Customer")["city"] == "Sahiwal", error_box(response) if response.status_code == 200 else response.status_code)
    check("...and editing customers that have entries left every journal line as it was (amounts, accounts and the names on the lines)",
          q(LEDGER_FINGERPRINT) == ledger_with_the_entries)
    response = edit(free["customer_id"], name="Renamed Before Any Entry")
    browser.submit("/customers/receipts/new", {"customer_id": str(free["customer_id"]), "amount": "300", "receipt_date": "2026-11-13",
                                               "deposit_to_account_id": bank}, has="customer_id")
    check("a customer renamed BEFORE its first entry has all its entries under the new name - its balance is in one piece",
          response.status_code == 302 and lines_named("Renamed Before Any Entry") > 0 and lines_named("No Entries Customer") == 0)

    browser.post_raw("/organizations/switch/1", [("org_id", "1")])
    response = browser.submit("/customers/new", {"name": "Receipt Customer"}, has="partner_type")
    namesake = card("Receipt Customer", 1)
    open_before = namesake is not None and not locked(namesake["customer_id"])
    renamed = browser.submit(f"/customers/{namesake['customer_id']}/edit", {"name": "Receipt Customer Of Company 1"}, has="partner_type")
    check("the lock looks at the customer's OWN company only: company 1's new 'Receipt Customer' has no entries and can be renamed, "
          "although Card Co's 'Receipt Customer' has a receipt",
          response.status_code == 302 and open_before and renamed.status_code == 302 and card("Receipt Customer Of Company 1", 1) is not None,
          error_box(renamed) if renamed.status_code == 200 else f"{response.status_code} {open_before} {renamed.status_code}")
    browser.post_raw(f"/organizations/switch/{org}", [("org_id", str(org))])

    # a customer saved by the older New Customer screen, which kept extra spaces INSIDE a name
    q("""INSERT INTO customers (organization_id, name, customer_no)
         VALUES (%s, 'Older  Copy   Customer', (SELECT max(customer_no) + 1 FROM customers WHERE organization_id = %s))""", (org, org))
    spaced = card("Older  Copy   Customer")
    browser.submit("/customers/receipts/new", {"customer_id": str(spaced["customer_id"]), "amount": "200", "receipt_date": "2026-11-13",
                                               "deposit_to_account_id": bank}, has="customer_id")
    response = edit(spaced["customer_id"], territory="GUJRAT")
    after = card("Older  Copy   Customer")
    check("a customer saved long ago with extra spaces inside its name can still be edited once it has entries, and the name stays exactly "
          "as it is stored - so its entries stay with it",
          locked(spaced["customer_id"]) and response.status_code == 302 and after is not None and after["territory"] == "GUJRAT"
          and lines_named("Older  Copy   Customer") > 0, error_box(response) if response.status_code == 200 else str(after))
    ids["card_lock_customers"] = {"receipt": (by_receipt["customer_id"], "Receipt Customer"), "invoice": (by_invoice["customer_id"], "Draft Invoice Customer"),
                                  "journal": (by_journal["customer_id"], "Journal Customer"), "nothing": (second["customer_id"], "Multan Agro Centre")}

    # ---- Status: Active / Inactive ---------------------------------------------------
    def offered():
        """Is 'Lahore Seed House' offered on (a new receipt, a new invoice, the Partner account box of a journal entry)?"""
        in_select = lambda path: "Lahore Seed House" in "".join(
            re.findall(r'<select name="customer_id".*?</select>', browser.get(path).get_data(as_text=True), re.S))
        _general, partner, _data = journal_entry_choices(browser)
        in_journal = any(label == "Lahore Seed House" for group in partner.values() for _value, label in group)
        return in_select("/customers/receipts/new"), in_select("/sales/invoices/new"), in_journal

    ledger_before_the_status = q(LEDGER_FINGERPRINT)
    check("an active customer is offered on a new receipt, a new invoice and in the Partner account box of a journal entry", offered() == (True, True, True), str(offered()))
    response = edit(third["customer_id"], status="inactive")
    rows, count, _boxes, page = customer_list(browser)
    seed_house = [row for row in rows if row["name"] == "Lahore Seed House"]
    check("made Inactive on its Edit screen, it is no longer offered in any of the three places",
          response.status_code == 302 and card("Lahore Seed House")["is_active"] is False and offered() == (False, False, False), str(offered()))
    check("...but it stays on the customer list, marked Inactive, with its ID and details, and search still finds it",
          len(seed_house) == 1 and seed_house[0]["inactive"] and seed_house[0]["customer_no"] == "3" and seed_house[0]["region"] == "LHR"
          and [row["inactive"] for row in rows if row["name"] != "Lahore Seed House"].count(True) == 0
          and [row["name"] for row in customer_list(browser, "seed")[0]] == ["Lahore Seed House"])
    boxes, _page = customer_form(browser, f"/customers/{third['customer_id']}/edit")
    response = edit(third["customer_id"], status="active")
    check("its Edit screen shows Status Inactive, and setting it back to Active offers it everywhere again",
          boxes.get("status") == "inactive" and response.status_code == 302 and offered() == (True, True, True), f"{boxes.get('status')} {offered()}")
    response = browser.post_raw(f"/customers/{third['customer_id']}/edit", [("name", "Lahore Seed House"), ("region", "LHR"), ("territory", "LAHORE")])
    check("an Edit that does not send the Status box leaves the status alone", response.status_code == 302 and card("Lahore Seed House")["is_active"] is True)

    check("...and switching a customer off and on wrote nothing to the journal", q(LEDGER_FINGERPRINT) == ledger_before_the_status)

    # ---- one company never sees or changes another company's customers --------------------
    acme = card_by_id(int(ids["customer"]))
    names = [row["name"] for row in customer_list(browser)[0]]
    in_company_1 = [name for (name,) in q("SELECT name FROM customers WHERE organization_id = 1 AND name <> 'Kasur Traders'")]
    check(f"Card Co's list shows only its own {len(names)} customers - not company 1's ({', '.join(in_company_1)}); searching 'Acme' finds nobody",
          names and not set(names) & set(in_company_1) and len(names) == one("SELECT count(*) FROM customers WHERE organization_id = %s", (org,))
          and customer_list(browser, "Acme")[0] == [], str(names))
    check("Card Co cannot open the Edit screen of company 1's customer (404)", browser.get(f"/customers/{ids['customer']}/edit").status_code == 404)
    refused(browser, "Card Co saving over company 1's customer", f"/customers/{ids['customer']}/edit", [("name", "Taken Over"), ("credit_limit", "1")])
    check("...and company 1's customer is exactly as it was", card_by_id(int(ids["customer"])) == acme)
    _boxes, page = customer_form(browser, "/customers/new")
    check("the values offered while typing (Region, Territory...) are Card Co's own - never another company's",
          'value="KSR"' in page and 'value="KASUR CITY"' in page and "ONLY-IN-COMPANY-1" not in page)
    check("a customer number that does not exist is 'not found', never a crash",
          [browser.get(path).status_code for path in ("/customers/999999999/edit", "/customers/99999999999999999999/edit", "/customers/0/edit",
                                                      "/customers/abc/edit", "/customers/-1/edit")] == [404] * 5)

    # ---- text that tries to break the page ---------------------------------------------
    crashes, tries = [], 0
    every_box = ["name", "partner_type", "nature_of_supply", "lic_expiry_date", "credit_limit", "status"] + [field for field, _label, _limit in TEXT_FIELDS]
    hostile = ["\x00", "x" * 100000, "'; DROP TABLE customers; --", "<script>alert(1)</script>", "😀" * 50, "٠١٢٣", "-1e999999", "%s %d {0} {{x}}", "\\", "\r\n\r\n"]
    for path in ("/customers/new", f"/customers/{second['customer_id']}/edit"):
        for value in hostile:
            for fields in ([(box, value) for box in every_box],                                 # the same text in every box
                           [("name", "Odd Input Customer")] + [(box, value) for box in every_box[1:]],
                           [("name", value)]):
                tries += 1
                status = browser.post_raw(path, fields).status_code
                if status >= 500:
                    crashes.append((path, value[:20], status))
        for fields in ([], [("name", "A"), ("name", "B")], [("credit_limit", "1"), ("credit_limit", "2"), ("name", "Twice")]):
            tries += 1
            status = browser.post_raw(path, fields).status_code
            if status >= 500:
                crashes.append((path, str(fields)[:30], status))
    check(f"{tries} forms filled with text that tries to break the page (a NUL, 100,000 letters, SQL, a script, emoji, odd numbers, repeated boxes) "
          "never end in a crash page", not crashes, str(crashes[:5]))
    check("...and the customers table is still there, with every customer it had",
          one("SELECT count(*) FROM customers WHERE name IN ('Kasur Traders', 'Lahore Seed House', 'Receipt Customer')") >= 3)
    browser.post_raw("/customers/new", [("name", "<script>alert('x')</script> & Co"), ("territory", "<b>bold</b>")])
    pages = customer_list(browser)[3] + browser.get(f"/customers/{card(chr(60) + 'script>alert(' + chr(39) + 'x' + chr(39) + ')</script> & Co')['customer_id']}/edit").get_data(as_text=True)
    check("a name typed as HTML is shown as the text that was typed, on the list and on the Edit screen - it never runs",
          "<script>alert(" not in pages and "&lt;script&gt;alert(" in pages and "<b>bold</b>" not in pages)

    # ---- the limits of the form match the database --------------------------------------
    lengths = dict(q("SELECT column_name, character_maximum_length FROM information_schema.columns WHERE table_name = 'customers'"))
    mismatched = [field for field, _label, limit in TEXT_FIELDS if field not in lengths or (lengths[field] is not None and limit > lengths[field])]
    check("no box accepts more text than its column can hold (so a long text can be refused, but can never crash the save)",
          not mismatched and NAME_MAX_LENGTH <= lengths["name"], str(mismatched))

    _two_people_saving_at_once(org)

    # ---- search again, now that the company has many customers ----------------------------
    everyone = q("""SELECT name, region, territory, city, category_parent, category, ntn, cnic, lic_number, phone, email, customer_no
                    FROM customers WHERE organization_id = %s""", (org,))

    def should_find(search):
        """Worked out here in plain Python: every word is inside one of the eleven searched boxes, or IS the ID."""
        words = search.lower().split()
        return sorted(" ".join(row[0].split()) for row in everyone      # (a page shows several spaces as one)
                      if all(any(word in (value or "").lower() for value in row[:11]) or str(row[11]) == word for word in words))

    wrong = {}
    for search in ("1", "2", "10", "12", "customer", "CUSTOMER 1", "kasur", "0300", "sahiwal receipt", "a", "same moment"):
        got = sorted(row["name"] for row in customer_list(browser, search)[0])
        if got != should_find(search):
            wrong[search] = (got, should_find(search))
    check(f"with {len(everyone)} customers in the company, eleven different searches each find exactly the customers they should",
          not wrong and len(everyone) >= 12, str(wrong)[:600])
    has_a_1 = lambda row: any("1" in (value or "") for value in row[:11])
    id_10 = [" ".join(row[0].split()) for row in everyone if row[11] == 10]
    only_in_a_longer_id = [" ".join(row[0].split()) for row in everyone if row[11] != 1 and "1" in str(row[11]) and not has_a_1(row)]
    found_by_1 = [row["name"] for row in customer_list(browser, "1")[0]]
    found_by_10 = [row["name"] for row in customer_list(browser, "10")[0]]
    check(f"an ID is matched as a whole: '10' finds customer 10, and '1' does NOT find the {len(only_in_a_longer_id)} customers "
          "whose only 1 is a digit of a longer ID (10, 11, 12...)",
          len(id_10) == 1 and id_10[0] in found_by_10 and len(only_in_a_longer_id) >= 3 and not set(only_in_a_longer_id) & set(found_by_1),
          f"10 -> {found_by_10}; wrongly found by 1: {sorted(set(only_in_a_longer_id) & set(found_by_1))}; candidates {len(only_in_a_longer_id)}")

    # ---- records an OLDER copy of the program could have saved ----------------------------
    # It let two customers share a name, and it let a name be nothing but spaces. Written
    # here straight into the table, exactly as that older program would have left them.
    next_no = one("SELECT max(customer_no) FROM customers WHERE organization_id = %s", (org,)) + 1
    for offset, old_name in enumerate(["Old Twin", "Old Twin", "   "]):
        q("INSERT INTO customers (organization_id, customer_no, name, nature_of_supply) VALUES (%s, %s, %s, 'Supplies')",
          (org, next_no + offset, old_name))
    twin_a, twin_b, nameless = [one("SELECT customer_id FROM customers WHERE organization_id = %s AND customer_no = %s", (org, next_no + offset))
                                for offset in range(3)]
    saved_a = browser.post_raw(f"/customers/{twin_a}/edit", [("name", "Old Twin"), ("phone", "042-700-0001"), ("status", "active")]).status_code
    listed = [row["name"] for row in customer_list(browser, "old twin")[0]]
    check("two customers who ALREADY share a name (an older copy of the program allowed it) are both still on the list, and each "
          "can be edited with its name left as it is",
          saved_a == 302 and listed == ["Old Twin", "Old Twin"]
          and one("SELECT phone FROM customers WHERE customer_id = %s", (twin_a,)) == "042-700-0001", f"{saved_a} {listed}")
    refused(browser, "a third 'Old Twin'", "/customers/new", [("name", "Old Twin")], "already exists")
    renamed = browser.post_raw(f"/customers/{twin_b}/edit", [("name", "Old Twin (Kasur)"), ("status", "active")]).status_code
    check("...and one of the two can be renamed to tell them apart (it has no entries yet)",
          renamed == 302 and one("SELECT name FROM customers WHERE customer_id = %s", (twin_b,)) == "Old Twin (Kasur)", str(renamed))
    check("a customer saved long ago with a blank name can still be opened from the list: its link reads '(no name)'",
          re.search(rf'<a href="/customers/{nameless}/edit"[^>]*>\s*\(no name\)\s*</a>', customer_list(browser)[3]) is not None)
    named = browser.post_raw(f"/customers/{nameless}/edit", [("name", "Named At Last"), ("status", "active")]).status_code
    check("...and there it can be given a name (it has no entries yet)",
          named == 302 and one("SELECT name FROM customers WHERE customer_id = %s", (nameless,)) == "Named At Last", str(named))


def _two_people_saving_at_once(org):
    """Two saves of the same company at the same moment, on two separate database connections."""
    import threading
    from core.validation import ValidationError
    from customers.services import clean_customer_form, create_customer, update_customer

    def race(first, second):
        """
        Person 1 does first(cursor) but has not finished; person 2 does second(cursor) meanwhile.
        -> (did 2 wait for 1?, what first() returned, what 2 got)
        """
        one_conn, two_conn = psycopg2.connect(SCRATCH_URL), psycopg2.connect(SCRATCH_URL)
        result = {}
        try:
            first_result = first(one_conn.cursor())

            def second_person():
                try:
                    result["value"] = second(two_conn.cursor())
                    two_conn.commit()
                except ValidationError as error:
                    two_conn.rollback()
                    result["refused"] = str(error)
                except Exception as error:      # pragma: no cover - reported by the check below
                    two_conn.rollback()
                    result["error"] = repr(error)

            thread = threading.Thread(target=second_person)
            thread.start()
            thread.join(1.0)
            waited = thread.is_alive()
            one_conn.commit()
            thread.join(15)
            return waited and not thread.is_alive(), first_result, result
        finally:
            one_conn.close()
            two_conn.close()

    def typed(editing=False, **boxes):
        data, problems = clean_customer_form(boxes, editing=editing)
        assert not problems, problems
        return data

    def new_customer(name):
        """-> a save of a new customer with this name, giving its ID"""
        return lambda cursor: create_customer(cursor, org, typed(name=name))[1]

    def named(name):
        return one("SELECT count(*) FROM customers WHERE organization_id = %s AND lower(name) = lower(%s)", (org, name))

    waited, first_number, result = race(new_customer("Same Moment One"), new_customer("Same Moment Two"))
    check("two people saving a new customer at the same moment get two different IDs - the second save waits for the first",
          waited and result.get("value") == first_number + 1, f"waited={waited} first={first_number} second={result}")
    waited, first_number, result = race(new_customer("Twin Name"), new_customer("twin name"))
    check("...and two people saving the SAME new name at the same moment: the first is saved, the second is told the name already exists",
          waited and "already exists" in result.get("refused", "") and named("twin name") == 1,
          f"waited={waited} second={result}")

    # Person 1 renames a customer and gives its old name to a new customer. Person 2 is, at
    # that moment, saving the first customer's card as it looked BEFORE - with the old name.
    setup = psycopg2.connect(SCRATCH_URL)
    try:
        moved_id = create_customer(setup.cursor(), org, typed(name="Moved Name"))[0]
        setup.commit()
    finally:
        setup.close()

    def rename_and_reuse_the_old_name(cursor):
        update_customer(cursor, org, moved_id, typed(editing=True, name="Moved Name (Lahore)"))
        return create_customer(cursor, org, typed(name="Moved Name"))[1]

    def save_the_card_as_it_looked_before(cursor):
        update_customer(cursor, org, moved_id, typed(editing=True, name="Moved Name", phone="042-000-0000"))

    waited, _, result = race(rename_and_reuse_the_old_name, save_the_card_as_it_looked_before)
    check("...and a card saved while someone else is renaming that same customer is checked against the customer AS IT IS NOW: "
          "its old name has just gone to a new customer, so the second save is refused and no two customers share the name",
          waited and "already exists" in result.get("refused", "") and named("Moved Name") == 1 and named("Moved Name (Lahore)") == 1
          and one("SELECT phone FROM customers WHERE customer_id = %s", (moved_id,)) is None,
          f"waited={waited} second={result} named 'Moved Name'={named('Moved Name')}")
    check("no ID is used twice in any company", one("""SELECT count(*) FROM (SELECT organization_id, customer_no FROM customers
                                                        GROUP BY 1, 2 HAVING count(*) > 1) twice""") == 0)


def _customers_of_an_older_database(ids):
    """A database made BEFORE the customer card (no ID, no new boxes) is started with today's code."""
    old_name = SCRATCH_NAME + "_oldcards"
    old_url = _url_for_database(TEST_DATABASE_URL, old_name)

    def old(sql, args=()):
        conn = psycopg2.connect(old_url)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, args)
            rows = cursor.fetchall() if cursor.description else None
            conn.commit()
            return rows
        finally:
            conn.close()

    def start_the_app():
        return subprocess.run([sys.executable, "-c", "import app"], cwd=REPO_ROOT, capture_output=True, text=True,
                              env={**os.environ, "DATABASE_URL": old_url})

    new_columns = ["customer_no", "is_company", "email", "phone", "street2", "city", "state", "zip_code", "country", "region",
                   "territory", "category_parent", "category", "lic_number", "lic_expiry_date"]
    old_data = "SELECT customer_id, organization_id, name, created_at, nature_of_supply, ntn, strn, cnic, address, credit_limit, is_active FROM customers ORDER BY 1"
    documents = """SELECT (SELECT count(*) FROM customer_receipts), (SELECT count(*) FROM sales_invoices), (SELECT count(*) FROM journal_headers),
                          (SELECT md5(string_agg(customer_receipt_id || ':' || customer_id || ':' || amount, '|' ORDER BY customer_receipt_id)) FROM customer_receipts),
                          (SELECT md5(string_agg(sales_invoice_id || ':' || customer_id, '|' ORDER BY sales_invoice_id)) FROM sales_invoices)"""
    ids_now = "SELECT customer_id, customer_no FROM customers ORDER BY 1"
    _admin(f'CREATE DATABASE "{old_name}" TEMPLATE "{SCRATCH_NAME}"')
    try:
        ids_given_at_creation = old(ids_now)
        # exactly the shape an older database has: the same table, without the new columns
        old("ALTER TABLE customers " + ", ".join(f"DROP COLUMN {column}" for column in new_columns))
        old("UPDATE customers SET address = E'Shop 4\\nMain Bazaar', ntn = '7654321-0', credit_limit = 125000.50 WHERE customer_id = %s", (int(ids["customer"]),))
        before = (old(old_data), old(LEDGER_FINGERPRINT), old(documents))
        started = start_the_app()
        check("an older database (customers without an ID or the new boxes) starts with today's code", started.returncode == 0, started.stderr[-400:])
        columns = {name for (name,) in old("SELECT column_name FROM information_schema.columns WHERE table_name = 'customers'")}
        check(f"...the {len(new_columns)} new columns were added", set(new_columns) <= columns, str(sorted(set(new_columns) - columns)))
        check("...nothing about any existing customer changed: same names, NTN, STRN, CNIC, address (both lines), credit limits, status, creation times",
              old(old_data) == before[0])
        check("...no journal line, receipt or invoice changed", (old(LEDGER_FINGERPRINT), old(documents)) == before[1:])
        numbered = old(ids_now)
        check(f"...every one of the {len(numbered)} customers now has an ID: 1, 2, 3... inside its own company, oldest customer first "
              "- the same IDs they would have been given when they were created",
              numbered == ids_given_at_creation and all(number is not None for _customer, number in numbered), f"{numbered} vs {ids_given_at_creation}")
        check("...every existing customer is a Company until someone says otherwise, and every new box starts empty",
              old("SELECT count(*) FROM customers WHERE NOT is_company OR email IS NOT NULL OR region IS NOT NULL OR lic_expiry_date IS NOT NULL") == [(0,)])
        check("...and one ID can never be held by two customers of a company (the database itself refuses it)",
              old("SELECT count(*) FROM pg_indexes WHERE tablename = 'customers' AND indexname = 'customers_id_per_organization' AND indexdef LIKE 'CREATE UNIQUE%%'") == [(1,)])
        again = start_the_app()
        check("starting it a second time changes no ID", again.returncode == 0 and old(ids_now) == numbered, again.stderr[-400:])

        # a customer added by an older copy of the program still running on the same database: it has no ID
        highest = old("SELECT max(customer_no) FROM customers WHERE organization_id = 1")[0][0]
        old("INSERT INTO customers (organization_id, name) VALUES (1, 'Added By Old Program A'), (1, 'Added By Old Program B')")
        third = start_the_app()
        late = old("SELECT name, customer_no FROM customers WHERE name LIKE 'Added By Old Program %%' ORDER BY customer_id")
        check("customers saved without an ID (by an older copy of the program) are given the company's next IDs at the next start, "
              "and nobody else's ID moves",
              third.returncode == 0 and late == [("Added By Old Program A", highest + 1), ("Added By Old Program B", highest + 2)]
              and old(ids_now)[:len(numbered)] == numbered, f"{late} (highest was {highest})")

        # journal lines written long ago may carry no customer name at all: the receipt or the invoice ITSELF must then lock the name
        from customers.services import name_lock_reason
        old("UPDATE journal_lines SET partner_name = NULL")
        conn = psycopg2.connect(old_url)
        try:
            cursor = conn.cursor()
            reason = {key: name_lock_reason(cursor, ids["card_org"], {"customer_id": customer_id, "name": name})
                      for key, (customer_id, name) in ids["card_lock_customers"].items()}
        finally:
            conn.close()
        check("where old journal lines carry no customer name, a name is still locked by the receipt itself and by the invoice itself; "
              "a customer with neither is not locked",
              bool(reason["receipt"]) and bool(reason["invoice"]) and reason["journal"] is None and reason["nothing"] is None, str(reason))
    finally:
        try:
            _admin(f'DROP DATABASE IF EXISTS "{old_name}" WITH (FORCE)')
        except Exception as error:
            print(f"\n(could not drop scratch database {old_name}: {error})")


def test_12_ledger_integrity():
    section("12. The ledger as a whole")
    check("every journal entry balances",
          one("SELECT count(*) FROM (SELECT journal_header_id FROM journal_lines GROUP BY 1 HAVING sum(debit) <> sum(credit)) x") == 0)
    totals = q("""SELECT sum(l.debit), sum(l.credit) FROM journal_lines l
                  JOIN journal_headers h ON h.journal_header_id = l.journal_header_id WHERE h.status = 'Posted'""")[0]
    check(f"posted debits equal posted credits ({totals[0]:,.2f} = {totals[1]:,.2f})", totals[0] == totals[1])
    check("no journal line is negative, or carries both a debit and a credit",
          one("SELECT count(*) FROM journal_lines WHERE debit < 0 OR credit < 0 OR (debit <> 0 AND credit <> 0)") == 0)
    check("no journal entry is empty",
          one("""SELECT count(*) FROM journal_headers h WHERE NOT EXISTS
                 (SELECT 1 FROM journal_lines l WHERE l.journal_header_id = h.journal_header_id)""") == 0)
    check("no journal line sits on another company's account",
          one("""SELECT count(*) FROM journal_lines l JOIN journal_headers h ON h.journal_header_id = l.journal_header_id
                 JOIN chart_of_accounts c ON c.account_id = l.account_id WHERE c.organization_id <> h.organization_id""") == 0)
    check("a document is Posted exactly when its journal entry is",
          one("""SELECT (SELECT count(*) FROM vendor_bills b JOIN journal_headers h ON h.journal_header_id = b.journal_header_id WHERE b.status <> h.status)
                      + (SELECT count(*) FROM sales_invoices i JOIN journal_headers h ON h.journal_header_id = i.journal_header_id WHERE i.status <> h.status)""") == 0)
    check("every company has an owner who can open it (except the one this test inserted by hand)",
          one("""SELECT count(*) FROM organizations o WHERE o.name <> 'Somebody Elses Co' AND NOT EXISTS
                 (SELECT 1 FROM organization_memberships m WHERE m.organization_id = o.organization_id)""") == 0)


def main():
    print(f"Scratch database: {SCRATCH_NAME}  (created now, deleted at the end)")
    try:
        test_1_every_post_form_has_the_csrf_token()
        import app as lp                      # creates every table in the scratch database

        from core.db import get_conn
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT current_database()")
        connected_to = cursor.fetchone()[0]
        conn.close()
        if connected_to != SCRATCH_NAME:      # never run a single test anywhere else
            raise SystemExit(f"STOP: the app is connected to '{connected_to}', not the scratch database.")

        browser, ids = Browser(lp.app), {}
        test_2_first_start(lp)
        test_3_setup(browser)
        test_4_workflows(browser, ids)
        test_5_posting_rules(browser, ids)
        test_6_wrong_input_is_refused(browser, ids)
        test_7_second_company(browser, ids)
        test_8_isolation(browser, ids)
        test_9_dashboard(browser, ids)
        test_10_account_kinds(browser, ids)
        test_11_customer_card(browser, ids)
        test_12_ledger_integrity()
    finally:
        _drop_scratch_database()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED, {PASSED} passed:")
        for failure in FAILURES:
            print(f"  ✗ {failure}")
        sys.exit(1)
    print(f"ALL {PASSED} END-TO-END WORKFLOW CHECKS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    main()
