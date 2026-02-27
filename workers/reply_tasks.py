# workers/reply_tasks.py
# Replaced SQLAlchemy with database/db.py functions

from workers.celery_app import celery_app
from database.db import (
    db_get_reply,
    db_get_lead_by_id,
    db_get_email_record,
    db_update_reply,
    db_get_approved_unsent_replies,
)
from services.sender import send_reply_email, get_sender_for_campaign
from datetime import datetime


@celery_app.task(name="workers.reply_tasks.send_single_reply")
def send_single_reply(reply_id: str):
    reply = db_get_reply(reply_id)

    if not reply:
        return f"Reply {reply_id} not found"
    if reply.get("sent"):
        return f"Reply {reply_id} already sent"
    if not reply.get("llm_response_draft"):
        return f"Reply {reply_id} has no draft"

    lead         = db_get_lead_by_id(reply["lead_id"])
    email_record = db_get_email_record(reply["email_id"]) if reply.get("email_id") else None
    sender       = get_sender_for_campaign(
        email_record["campaign_id"] if email_record else "default"
    )

    try:
        send_reply_email(
            to_email=lead["email"],
            to_name=lead.get("first_name") or "",
            body=reply["llm_response_draft"],
            sender=sender,
            in_reply_to_message_id=email_record.get("message_id") if email_record else None,
        )

        db_update_reply(reply_id, {
            "sent":         True,
            "responded_at": datetime.utcnow().isoformat(),
        })
        return f"Reply sent to {lead['email']}"

    except Exception as e:
        print(f"[REPLY ERROR] {reply_id}: {e}")
        return f"Failed: {e}"


@celery_app.task(name="workers.reply_tasks.auto_send_approved_replies")
def auto_send_approved_replies():
    pending = db_get_approved_unsent_replies()
    for reply in pending:
        send_single_reply.delay(reply["id"])
    return f"Queued {len(pending)} replies"