# routers/replies.py
# Replaced SQLAlchemy with database/db.py functions

from fastapi import APIRouter, BackgroundTasks
from database.db import (
    db_get_lead_by_email,
    db_get_lead_by_id,
    db_get_email_record_by_message_id,
    db_get_latest_sent_email_for_lead,
    db_update_email_record,
    db_create_reply,
    db_get_reply,
    db_update_reply,
    db_get_approval_queue,
    db_add_suppression,
    db_is_suppressed,
    db_create_lead,
)
from services.personalization import classify_reply, generate_reply_response
from workers.reply_tasks import send_single_reply
from config import settings
from datetime import datetime
import uuid
import re
import random

router = APIRouter(tags=["replies"])

UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe", "remove me", "stop emailing",
    "take me off", "opt out", "don't email", "please remove",
]


# ─── SIMULATE REPLY (Gmail workaround) ───────────────────────────────────────

@router.post("/simulate-reply")
async def simulate_reply(body: dict, background_tasks: BackgroundTasks):
    from_email  = body.get("from_email", "").lower().strip()
    text_body   = body.get("text", "")
    in_reply_to = body.get("in_reply_to", "")

    if not from_email or not text_body:
        return {"error": "from_email and text are required"}

    background_tasks.add_task(
        _process_reply,
        from_email=from_email,
        text_body=text_body,
        in_reply_to=in_reply_to,
    )
    return {"status": "received", "from": from_email}


async def _process_reply(from_email: str, text_body: str, in_reply_to: str = ""):
    lead = db_get_lead_by_email(from_email)
    if not lead:
        print(f"[REPLY] No lead found for {from_email}")
        return

    # Unsubscribe check — handle immediately
    if any(kw in text_body.lower() for kw in UNSUBSCRIBE_KEYWORDS):
        db_add_suppression(from_email, "unsubscribe_reply")
        print(f"[REPLY] {from_email} unsubscribed")
        return

    # Find original email for threading
    email_record = None
    if in_reply_to:
        email_record = db_get_email_record_by_message_id(in_reply_to)
    if not email_record:
        email_record = db_get_latest_sent_email_for_lead(lead["id"])

    # Classify
    category = classify_reply(text_body)
    print(f"[REPLY] {from_email} → {category}")

    # Save reply
    reply = db_create_reply({
        "id":          str(uuid.uuid4()),
        "email_id":    email_record["id"] if email_record else None,
        "lead_id":     lead["id"],
        "raw_content": text_body[:5000],
        "category":    category,
    })

    # Mark original email as replied → stops remaining sequence steps
    if email_record:
        db_update_email_record(email_record["id"], {"status": "replied"})

    # Generate LLM draft for actionable replies
    if category in ["interested", "question", "objection"]:
        original_body = email_record.get("body", "") if email_record else ""
        draft = generate_reply_response(
            original_email=original_body,
            reply_content=text_body,
            reply_category=category,
            lead=lead,
        )
        db_update_reply(reply["id"], {"llm_response_draft": draft})

        if settings.auto_reply_enabled:
            delay = random.randint(settings.auto_reply_min_delay, settings.auto_reply_max_delay)
            db_update_reply(reply["id"], {"approved": True})
            send_single_reply.apply_async(args=[reply["id"]], countdown=delay)
            print(f"[REPLY] Auto-reply queued for {from_email} in {delay}s")


# ─── APPROVAL QUEUE ───────────────────────────────────────────────────────────

@router.get("/replies/queue")
def get_approval_queue():
    rows = db_get_approval_queue()
    result = []
    for r in rows:
        # Supabase join returns leads data nested
        lead_data = r.get("leads") or {}
        result.append({
            "id":             r["id"],
            "lead_email":     lead_data.get("email"),
            "lead_name":      lead_data.get("first_name"),
            "business":       lead_data.get("business_name"),
            "category":       r.get("category"),
            "their_reply":    (r.get("raw_content") or "")[:400],
            "draft_response": r.get("llm_response_draft"),
            "received_at":    r.get("received_at"),
        })
    return result


@router.post("/replies/{reply_id}/approve")
def approve_reply(reply_id: str, body: dict = {}):
    reply = db_get_reply(reply_id)
    if not reply:
        return {"error": "Reply not found"}

    updates = {"approved": True}
    if body.get("edited_response"):
        updates["llm_response_draft"] = body["edited_response"]

    db_update_reply(reply_id, updates)
    send_single_reply.delay(reply_id)

    lead = db_get_lead_by_id(reply["lead_id"])
    return {
        "status":   "approved_and_queued",
        "reply_id": reply_id,
        "to":       lead["email"] if lead else None,
    }


@router.post("/replies/{reply_id}/reject")
def reject_reply(reply_id: str):
    reply = db_get_reply(reply_id)
    if not reply:
        return {"error": "Reply not found"}
    db_update_reply(reply_id, {"sent": True})
    return {"status": "dismissed"}


# ─── UNSUBSCRIBE ──────────────────────────────────────────────────────────────

@router.get("/unsubscribe")
def unsubscribe(email: str):
    if not email:
        return {"error": "No email provided"}
    db_add_suppression(email, "unsubscribe_link_click")
    return {"message": "You have been unsubscribed successfully.", "email": email}


# ─── LEADS ────────────────────────────────────────────────────────────────────

@router.post("/leads/")
def create_lead(body: dict):
    if not body.get("email"):
        return {"error": "email is required"}
    lead = db_create_lead({
        "first_name":      body.get("first_name", ""),
        "last_name":       body.get("last_name", ""),
        "email":           body["email"].lower().strip(),
        "business_name":   body.get("business_name", ""),
        "industry":        body.get("industry", ""),
        "location":        body.get("location", ""),
        "website":         body.get("website", ""),
        "source_platform": body.get("source_platform", "manual"),
        "specifications":  body.get("specifications", ""),
        "bundle_id":       body.get("bundle_id", "default"),
    })
    return {"id": lead["id"], "email": lead["email"], "bundle_id": lead["bundle_id"]}


@router.get("/leads/")
def list_leads(bundle_id: str = None):
    from database.db import db_list_leads
    leads = db_list_leads(bundle_id=bundle_id)
    return [
        {
            "id":       l["id"],
            "email":    l["email"],
            "name":     f"{l.get('first_name','') or ''} {l.get('last_name','') or ''}".strip(),
            "business": l.get("business_name"),
            "bundle_id": l.get("bundle_id"),
        }
        for l in leads
    ]