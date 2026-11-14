"""
accounting/schema.py

The foundation every other segment posts into: the Chart of Accounts, and
Journal Entries (headers + lines) as the single source of truth for every
financial transaction in the system - whether created directly here or
generated behind the scenes by Sales, Vendors, Assets, etc. once those
segments exist.
"""

ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Revenue", "Expense"]

# ---------------------------------------------------------------------
# Account KIND - what an account is used for, on top of its type
# ---------------------------------------------------------------------
# The kind decides WHERE an account can be picked:
#   Bank, Cash          the only accounts offered as "Paid from" / "Deposit
#                       to" on a payment or a receipt (and, later, as the
#                       two sides of a Bank Internal Transfer)
#   Trade receivable,   the two PARTNER control accounts. They are never
#   Trade payable       picked directly: a journal entry picks the customer
#                       or the vendor, and the line lands here by itself.
#   Non-trading         a partner account for ONE person outside the trade
#   receivable/payable  (a director, an employee): "Director loan - Mr X".
#                       Picked in the Partner account column of a journal entry.
#   General             everything else (revenue, expenses, tax ledgers,
#                       equity, other assets and liabilities).
KIND_GENERAL = "General"
KIND_BANK = "Bank"
KIND_CASH = "Cash"
KIND_TRADE_RECEIVABLE = "Trade receivable"
KIND_TRADE_PAYABLE = "Trade payable"
KIND_NON_TRADING_RECEIVABLE = "Non-trading receivable"
KIND_NON_TRADING_PAYABLE = "Non-trading payable"

ACCOUNT_KINDS = [
    KIND_GENERAL, KIND_BANK, KIND_CASH,
    KIND_TRADE_RECEIVABLE, KIND_TRADE_PAYABLE,
    KIND_NON_TRADING_RECEIVABLE, KIND_NON_TRADING_PAYABLE,
]
CASH_AND_BANK_KINDS = (KIND_BANK, KIND_CASH)
TRADE_PARTNER_KINDS = (KIND_TRADE_RECEIVABLE, KIND_TRADE_PAYABLE)
NON_TRADING_PARTNER_KINDS = (KIND_NON_TRADING_RECEIVABLE, KIND_NON_TRADING_PAYABLE)
PARTNER_KINDS = TRADE_PARTNER_KINDS + NON_TRADING_PARTNER_KINDS

# The kinds a user may give an account of each type. The two trade kinds are
# not here on purpose: they belong to the two control accounts below and are
# set by the system, never by hand.
KINDS_FOR_TYPE = {
    "Asset": [KIND_GENERAL, KIND_BANK, KIND_CASH, KIND_NON_TRADING_RECEIVABLE],
    "Liability": [KIND_GENERAL, KIND_NON_TRADING_PAYABLE],
    "Equity": [KIND_GENERAL],
    "Revenue": [KIND_GENERAL],
    "Expense": [KIND_GENERAL],
}

# The partner control accounts, by the SAME account codes the vouchers post
# to (sales/routes.py and customers/routes.py use 1100, vendors/routes.py
# uses 2000). All customers share the first, all vendors the second; who the
# customer or vendor is, is kept on each journal line (partner_name).
TRADE_RECEIVABLES_CODE = "1100"
TRADE_PAYABLES_CODE = "2000"
CONTROL_ACCOUNT_KINDS = {
    TRADE_RECEIVABLES_CODE: KIND_TRADE_RECEIVABLE,
    TRADE_PAYABLES_CODE: KIND_TRADE_PAYABLE,
}

# The minimal starter Chart of Accounts every organization begins with:
# (code, name, type, kind)
STARTER_ACCOUNTS = [
    ("1000", "Cash in Hand", "Asset", KIND_CASH),
    ("1010", "Bank Account", "Asset", KIND_BANK),
    ("1100", "Trade Receivables", "Asset", KIND_TRADE_RECEIVABLE),
    ("2000", "Trade Creditors", "Liability", KIND_TRADE_PAYABLE),
    ("3000", "Owner's Equity", "Equity", KIND_GENERAL),
    ("3100", "Retained Earnings", "Equity", KIND_GENERAL),
    ("4000", "Sales Revenue", "Revenue", KIND_GENERAL),
    ("5000", "Cost of Goods Sold", "Expense", KIND_GENERAL),
    ("6000", "General Expenses", "Expense", KIND_GENERAL),
]


def seed_organization(cursor, organization_id):
    """
    Give ONE organization its starter Chart of Accounts - but only if it
    has no accounts at all yet. This never overwrites or adds to accounts
    a user already created/edited.

    Called from two places:
      - ensure_schema() below, at app startup, for every organization
      - core.db.seed_new_organization(), the moment a new organization is
        created, so it is usable immediately (no restart needed)
    """
    cursor.execute(
        "SELECT COUNT(*) FROM chart_of_accounts WHERE organization_id = %s",
        (organization_id,)
    )
    if cursor.fetchone()[0] == 0:
        for code, name, account_type, account_kind in STARTER_ACCOUNTS:
            reconcilable = name in ("Trade Receivables", "Trade Creditors")
            cursor.execute("""
                INSERT INTO chart_of_accounts
                    (organization_id, code, name, account_type, allow_reconciliation, account_kind)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (organization_id, code, name, account_type, reconcilable, account_kind))


def _classify_existing_accounts(cursor):
    """
    Runs ONCE, at the moment the account_kind column is added to a database
    that already has accounts (every one of them starts as 'General').
    It only ever sets account_kind; nothing else about an account changes.

      1. The two partner control accounts, found by the codes the vouchers
         already post to: 1100 -> Trade receivable, 2000 -> Trade payable.

      2. Cash and bank accounts. Before this column existed, the dashboard
         counted "Asset accounts with codes 1000 to 1099" as cash & bank and
         told the user to give a new bank account a code in that range. The
         same accounts are marked here, so nothing that was counted as money
         yesterday drops out today. Whether each one is Bank or Cash is read
         from its name: a name with "cash" in it and no "bank" is Cash
         ("Cash in Hand", "Petty Cash"); every other one is Bank.

    This second step is a best guess, and it is the only guess made. A bank
    account kept under any other code stays 'General', as does every
    director / employee account - they are marked by hand on the Chart of
    Accounts screen (Edit). Anything guessed wrongly is corrected there too.
    """
    for code, account_kind in CONTROL_ACCOUNT_KINDS.items():
        cursor.execute(
            "UPDATE chart_of_accounts SET account_kind = %s WHERE code = %s",
            (account_kind, code)
        )
    cursor.execute("""
        UPDATE chart_of_accounts
        SET account_kind = CASE
                WHEN name ILIKE '%%cash%%' AND name NOT ILIKE '%%bank%%' THEN %s
                ELSE %s
            END
        WHERE account_type = 'Asset'
          AND btrim(code) ~ '^10[0-9][0-9]$'
    """, (KIND_CASH, KIND_BANK))


def ensure_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chart_of_accounts (
            account_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            code VARCHAR(20) NOT NULL,
            name VARCHAR(150) NOT NULL,
            account_type VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (organization_id, code)
        )
    """)
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS allow_reconciliation BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS tags VARCHAR(255)")
    cursor.execute("ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")

    # account_kind (see the top of this file). Additive, like every column
    # above. The accounts already in the database are classified once, at
    # the moment the column first appears - never again after that, so a
    # kind somebody changes by hand is not put back at the next start.
    cursor.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'chart_of_accounts' AND column_name = 'account_kind'
    """)
    kind_column_is_new = cursor.fetchone() is None
    cursor.execute(
        "ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS "
        "account_kind VARCHAR(30) NOT NULL DEFAULT 'General'"
    )
    if kind_column_is_new:
        _classify_existing_accounts(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_headers (
            journal_header_id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
            accounting_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS reference VARCHAR(100)")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS journal_name VARCHAR(100) DEFAULT 'Miscellaneous Operations'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Draft'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'Manual'")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS source_id INTEGER")
    cursor.execute("ALTER TABLE journal_headers ADD COLUMN IF NOT EXISTS posted_at TIMESTAMP")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            journal_line_id SERIAL PRIMARY KEY,
            journal_header_id INTEGER NOT NULL REFERENCES journal_headers(journal_header_id),
            account_id INTEGER NOT NULL REFERENCES chart_of_accounts(account_id),
            debit NUMERIC(14, 2) NOT NULL DEFAULT 0,
            credit NUMERIC(14, 2) NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS partner_name VARCHAR(150)")
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS label VARCHAR(255)")
    cursor.execute("ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS line_order INTEGER NOT NULL DEFAULT 0")

    # A blank Chart of Accounts is useless, so seed a minimal starter set
    # per organization the first time - just enough to record something.
    # This never overwrites accounts a user already created/edited.
    cursor.execute("SELECT DISTINCT organization_id FROM organizations")
    for (org_id,) in cursor.fetchall():
        seed_organization(cursor, org_id)
