"""
core/db.py

Every segment (accounting, sales, vendors, etc.) owns its own tables and is
responsible for creating them. This file just gives every segment a shared
way to connect to the database, and a shared registry so app.py can call
"create everything" once at startup without needing to know which segments
exist.

HOW TO ADD A NEW SEGMENT'S TABLES:
  1. In your segment folder (e.g. ledgerpulse/vendors/schema.py), write a
     function ensure_schema(cursor) that runs CREATE TABLE IF NOT EXISTS
     for every table that segment owns.
  2. In app.py, import that function and add it to the SCHEMA_FUNCTIONS list
     below (or pass it into ensure_all_schemas).
  3. Never DROP or destructively rewrite a table here - only ADD.
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    """
    Open a new database connection. Every route should open its own
    connection, use it, and close it - do not share connections across
    requests.
    """
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def ensure_all_schemas(schema_functions):
    """
    Call this once at startup with a list of ensure_schema(cursor) functions,
    one per segment. Runs them all in a single transaction so a fresh
    database (or a fresh organization) always has every table it needs,
    with nothing missing.

    Example (in app.py):
        from organizations.schema import ensure_schema as org_schema
        from accounting.schema import ensure_schema as accounting_schema

        ensure_all_schemas([org_schema, accounting_schema])
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        for schema_fn in schema_functions:
            schema_fn(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
