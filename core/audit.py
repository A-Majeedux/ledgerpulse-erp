"""
core/audit.py

Who did that?

A posted invoice is a statement the company made to somebody outside it. When it
later goes back to a draft, the books change - and the person looking at the
invoice six months on deserves to know who changed it and when, without going
hunting through a log file.

So every document keeps its own short history: posted, reset to draft, deleted,
edited. The record is written in the same transaction as the change, which means
there is no window in which a document has moved and nobody wrote it down.

`audit_logs` (auth) is the system's security log - logins, role changes, the
platform's own business. This is the document's own diary, and it is read back on
the document's own screen.
"""
from core.db import get_conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS transaction_history (
    history_id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    subject_type VARCHAR(40) NOT NULL,
    subject_id INTEGER NOT NULL,
    action VARCHAR(30) NOT NULL,
    detail TEXT,
    user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    user_name VARCHAR(120),
    ip_address VARCHAR(60),
    happened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS transaction_history_subject_idx
    ON transaction_history (organization_id, subject_type, subject_id, happened_at DESC);
"""

# What the reader sees. Words, not codes.
ACTION_WORDS = {
    "Created": "Created",
    "Posted": "Posted",
    "Reset": "Reset to draft",
    "Deleted": "Deleted",
    "Edited": "Edited",
    "Paid": "Payment applied",
    "Reversed": "Reversed",
    "Imported": "Imported",
}


def ensure_schema(cursor):
    cursor.execute(SCHEMA)


def _who():
    """The signed-in person, as far as the request knows."""
    try:
        from flask import has_request_context, request, session
        if not has_request_context():
            return None, None, None
        return session.get("user_id"), session.get("user_name") or session.get("email"), request.remote_addr
    except Exception:
        return None, None, None


def note(cursor, organization_id, subject_type, subject_id, action, detail=None,
         user_id=None, user_name=None, ip_address=None):
    """Write one line of a document's history. Call it inside the same transaction."""
    if not organization_id or not subject_id:
        return
    if user_id is None or user_name is None:
        session_user_id, session_user_name, session_ip = _who()
        user_id = session_user_id if user_id is None else user_id
        user_name = session_user_name if user_name is None else user_name
        ip_address = session_ip if ip_address is None else ip_address
    cursor.execute("""
        INSERT INTO transaction_history
            (organization_id, subject_type, subject_id, action, detail, user_id, user_name, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (organization_id, subject_type, int(subject_id), action, detail,
          user_id, (user_name or "System")[:120], ip_address))


def history(organization_id, subject_type, subject_id):
    """The diary, newest first, for a document's screen."""
    conn = get_conn()
    cursor = conn.cursor()
    ensure_schema(cursor)
    cursor.execute("""
        SELECT action, detail, user_name, happened_at
        FROM transaction_history
        WHERE organization_id = %s AND subject_type = %s AND subject_id = %s
        ORDER BY happened_at DESC, history_id DESC
    """, (organization_id, subject_type, int(subject_id)))
    rows = [(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return [{"action": row[0], "words": ACTION_WORDS.get(row[0], row[0]),
             "detail": row[1], "who": row[2],
             "when": row[3].strftime("%d %b %Y, %H:%M") if row[3] else ""} for row in rows]


def latest(organization_id, subject_type, subject_id, action):
    """When this document was last put in this state, and by whom - or (None, None)."""
    conn = get_conn()
    cursor = conn.cursor()
    ensure_schema(cursor)
    cursor.execute("""
        SELECT user_name, happened_at FROM transaction_history
        WHERE organization_id = %s AND subject_type = %s AND subject_id = %s AND action = %s
        ORDER BY happened_at DESC, history_id DESC LIMIT 1
    """, (organization_id, subject_type, int(subject_id), action))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return None, None
    return row[0], row[1].strftime("%d %b %Y, %H:%M") if row[1] else ""
