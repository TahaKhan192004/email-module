# workers/send_tasks.py
# Replaced all SQLAlchemy ORM queries with database/db.py functions

from workers.celery_app import celery_app
from database.db import (
    db_get_pending_emails_due,
    db_get_emails_sent_today,
    db_get_lead_by_id,
    db_get_campaign,
    db_get_previous_email_subjects,
    db_update_email_record,
    db_is_suppressed,
    db_check_lead_replied_in_campaign,
    db_get_previous_step_record,
)
from services.personalization import generate_personalized_email
from services.sender import send_email, get_sender_for_campaign
from config import settings
from datetime import datetime
import time
import random


@celery_app.task(name="workers.send_tasks.process_pending_emails")
def process_pending_emails():
    sent_count    = 0
    failed_count  = 0
    skipped_count = 0

    # How many can we still send today?
    today_sent = db_get_emails_sent_today()
    remaining  = settings.daily_send_limit - today_sent

    if remaining <= 0:
        return f"Daily limit of {settings.daily_send_limit} reached."

    # Fetch emails due now
    pending = db_get_pending_emails_due(limit=remaining)

    if not pending:
        return "No pending emails due right now."

    for record in pending:
        record_id   = record["id"]
        lead_id     = record["lead_id"]
        campaign_id = record["campaign_id"]
        step        = record["sequence_step"]

        try:
            # Fetch lead and campaign
            lead     = db_get_lead_by_id(lead_id)
            campaign = db_get_campaign(campaign_id)

            if not lead or not lead.get("email"):
                db_update_email_record(record_id, {"status": "failed", "notes": "no_lead_email"})
                skipped_count += 1
                continue

            # Pre-send checks
            if db_is_suppressed(lead["email"]):
                db_update_email_record(record_id, {"status": "failed", "notes": "suppressed"})
                skipped_count += 1
                continue

            if db_check_lead_replied_in_campaign(lead_id, campaign_id):
                db_update_email_record(record_id, {"status": "failed", "notes": "lead_already_replied"})
                skipped_count += 1
                continue

            if step > 1:
                prev = db_get_previous_step_record(lead_id, campaign_id, step)
                if prev and prev.get("status") == "bounced":
                    db_update_email_record(record_id, {"status": "failed", "notes": "previous_step_bounced"})
                    skipped_count += 1
                    continue

            # Previous subjects — so Gemini doesn't repeat the same angle
            prev_subjects = db_get_previous_email_subjects(lead_id, campaign_id, before_step=step)

            # Generate personalized email
            email_content = generate_personalized_email(
                lead=lead,
                campaign=campaign,
                sequence_step=step,
                previous_subjects=prev_subjects,
            )

            # Send via Gmail
            sender = get_sender_for_campaign(campaign_id)
            result = send_email(
                to_email=lead["email"],
                to_name=lead.get("first_name") or lead.get("business_name") or "",
                subject=email_content["subject"],
                html_body=email_content["body"],
                sender=sender,
            )

            # Mark as sent
            db_update_email_record(record_id, {
                "subject":      email_content["subject"],
                "body":         email_content["body"],
                "sender_email": sender["email"],
                "status":       "sent",
                "sent_at":      datetime.utcnow().isoformat(),
                "message_id":   result.get("message_id", ""),
            })
            sent_count += 1

        except Exception as e:
            db_update_email_record(record_id, {
                "status": "failed",
                "notes":  str(e)[:250],
            })
            failed_count += 1
            print(f"[SEND ERROR] record={record_id} step={step}: {e}")

        # Throttle between sends
        time.sleep(random.uniform(2.0, 5.0))

    return f"Done: {sent_count} sent, {failed_count} failed, {skipped_count} skipped"