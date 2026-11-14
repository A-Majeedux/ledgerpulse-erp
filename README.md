# zyro-erp

## Run it

1. Install the Python packages: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and put in your database connection string and a secret key.
3. Start the app: `python3 app.py`, then open http://localhost:5000

The first start creates every table and a starter Chart of Accounts. The first
visit shows a one-time setup screen where you create the administrator and name
your company.

## Check it

| Command | What it checks | Needs a database? |
| --- | --- | --- |
| `python3 test_money.py` | Decimal-safe money arithmetic and rounding | No |
| `python3 test_integration.py` | Invoice, payment and receipt arithmetic | No |
| `python3 test_dashboard.py` | Dashboard arithmetic: which dates a period covers, like-for-like comparisons, percentages, rounding | No |
| `python3 test_customers.py` | The rules of the customer card: what may be typed in each box, the limits, how the list writes amounts and dates, the search | No |
| `python3 test_auth.py` | Login, platform-admin and subscription rules | Yes - reads `DATABASE_URL` |
| `python3 test_workflows.py` | The real screens end to end: every form, every journal entry posted, wrong input refused, companies kept apart, every dashboard figure compared with the ledger, which accounts each screen offers, the customer card (ID, Edit, list, search), and the upgrade of an older database | Yes - `TEST_DATABASE_URL` |

`test_workflows.py` never touches your real data: it creates its own empty
scratch database on the server `TEST_DATABASE_URL` points to, works only in
that, and deletes it when it finishes. Without `TEST_DATABASE_URL` it does nothing.

## Where the project stands

`PROJECT_STAGES.md` lists every stage of the build: what is done, what comes
next, who decided what, and the known problems that are not fixed yet.
