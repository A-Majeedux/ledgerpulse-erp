"""
accounting/intelligence.py

Rules-based accounting intelligence & recommendation service.
Analyzes historical posted journal entries and vendor/customer transactions within the active organization
to suggest appropriate accounts, partner matching, and explanations for new entries.
Strictly organization-isolated. Never automatically posts or modifies data.
"""
from decimal import Decimal
from core.money import to_decimal

def suggest_account_for_entry(cursor, organization_id, query_text="", partner_name=""):
    """
    Searches historical posted journal entries and lines in the given organization
    matching partner_name or query_text keywords.
    Returns a list of recommendations with confidence, account_id, account_name, and explanation.
    """
    recommendations = []
    
    query_lower = (query_text or "").strip().lower()
    partner_lower = (partner_name or "").strip().lower()
    
    if not query_lower and not partner_lower:
        return []
        
    sql = """
        SELECT 
            coa.account_id,
            coa.code,
            coa.name AS account_name,
            coa.account_type,
            COUNT(*) as match_count,
            MAX(jh.accounting_date) as last_used
        FROM journal_lines jl
        JOIN journal_headers jh ON jh.journal_header_id = jl.journal_header_id
        JOIN chart_of_accounts coa ON coa.account_id = jl.account_id
        WHERE jh.organization_id = %s AND jh.status = 'Posted'
          AND (
              (%s != '' AND LOWER(COALESCE(jl.partner_name, '')) LIKE %s)
              OR (%s != '' AND (LOWER(COALESCE(jl.label, '')) LIKE %s OR LOWER(COALESCE(jh.reference, '')) LIKE %s))
          )
        GROUP BY coa.account_id, coa.code, coa.name, coa.account_type
        ORDER BY match_count DESC, last_used DESC
        LIMIT 5
    """
    
    partner_pattern = f"%{partner_lower}%" if partner_lower else ""
    query_pattern = f"%{query_lower}%" if query_lower else ""
    
    cursor.execute(sql, (
        organization_id,
        partner_lower, partner_pattern,
        query_lower, query_pattern, query_pattern
    ))
    rows = cursor.fetchall()
    
    for row in rows:
        account_id, code, name, account_type, match_count, last_used = row
        confidence = "High" if match_count >= 3 else ("Medium" if match_count >= 1 else "Low")
        explanation = f"Matched {match_count} previous posted entry/entries using account [{code}] {name}."
        recommendations.append({
            "account_id": account_id,
            "code": code,
            "account_name": name,
            "account_type": account_type,
            "confidence": confidence,
            "match_count": match_count,
            "explanation": explanation
        })
        
    return recommendations
