# routers/campaigns.py
# Replaced SQLAlchemy with database/db.py functions
# Lead/campaign data is now plain dicts instead of ORM objects

from fastapi import APIRouter, HTTPException
from database.db import (
    db_create_campaign,
    db_get_campaign,
    db_list_campaigns,
    db_update_campaign_status,
    db_get_leads_by_bundle_ids,
    db_get_campaign_emails,
    db_is_suppressed,
)
from services.sequence_engine import enqueue_campaign_sequence
from config import settings
from datetime import datetime
import uuid

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("/")
def create_campaign(body: dict):
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    if not body.get("lead_bundle_ids"):
        raise HTTPException(status_code=400, detail="lead_bundle_ids is required")
    if not body.get("body_template"):
        raise HTTPException(status_code=400, detail="body_template is required")

    campaign = db_create_campaign({
        "id":               str(uuid.uuid4()),
        "name":             body["name"],
        "subject_template": body.get("subject_template", ""),
        "body_template":    body["body_template"],
        "sender_name":      body.get("sender_name", settings.primary_sender_name),
        "sender_email":     body.get("sender_email", settings.primary_sender_email),
        "reply_to":         body.get("reply_to", settings.reply_to_email),
        "status":           "draft",
        "lead_bundle_ids":  body["lead_bundle_ids"],
        "created_at":       datetime.utcnow().isoformat(),
    })

    leads      = db_get_leads_by_bundle_ids(body["lead_bundle_ids"])
    lead_count = len(leads)

    return {
        "id":         campaign["id"],
        "name":       campaign["name"],
        "status":     "draft",
        "lead_count": lead_count,
        "message":    "Campaign created. POST /campaigns/{id}/launch to start.",
    }


@router.get("/")
def list_campaigns():
    campaigns = db_list_campaigns()
    return [
        {
            "id":         c["id"],
            "name":       c["name"],
            "status":     c["status"],
            "created_at": c.get("created_at"),
        }
        for c in campaigns
    ]


@router.post("/{campaign_id}/launch")
def launch_campaign(campaign_id: str):
    campaign = db_get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["status"] == "active":
        raise HTTPException(status_code=400, detail="Campaign already active")
    if not campaign.get("lead_bundle_ids"):
        raise HTTPException(status_code=400, detail="No lead bundles on this campaign")

    leads       = db_get_leads_by_bundle_ids(campaign["lead_bundle_ids"])
    clean_leads = [l for l in leads if l.get("email") and not db_is_suppressed(l["email"])]
    lead_ids    = [l["id"] for l in clean_leads]

    if not lead_ids:
        raise HTTPException(status_code=400, detail="No eligible leads found")

    db_update_campaign_status(campaign_id, "active")

    result = enqueue_campaign_sequence(campaign_id, lead_ids)

    # Kick off immediate send cycle
    from workers.send_tasks import process_pending_emails
    process_pending_emails.delay()

    return {
        "status":             "launched",
        "campaign_id":        campaign_id,
        "total_leads":        len(leads),
        "suppressed_skipped": len(leads) - len(clean_leads),
        "emails_queued":      result.get("queued", 0),
        "note":               "Step 1 emails send within 10 minutes, others spaced over ~21 days",
    }


@router.patch("/{campaign_id}/pause")
def pause_campaign(campaign_id: str):
    campaign = db_get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db_update_campaign_status(campaign_id, "paused")
    return {"status": "paused"}


@router.patch("/{campaign_id}/resume")
def resume_campaign(campaign_id: str):
    campaign = db_get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db_update_campaign_status(campaign_id, "active")
    return {"status": "active"}


@router.get("/{campaign_id}/analytics")
def get_analytics(campaign_id: str):
    campaign = db_get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    records = db_get_campaign_emails(campaign_id)
    if not records:
        return {"message": "No emails found for this campaign yet"}

    total = len(records)
    sent  = [r for r in records if r["status"] not in ["pending", "failed"]]
    s     = max(len(sent), 1)

    def pct(count):
        return f"{round(count / s * 100, 1)}%"

    return {
        "campaign_name": campaign["name"],
        "total_queued":  total,
        "total_sent":    len(sent),
        "total_failed":  len([r for r in records if r["status"] == "failed"]),
        "reply_rate":    pct(len([r for r in records if r["status"] == "replied"])),
        "bounce_rate":   pct(len([r for r in records if r["status"] == "bounced"])),
        "by_step": {
            step: {
                "queued":  len([r for r in records if r["sequence_step"] == step]),
                "sent":    len([r for r in records if r["sequence_step"] == step and r.get("sent_at")]),
                "replied": len([r for r in records if r["sequence_step"] == step and r["status"] == "replied"]),
                "failed":  len([r for r in records if r["sequence_step"] == step and r["status"] == "failed"]),
            }
            for step in range(1, 6)
        },
    }