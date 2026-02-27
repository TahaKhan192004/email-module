# services/sequence_engine.py
# Replaced SQLAlchemy ORM with database/db.py functions

from datetime import datetime, timedelta
from database.db import (
    db_get_lead_by_id,
    db_bulk_create_email_records,
    db_check_lead_replied_in_campaign,
    db_get_previous_step_record,
    db_is_suppressed,
)
import random
import uuid

SEQUENCE_DELAYS = {
    1: (0, 0),
    2: (3, 5),
    3: (7, 9),
    4: (12, 15),
    5: (18, 21),
}


def enqueue_campaign_sequence(campaign_id: str, lead_ids: list) -> dict:
    """
    Build all email records for every lead x every step and bulk insert them.
    Much faster than inserting one by one.
    """
    records_to_insert = []
    skipped = 0

    for lead_id in lead_ids:
        lead = db_get_lead_by_id(lead_id)
        if not lead or not lead.get("email"):
            skipped += 1
            continue

        if db_is_suppressed(lead["email"]):
            skipped += 1
            continue

        for step, (min_days, max_days) in SEQUENCE_DELAYS.items():
            delay_days  = random.randint(min_days, max_days)
            send_hour   = random.randint(9, 11)
            send_minute = random.randint(0, 55)

            scheduled_at = (
                datetime.utcnow() + timedelta(days=delay_days)
            ).replace(
                hour=send_hour,
                minute=send_minute,
                second=0,
                microsecond=0,
            )

            records_to_insert.append({
                "id":            str(uuid.uuid4()),
                "campaign_id":   campaign_id,
                "lead_id":       lead_id,
                "sequence_step": step,
                "status":        "pending",
                "scheduled_at":  scheduled_at.isoformat(),
            })

    queued = 0
    if records_to_insert:
        # Bulk insert all at once — one HTTPS call instead of N calls
        db_bulk_create_email_records(records_to_insert)
        queued = len(records_to_insert)

    print(f"Sequence queued: {queued} emails, {skipped} leads skipped")
    return {"queued": queued, "skipped": skipped}


def should_send_email(record: dict) -> tuple:
    """
    Final pre-send checks.
    Returns (True, None) if safe to send, (False, reason) if not.
    record is now a plain dict (from Supabase) not an ORM object.
    """
    lead_email = record.get("lead_email") or _get_lead_email(record["lead_id"])

    if not lead_email:
        return False, "no_lead_email"

    if db_is_suppressed(lead_email):
        return False, "suppressed"

    if db_check_lead_replied_in_campaign(record["lead_id"], record["campaign_id"]):
        return False, "lead_already_replied"

    step = record.get("sequence_step", 1)
    if step > 1:
        prev = db_get_previous_step_record(record["lead_id"], record["campaign_id"], step)
        if prev and prev.get("status") == "bounced":
            return False, "previous_step_bounced"

    return True, None


def _get_lead_email(lead_id: str) -> str:
    """Helper to get lead email when it's not already on the record"""
    from database.db import db_get_lead_by_id
    lead = db_get_lead_by_id(lead_id)
    return lead.get("email", "") if lead else ""