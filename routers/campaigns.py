# routers/campaigns.py
from fastapi import APIRouter, Depends, HTTPException
from database.session import get_db
from database.models import Campaign, CampaignStatus, EmailRecord, Lead
from services.sequence_engine import enqueue_campaign_sequence
from services.suppression import is_suppressed, get_all_suppressions
from config import settings
from datetime import datetime
import uuid

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("/")
def create_campaign(body: dict, db=Depends(get_db)):
    """Create a new campaign (starts in DRAFT status)"""

    campaign = Campaign(
        id=str(uuid.uuid4()),
        name=body["name"],
        subject_template=body.get("subject_template", ""),
        body_template=body.get("body_template", "Hi {first_name}, I wanted to reach out..."),
        sender_name=body.get("sender_name", settings.primary_sender_name),
        sender_email=body.get("sender_email", settings.primary_sender_email),
        reply_to=body.get("reply_to", settings.reply_to_email),
        status=CampaignStatus.DRAFT,
        lead_bundle_ids=body.get("lead_bundle_ids", []),
        created_at=datetime.utcnow(),
    )
    db.add(campaign)
    db.commit()

    lead_count = db.query(Lead).filter(
        Lead.bundle_id.in_(campaign.lead_bundle_ids)
    ).count()

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": "draft",
        "lead_count": lead_count,
        "message": "Campaign created. Call /launch to start sending.",
    }


@router.post("/{campaign_id}/launch")
def launch_campaign(campaign_id: str, db=Depends(get_db)):
    """Launch a campaign — enqueues all email sequences"""

    campaign = db.query(Campaign).get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == CampaignStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Campaign already active")
    if not campaign.lead_bundle_ids:
        raise HTTPException(status_code=400, detail="No lead bundles selected")

    leads = db.query(Lead).filter(
        Lead.bundle_id.in_(campaign.lead_bundle_ids)
    ).all()

    if not leads:
        raise HTTPException(status_code=400, detail="No leads found in selected bundles")

    clean_leads = [l for l in leads if l.email and not is_suppressed(l.email, db)]
    lead_ids    = [l.id for l in clean_leads]

    campaign.status = CampaignStatus.ACTIVE
    db.commit()

    result = enqueue_campaign_sequence(campaign_id, lead_ids)

    # Trigger immediate processing
    from workers.send_tasks import process_pending_emails
    process_pending_emails.delay()

    return {
        "status": "launched",
        "campaign_id": campaign_id,
        "total_leads": len(leads),
        "suppressed_skipped": len(leads) - len(clean_leads),
        "emails_queued": result.get("queued", 0),
        "note": f"Step 1 emails send immediately, others spaced over ~21 days",
    }


@router.patch("/{campaign_id}/pause")
def pause_campaign(campaign_id: str, db=Depends(get_db)):
    campaign = db.query(Campaign).get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Not found")
    campaign.status = CampaignStatus.PAUSED
    db.commit()
    return {"status": "paused", "note": "Pending emails will not be sent while paused"}


@router.get("/{campaign_id}/analytics")
def get_analytics(campaign_id: str, db=Depends(get_db)):
    records = db.query(EmailRecord).filter(
        EmailRecord.campaign_id == campaign_id
    ).all()

    if not records:
        return {"message": "No emails found for this campaign yet"}

    total = len(records)
    sent  = [r for r in records if r.status.value not in ["pending", "failed"]]
    s     = max(len(sent), 1)   # avoid division by zero

    return {
        "total_queued":  total,
        "total_sent":    len(sent),
        "total_failed":  len([r for r in records if r.status.value == "failed"]),
        "reply_rate":    f"{round(len([r for r in records if r.status.value == 'replied']) / s * 100, 1)}%",
        "bounce_rate":   f"{round(len([r for r in records if r.status.value == 'bounced']) / s * 100, 1)}%",
        "by_step": {
            step: {
                "queued":  len([r for r in records if r.sequence_step == step]),
                "sent":    len([r for r in records if r.sequence_step == step and r.sent_at]),
                "replied": len([r for r in records if r.sequence_step == step and r.status.value == "replied"]),
                "failed":  len([r for r in records if r.sequence_step == step and r.status.value == "failed"]),
            }
            for step in range(1, 6)
        },
    }


@router.get("/")
def list_campaigns(db=Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status.value,
            "created_at": c.created_at,
        }
        for c in campaigns
    ]