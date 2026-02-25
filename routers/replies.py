# routers/replies.py
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from database.session import get_db
from database.models import EmailRecord, EmailStatus, Reply, Lead
from services.personalization import classify_reply, generate_reply_response
from services.suppression import add_suppression
from workers.reply_tasks import send_single_reply
from config import settings
from datetime import datetime
import uuid
import re
import random

router = APIRouter(tags=["replies"])


# ─── HELPER FUNCTIONS ────────────────────────────────────────────────────────

def extract_email(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", text)
    return match.group(0).lower() if match else ""


# ─── MANUAL REPLY INJECTION (for Gmail testing) ───────────────────────────────

@router.post("/simulate-reply")
async def simulate_reply(body: dict, background_tasks: BackgroundTasks, db=Depends(get_db)):
    """
    Since Gmail can't auto-receive replies like SendGrid,
    use this endpoint to manually inject a reply for testing.

    Body: {"from_email": "...", "text": "...", "in_reply_to": "optional_message_id"}
    """
    from_email  = body.get("from_email", "").lower().strip()
    text_body   = body.get("text", "")
    in_reply_to = body.get("in_reply_to", "")

    if not from_email or not text_body:
        return {"error": "from_email and text are required"}

    background_tasks.add_task(
        process_reply,
        from_email=from_email,
        text_body=text_body,
        in_reply_to=in_reply_to,
    )
    return {"status": "received", "from": from_email}


async def process_reply(from_email: str, text_body: str, in_reply_to: str = ""):
    """Core reply processing logic"""
    from database.session import get_db_session
    db = get_db_session()

    try:
        # Find the lead by email
        lead = db.query(Lead).filter(Lead.email == from_email).first()
        if not lead:
            print(f"[REPLY] No lead found for {from_email}")
            return

        # Handle unsubscribes immediately
        unsubscribe_keywords = [
            "unsubscribe", "remove me", "stop emailing",
            "take me off", "opt out", "don't email", "please remove"
        ]
        if any(kw in text_body.lower() for kw in unsubscribe_keywords):
            add_suppression(from_email, "unsubscribe_reply", db)
            print(f"[REPLY] {from_email} unsubscribed")
            return

        # Find original email record for threading
        email_record = None
        if in_reply_to:
            email_record = (
                db.query(EmailRecord)
                .filter(EmailRecord.message_id == in_reply_to)
                .first()
            )

        # If no message_id match, find most recent email to this lead
        if not email_record:
            email_record = (
                db.query(EmailRecord)
                .filter(
                    EmailRecord.lead_id == lead.id,
                    EmailRecord.status == EmailStatus.SENT,
                )
                .order_by(EmailRecord.sent_at.desc())
                .first()
            )

        # Classify the reply
        category = classify_reply(text_body)
        print(f"[REPLY] {from_email} → category: {category}")

        # Save reply record
        reply = Reply(
            id=str(uuid.uuid4()),
            email_id=email_record.id if email_record else None,
            lead_id=lead.id,
            raw_content=text_body[:5000],
            category=category,
            received_at=datetime.utcnow(),
        )
        db.add(reply)

        # Mark email as replied → stops future sequence steps
        if email_record:
            email_record.status = EmailStatus.REPLIED

        # Generate LLM response for actionable replies
        if category in ["interested", "question", "objection"]:
            original_body = email_record.body if email_record else ""
            draft = generate_reply_response(
                original_email=original_body,
                reply_content=text_body,
                reply_category=category,
                lead=lead,
            )
            reply.llm_response_draft = draft

            # If auto-reply is on, approve and queue with delay
            if settings.auto_reply_enabled:
                delay = random.randint(
                    settings.auto_reply_min_delay,
                    settings.auto_reply_max_delay
                )
                reply.approved = True
                db.commit()
                send_single_reply.apply_async(args=[reply.id], countdown=delay)
                print(f"[REPLY] Auto-reply queued for {from_email} in {delay}s")
            else:
                db.commit()
                print(f"[REPLY] Draft saved — awaiting approval in queue")
        else:
            db.commit()

    finally:
        db.close()


# ─── APPROVAL QUEUE ───────────────────────────────────────────────────────────

@router.get("/replies/queue")
def get_approval_queue(db=Depends(get_db)):
    """Get all replies waiting for human review"""
    from sqlalchemy import and_

    pending = (
        db.query(Reply)
        .filter(and_(Reply.approved == False, Reply.sent == False))
        .order_by(Reply.received_at.desc())
        .all()
    )

    return [
        {
            "id": r.id,
            "lead_email":    r.lead.email if r.lead else None,
            "lead_name":     r.lead.first_name if r.lead else None,
            "business":      r.lead.business_name if r.lead else None,
            "category":      r.category.value if r.category else None,
            "their_reply":   r.raw_content[:400] if r.raw_content else None,
            "draft_response": r.llm_response_draft,
            "received_at":   r.received_at,
        }
        for r in pending
    ]


@router.post("/replies/{reply_id}/approve")
def approve_reply(reply_id: str, body: dict = {}, db=Depends(get_db)):
    """Approve a reply (optionally with edited text) and queue it for sending"""
    reply = db.query(Reply).get(reply_id)
    if not reply:
        return {"error": "Reply not found"}

    # Allow editing the draft before approving
    if "edited_response" in body and body["edited_response"]:
        reply.llm_response_draft = body["edited_response"]

    reply.approved = True
    db.commit()

    send_single_reply.delay(reply.id)

    return {
        "status": "approved_and_queued",
        "reply_id": reply_id,
        "to": reply.lead.email if reply.lead else None,
    }


@router.post("/replies/{reply_id}/reject")
def reject_reply(reply_id: str, db=Depends(get_db)):
    """Dismiss a reply without sending anything"""
    reply = db.query(Reply).get(reply_id)
    if not reply:
        return {"error": "Reply not found"}

    reply.sent = True   # marks it as handled so it leaves the queue
    db.commit()
    return {"status": "dismissed"}


# ─── UNSUBSCRIBE ENDPOINT ─────────────────────────────────────────────────────

@router.get("/unsubscribe")
def unsubscribe(email: str, db=Depends(get_db)):
    """
    Handles clicks on the unsubscribe link in emails.
    URL: http://YOUR_VPS_IP:8000/unsubscribe?email=user@example.com
    """
    if not email:
        return {"error": "No email provided"}

    add_suppression(email, "unsubscribe_link_click", db)
    return {
        "message": "You have been unsubscribed successfully.",
        "email": email,
    }


# ─── LEADS MANAGEMENT ────────────────────────────────────────────────────────

@router.post("/leads/")
def create_lead(body: dict, db=Depends(get_db)):
    """Manually add a lead for testing"""
    from database.models import Lead

    lead = Lead(
        id=str(uuid.uuid4()),
        first_name=body.get("first_name", ""),
        last_name=body.get("last_name", ""),
        email=body["email"],
        business_name=body.get("business_name", ""),
        industry=body.get("industry", ""),
        location=body.get("location", ""),
        website=body.get("website", ""),
        source_platform=body.get("source_platform", "manual"),
        specifications=body.get("specifications", ""),
        bundle_id=body.get("bundle_id", "default"),
        created_at=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    return {"id": lead.id, "email": lead.email, "bundle_id": lead.bundle_id}


@router.get("/leads/")
def list_leads(bundle_id: str = None, db=Depends(get_db)):
    from database.models import Lead
    query = db.query(Lead)
    if bundle_id:
        query = query.filter(Lead.bundle_id == bundle_id)
    leads = query.all()
    return [
        {
            "id": l.id,
            "email": l.email,
            "name": f"{l.first_name or ''} {l.last_name or ''}".strip(),
            "business": l.business_name,
            "bundle_id": l.bundle_id,
        }
        for l in leads
    ]