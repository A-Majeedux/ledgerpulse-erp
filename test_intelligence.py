"""
test_intelligence.py
Unit tests for LedgerPulse Accounting Intelligence & Suggestion service.
"""
from decimal import Decimal
from accounting.intelligence import suggest_account_for_entry

class MockCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

def test_intelligence_suggestions():
    mock_rows = [
        (101, "6010", "Rent Expense", "Expense", 4, "2026-10-01"),
        (102, "6020", "Utilities Expense", "Expense", 2, "2026-09-15"),
    ]
    cursor = MockCursor(mock_rows)
    suggestions = suggest_account_for_entry(cursor, organization_id=1, query_text="Rent", partner_name="Landlord Co")
    
    assert len(suggestions) == 2
    assert suggestions[0]["account_id"] == 101
    assert suggestions[0]["code"] == "6010"
    assert suggestions[0]["confidence"] == "High"
    assert "Matched 4" in suggestions[0]["explanation"]
    print("✓ Intelligence suggestion service tests passed successfully.")

if __name__ == "__main__":
    test_intelligence_suggestions()
