# services/suppression.py
# Fully replaced — no SQLAlchemy, no psycopg2
# All calls go through database/db.py → Supabase REST

from database.db import db_is_suppressed, db_add_suppression, db_list_suppressions


def is_suppressed(email: str) -> bool:
    """Check if an email is on the suppression list"""
    return db_is_suppressed(email)


def add_suppression(email: str, reason: str):
    """Add email to suppression list — safe to call multiple times"""
    return db_add_suppression(email, reason)


def get_all_suppressions() -> list:
    return db_list_suppressions()