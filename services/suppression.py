# services/suppression.py
from database.models import Suppression
from database.session import get_db_session
from datetime import datetime
import uuid


def is_suppressed(email: str, db) -> bool:
    """Check if an email address is on the suppression list"""
    return (
        db.query(Suppression)
        .filter(Suppression.email_address == email.strip().lower())
        .first()
    ) is not None


def add_suppression(email: str, reason: str, db=None):
    """Add an email to the suppression list (safe to call multiple times)"""
    close_after = False
    if db is None:
        db = get_db_session()
        close_after = True

    try:
        email = email.strip().lower()
        if not is_suppressed(email, db):
            record = Suppression(
                id=str(uuid.uuid4()),
                email_address=email,
                reason=reason,
                created_at=datetime.utcnow(),
            )
            db.add(record)
            db.commit()
    finally:
        if close_after:
            db.close()


def get_all_suppressions(db) -> list:
    """Return all suppressed emails"""
    return db.query(Suppression).order_by(Suppression.created_at.desc()).all()