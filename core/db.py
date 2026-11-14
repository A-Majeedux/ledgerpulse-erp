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

HOW TO GIVE EVERY ORGANIZATION SOME STARTER DATA (accounts, rates, ...):
  1. In your segment's schema.py, write seed_organization(cursor,
     organization_id) that inserts the starter rows for that ONE
     organization, and only when they are missing (safe to run twice).
  2. Call it from your ensure_schema() for every existing organization.
  3. In app.py, add it to the register_organization_seeders([...]) list,
     so a company created later through "New Organization" gets the same
     starter data immediately instead of only after the next restart.
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


# ---------------------------------------------------------------------
# Starter data for a newly created organization
# ---------------------------------------------------------------------
# app.py fills this list once at startup (register_organization_seeders).
_ORGANIZATION_SEEDERS = []


def register_organization_seeders(seed_functions):
    """
    Call this once at startup (in app.py) with the list of
    seed_organization(cursor, organization_id) functions, one per segment,
    in the same order the segments' schemas are created.

    ORDER MATTERS: accounting must come first. It only adds the starter
    Chart of Accounts to an organization that has NO accounts yet, so if
    another segment added its own accounts first, the starter set would
    be skipped.
    """
    _ORGANIZATION_SEEDERS.clear()
    _ORGANIZATION_SEEDERS.extend(seed_functions)


def seed_new_organization(cursor, organization_id):
    """
    Give one organization all of its starter data (Chart of Accounts, WHT
    accounts, tax rate lists, ...), using the CALLER'S cursor so it is part
    of the same transaction as the INSERT that created the organization:
    either the company is created complete, or not at all.

    Safe to call more than once - every seeder only adds what is missing.
    """
    if not _ORGANIZATION_SEEDERS:
        raise RuntimeError(
            "No organization seeders are registered. app.py must call "
            "register_organization_seeders([...]) at startup, otherwise a new "
            "organization would be created with no Chart of Accounts."
        )
    for seed_fn in _ORGANIZATION_SEEDERS:
        seed_fn(cursor, organization_id)
