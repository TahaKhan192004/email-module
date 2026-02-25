# workers/reply_tasks.py
from workers.celery_app import celery_app
from database.session import get_db_session
from database.models import Reply
from services.sender import send_reply_email, get_sender_for_campaign
from datetime import datetime


@celery_app.task(name="workers.reply_tasks.send_single_reply")
def send_single_reply(reply_id: str):
    """Send one approved reply"""
    db = get_db_session()
    try:
        reply = db.query(Reply).get(reply_id)
        if not reply:
            return f"Reply {reply_id} not found"
        if reply.sent:
            return f"Reply {reply_id} already sent"
        if not reply.llm_response_draft:
            return f"Reply {reply_id} has no draft to send"

        lead         = reply.lead
        email_record = reply.email
        sender       = get_sender_for_campaign(
            email_record.campaign_id if email_record else "default"
        )

        send_reply_email(
            to_email=lead.email,
            to_name=lead.first_name or "",
            body=reply.llm_response_draft,
            sender=sender,
            in_reply_to_message_id=email_record.message_id if email_record else None,
        )

        reply.sent         = True
        reply.responded_at = datetime.utcnow()
        db.commit()

        return f"Reply sent to {lead.email}"

    except Exception as e:
        print(f"[REPLY ERROR] {reply_id}: {e}")
        return f"Failed: {e}"

    finally:
        db.close()


@celery_app.task(name="workers.reply_tasks.auto_send_approved_replies")
def auto_send_approved_replies():
    """
    Runs every 5 minutes via Celery Beat.
    Finds approved-but-unsent replies and queues them.
    """
    db = get_db_session()
    try:
        pending = (
            db.query(Reply)
            .filter(Reply.approved == True, Reply.sent == False)
            .all()
        )
        for reply in pending:
            send_single_reply.delay(reply.id)

        return f"Queued {len(pending)} replies for sending"
    finally:
        db.close()