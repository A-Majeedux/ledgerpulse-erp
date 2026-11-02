"""
reset_accounting_tables.py

ONE-TIME USE ONLY. Run this once to remove old leftover tables from the
previous version of the app, so the new accounting module can create
fresh, correct ones in their place. Safe to delete this file after
running it successfully.
"""
from core.db import get_conn

conn = get_conn()
cursor = conn.cursor()

cursor.execute("DROP TABLE IF EXISTS journal_lines CASCADE")
cursor.execute("DROP TABLE IF EXISTS journal_headers CASCADE")
cursor.execute("DROP TABLE IF EXISTS chart_of_accounts CASCADE")

conn.commit()
cursor.close()
conn.close()

print("Old accounting tables removed. You can now run: python app.py")