"""
scripts/find_test_leftovers.py

Read-only script to list leftover test rows from earlier test runs in the database.
Lists organizations matching TEST_% or Test Org Vendors / Other Org Vendors,
vendors named 'Seeded Vendor', and related bills, payments, credit notes, batches, and journal entries.
Does NOT delete anything.
"""
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.db import get_conn

def find_leftovers():
    conn = get_conn()
    cursor = conn.cursor()
    
    print("=== FINDING TEST LEFTOVERS (READ-ONLY) ===")
    
    # 1. Organizations
    cursor.execute("""
        SELECT organization_id, name, created_at 
        FROM organizations 
        WHERE name LIKE 'TEST_%' OR name LIKE '%Test Org%' OR name LIKE '%Other Org%'
        ORDER BY organization_id
    """)
    orgs = cursor.fetchall()
    print(f"\nFound {len(orgs)} test organizations:")
    org_ids = []
    for org in orgs:
        org_id, name, created_at = org
        org_ids.append(org_id)
        print(f"  - Org ID: {org_id}, Name: '{name}', Created: {created_at}")
        
        # Count vendors in this org
        cursor.execute("SELECT COUNT(*) FROM vendors WHERE organization_id = %s", (org_id,))
        v_count = cursor.fetchone()[0]
        
        # Count bills in this org
        cursor.execute("SELECT COUNT(*) FROM vendor_bills WHERE organization_id = %s", (org_id,))
        b_count = cursor.fetchone()[0]
        
        # Count payments in this org
        cursor.execute("SELECT COUNT(*) FROM vendor_payments WHERE organization_id = %s", (org_id,))
        p_count = cursor.fetchone()[0]
        
        # Count credit notes in this org
        cursor.execute("SELECT COUNT(*) FROM vendor_credit_notes WHERE organization_id = %s", (org_id,))
        cn_count = cursor.fetchone()[0]
        
        # Count batch payments in this org
        cursor.execute("SELECT COUNT(*) FROM batch_vendor_payments WHERE organization_id = %s", (org_id,))
        bp_count = cursor.fetchone()[0]
        
        # Count journal headers in this org
        cursor.execute("SELECT COUNT(*) FROM journal_headers WHERE organization_id = %s", (org_id,))
        jh_count = cursor.fetchone()[0]
        
        print(f"    -> Counts for Org {org_id} ('{name}'): Vendors={v_count}, Bills={b_count}, Payments={p_count}, Credit Notes={cn_count}, Batch Payments={bp_count}, Journal Headers={jh_count}")

    # Also check for vendors named 'Seeded Vendor' across any org
    cursor.execute("""
        SELECT v.vendor_id, v.organization_id, o.name, v.name, v.created_at
        FROM vendors v
        JOIN organizations o ON v.organization_id = o.organization_id
        WHERE v.name = 'Seeded Vendor'
    """)
    seeded_vendors = cursor.fetchall()
    print(f"\nFound {len(seeded_vendors)} vendors named 'Seeded Vendor':")
    for sv in seeded_vendors:
        print(f"  - Vendor ID: {sv[0]}, Org ID: {sv[1]} ('{sv[2]}'), Name: '{sv[3]}', Created: {sv[4]}")

    cursor.close()
    conn.close()
    print("\n=== END OF LEFTOVERS REPORT ===")

if __name__ == '__main__':
    find_leftovers()
