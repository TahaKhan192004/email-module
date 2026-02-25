# workers/send_tasks.py
from workers.celery_app import celery_app
from database.session import get_db_session
from database.models import EmailRecord, EmailStatus
from services.sequence_engine import should_send_email
from services.personalization import generate_personalized_email
from services.sender import send_email, get_sender_for_campaign
from config import settings
from datetime import datetime, timedelta
import time
import random


@celery_app.task(name="workers.send_tasks.process_pending_emails")
def process_pending_emails():
    """
    Runs every 10 minutes via Celery Beat.
    Picks up PENDING emails that are due and sends them.
    """
    db = get_db_session()
    sent_count   = 0
    failed_count = 0
    skipped_count = 0

    try:
        # How many have we already sent today?
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_sent  = (
            db.query(EmailRecord)
            .filter(
                EmailRecord.sent_at >= today_start,
                EmailRecord.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
            )
            .count()
        )

        remaining = settings.daily_send_limit - today_sent
        if remaining <= 0:
            return f"Daily limit of {settings.daily_send_limit} reached. Done for today."

        # Find emails that are scheduled for now (with a 5-minute lookahead)
        window_end = datetime.utcnow() + timedelta(minutes=5)
        pending = (
            db.query(EmailRecord)
            .filter(
                EmailRecord.status == EmailStatus.PENDING,
                EmailRecord.scheduled_at <= window_end,
            )
            .limit(remaining)
            .all()
        )

        if not pending:
            return "No pending emails due right now."

        for record in pending:
            try:
                # Access lead relationship
                lead     = record.lead
                campaign = record.campaign

                if not lead or not lead.email:
                    record.status = EmailStatus.FAILED
                    record.notes  = "no_lead_email"
                    db.commit()
                    skipped_count += 1
                    continue

                # Final checks before sending
                ok, reason = should_send_email(record, db)
                if not ok:
                    record.status = EmailStatus.FAILED
                    record.notes  = reason
                    db.commit()
                    skipped_count += 1
                    continue

                # Get previous subjects for this lead (so Gemini doesn't repeat them)
                prev_records = (
                    db.query(EmailRecord)
                    .filter(
                        EmailRecord.lead_id       == record.lead_id,
                        EmailRecord.campaign_id   == record.campaign_id,
                        EmailRecord.sequence_step <  record.sequence_step,
                        EmailRecord.subject       != None,
                    )
                    .all()
                )
                prev_subjects = [r.subject for r in prev_records if r.subject]

                # Generate email content with Gemini
                email_content = generate_personalized_email(
                    lead=lead,
                    campaign=campaign,
                    sequence_step=record.sequence_step,
                    previous_subjects=prev_subjects,
                )

                # Send via Gmail SMTP
                sender = get_sender_for_campaign(record.campaign_id)
                result = send_email(
                    to_email=lead.email,
                    to_name=lead.first_name or lead.business_name or "",
                    subject=email_content["subject"],
                    html_body=email_content["body"],
                    sender=sender,
                )

                # Save results
                record.subject    = email_content["subject"]
                record.body       = email_content["body"]
                record.sender_email = sender["email"]
                record.status     = EmailStatus.SENT
                record.sent_at    = datetime.utcnow()
                record.message_id = result.get("message_id", "")
                db.commit()
                sent_count += 1

            except Exception as e:
                record.status = EmailStatus.FAILED
                record.notes  = str(e)[:250]
                db.commit()
                failed_count += 1
                print(f"[SEND ERROR] lead={record.lead_id} step={record.sequence_step}: {e}")

            # Throttle between sends — don't hammer Gmail
            time.sleep(random.uniform(2.0, 5.0))

        return f"Done: {sent_count} sent, {failed_count} failed, {skipped_count} skipped"

    finally:
        db.close()