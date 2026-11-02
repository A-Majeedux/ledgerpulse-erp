# VENDORS_STATUS.md

## Phase 0: Audit & Discovery
- **Status**: Completed
- **What was done**: Inspected existing codebase, schema definitions (`vendors/schema.py`), routes (`vendors/routes.py`), templates, and database patterns. Created `VENDORS_AUDIT.md`.
- **Files reviewed**: `vendors/schema.py`, `vendors/routes.py`, `app.py`, `accounting/services.py`, `core/db.py`.
- **Tests run**: Code compilation check.
- **Assumptions**: Rely on existing `chart_of_accounts`, `journal_headers`, `journal_lines`, and `accounting/services.py` for all double-entry posting.
- **Known gaps**: Credit notes, vendor detail card with smart buttons, bill/payment allocation, batch payments, own cheques report, partner ledger, and vendor aging are missing or partial and will be implemented in Phases 1–7.
