# services/sequence_engine.py
from datetime import datetime, timedelta
from database.models import EmailRecord, EmailStatus, Lead
from database.session import get_db_session
from services.suppression import is_suppressed
import random
import uuid

# How many days after campaign launch each step is sent
SEQUENCE_DELAYS = {
    1: (0, 0),      # Immediately
    2: (3, 5),      # Day 3–5
    3: (7, 9),      # Day 7–9
    4: (12, 15),    # Day 12–15
    5: (18, 21),    # Day 18–21
}


def enqueue_campaign_sequence(campaign_id: str, lead_ids: list) -> dict:
    """
    Create PENDING EmailRecord rows for every lead x every step.
    Celery picks these up and sends them at the scheduled time.
    """
    db = get_db_session()
    queued = 0
    skipped = 0

    try:
        for lead_id in lead_ids:
            lead = db.query(Lead).get(lead_id)
            if not lead or not lead.email:
                skipped += 1
                continue

            if is_suppressed(lead.email, db):
                skipped += 1
                continue

            for step, (min_days, max_days) in SEQUENCE_DELAYS.items():
                delay_days  = random.randint(min_days, max_days)
                send_hour   = random.randint(9, 11)    # 9am–11am
                send_minute = random.randint(0, 55)

                scheduled_at = (
                    datetime.utcnow()
                    + timedelta(days=delay_days)
                ).replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)

                record = EmailRecord(
                    id=str(uuid.uuid4()),
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    sequence_step=step,
                    status=EmailStatus.PENDING,
                    scheduled_at=scheduled_at,
                )
                db.add(record)
                queued += 1

        db.commit()
        print(f"Sequence queued: {queued} emails, {skipped} leads skipped")
        return {"queued": queued, "skipped": skipped}

    finally:
        db.close()


def should_send_email(record: EmailRecord, db) -> tuple:
    """
    Final check before sending each email.
    Returns (True, None) or (False, reason_string)
    """

    # 1. Suppressed
    if is_suppressed(record.lead.email, db):
        return False, "suppressed"

    # 2. Lead already replied to this campaign — stop the sequence
    already_replied = (
        db.query(EmailRecord)
        .filter(
            EmailRecord.lead_id    == record.lead_id,
            EmailRecord.campaign_id == record.campaign_id,
            EmailRecord.status     == EmailStatus.REPLIED,
        )
        .first()
    )
    if already_replied:
        return False, "lead_already_replied"

    # 3. Previous step bounced — don't keep trying
    if record.sequence_step > 1:
        prev = (
            db.query(EmailRecord)
            .filter(
                EmailRecord.lead_id       == record.lead_id,
                EmailRecord.campaign_id   == record.campaign_id,
                EmailRecord.sequence_step == record.sequence_step - 1,
            )
            .first()
        )
        if prev and prev.status == EmailStatus.BOUNCED:
            return False, "previous_step_bounced"

    return True, None