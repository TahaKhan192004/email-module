# database/db.py
# Central data access layer — all queries go through Supabase REST client.
# Replaces SQLAlchemy ORM queries used throughout the original codebase.
#
# PATTERN:
#   Old: db.query(Lead).filter(Lead.email == email).first()
#   New: db_get_lead_by_email(email)
#
# Every function here makes an HTTPS call to Supabase — no TCP/IPv6 needed.

from database.supabase_client import supabase
from datetime import datetime
from typing import Optional
import uuid


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat()

def _uuid() -> str:
    return str(uuid.uuid4())

def _single(response) -> Optional[dict]:
    """Return first row or None from a Supabase response"""
    data = response.data
    return data[0] if data else None

def _all(response) -> list:
    """Return all rows from a Supabase response"""
    return response.data or []


# ─── LEADS ────────────────────────────────────────────────────────────────────

def db_create_lead(lead_data: dict) -> dict:
    lead_data["id"]         = lead_data.get("id", _uuid())
    lead_data["created_at"] = _now()
    lead_data["status"]     = lead_data.get("status", "new")
    response = supabase.table("leads").insert(lead_data).execute()
    return _single(response)


def db_get_lead_by_id(lead_id: str) -> Optional[dict]:
    response = supabase.table("leads").select("*").eq("id", lead_id).execute()
    return _single(response)


def db_get_lead_by_email(email: str) -> Optional[dict]:
    response = (
        supabase.table("leads")
        .select("*")
        .eq("email", email.strip().lower())
        .execute()
    )
    return _single(response)


def db_list_leads(bundle_id: str = None) -> list:
    query = supabase.table("leads").select("*")
    if bundle_id:
        query = query.eq("bundle_id", bundle_id)
    return _all(query.execute())


def db_get_leads_by_bundle_ids(bundle_ids: list) -> list:
    """Get all leads belonging to any of the given bundle IDs"""
    response = (
        supabase.table("leads")
        .select("*")
        .in_("bundle_id", bundle_ids)
        .execute()
    )
    return _all(response)


def db_update_lead_status(lead_id: str, status: str) -> dict:
    response = (
        supabase.table("leads")
        .update({"status": status})
        .eq("id", lead_id)
        .execute()
    )
    return _single(response)


# ─── CAMPAIGNS ────────────────────────────────────────────────────────────────

def db_create_campaign(campaign_data: dict) -> dict:
    campaign_data["id"]         = campaign_data.get("id", _uuid())
    campaign_data["created_at"] = _now()
    campaign_data["status"]     = campaign_data.get("status", "draft")
    response = supabase.table("campaigns").insert(campaign_data).execute()
    return _single(response)


def db_get_campaign(campaign_id: str) -> Optional[dict]:
    response = (
        supabase.table("campaigns")
        .select("*")
        .eq("id", campaign_id)
        .execute()
    )
    return _single(response)


def db_list_campaigns() -> list:
    response = (
        supabase.table("campaigns")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return _all(response)


def db_update_campaign_status(campaign_id: str, status: str) -> dict:
    response = (
        supabase.table("campaigns")
        .update({"status": status})
        .eq("id", campaign_id)
        .execute()
    )
    return _single(response)


# ─── EMAIL RECORDS ────────────────────────────────────────────────────────────

def db_create_email_record(record_data: dict) -> dict:
    record_data["id"]     = record_data.get("id", _uuid())
    record_data["status"] = record_data.get("status", "pending")
    response = supabase.table("email_records").insert(record_data).execute()
    return _single(response)


def db_bulk_create_email_records(records: list) -> list:
    """Insert many email records at once — much faster than one by one"""
    if not records:
        return []
    response = supabase.table("email_records").insert(records).execute()
    return _all(response)


def db_get_email_record(record_id: str) -> Optional[dict]:
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("id", record_id)
        .execute()
    )
    return _single(response)


def db_get_email_record_by_message_id(message_id: str) -> Optional[dict]:
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("message_id", message_id)
        .execute()
    )
    return _single(response)


def db_get_pending_emails_due(limit: int = 50) -> list:
    """Get PENDING emails scheduled for now or earlier"""
    now = _now()
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("status", "pending")
        .lte("scheduled_at", now)
        .limit(limit)
        .execute()
    )
    return _all(response)


def db_get_emails_sent_today() -> int:
    """Count emails already sent today"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    response = (
        supabase.table("email_records")
        .select("id", count="exact")
        .in_("status", ["sent", "replied"])
        .gte("sent_at", today)
        .execute()
    )
    return response.count or 0


def db_get_campaign_emails(campaign_id: str) -> list:
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return _all(response)


def db_check_lead_replied_in_campaign(lead_id: str, campaign_id: str) -> bool:
    response = (
        supabase.table("email_records")
        .select("id", count="exact")
        .eq("lead_id", lead_id)
        .eq("campaign_id", campaign_id)
        .eq("status", "replied")
        .execute()
    )
    return (response.count or 0) > 0


def db_get_previous_email_subjects(lead_id: str, campaign_id: str, before_step: int) -> list:
    response = (
        supabase.table("email_records")
        .select("subject")
        .eq("lead_id", lead_id)
        .eq("campaign_id", campaign_id)
        .lt("sequence_step", before_step)
        .not_.is_("subject", "null")
        .execute()
    )
    return [r["subject"] for r in _all(response) if r.get("subject")]


def db_get_previous_step_record(lead_id: str, campaign_id: str, step: int) -> Optional[dict]:
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("lead_id", lead_id)
        .eq("campaign_id", campaign_id)
        .eq("sequence_step", step - 1)
        .execute()
    )
    return _single(response)


def db_get_latest_sent_email_for_lead(lead_id: str) -> Optional[dict]:
    response = (
        supabase.table("email_records")
        .select("*")
        .eq("lead_id", lead_id)
        .eq("status", "sent")
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    return _single(response)


def db_update_email_record(record_id: str, updates: dict) -> dict:
    response = (
        supabase.table("email_records")
        .update(updates)
        .eq("id", record_id)
        .execute()
    )
    return _single(response)


# ─── REPLIES ──────────────────────────────────────────────────────────────────

def db_create_reply(reply_data: dict) -> dict:
    reply_data["id"]          = reply_data.get("id", _uuid())
    reply_data["received_at"] = _now()
    reply_data["approved"]    = reply_data.get("approved", False)
    reply_data["sent"]        = reply_data.get("sent", False)
    response = supabase.table("replies").insert(reply_data).execute()
    return _single(response)


def db_get_reply(reply_id: str) -> Optional[dict]:
    response = (
        supabase.table("replies")
        .select("*")
        .eq("id", reply_id)
        .execute()
    )
    return _single(response)


def db_get_approval_queue() -> list:
    """Replies waiting for human review — approved=false, sent=false"""
    response = (
        supabase.table("replies")
        .select("*, leads(first_name, email, business_name)")
        .eq("approved", False)
        .eq("sent", False)
        .order("received_at", desc=True)
        .execute()
    )
    return _all(response)


def db_get_approved_unsent_replies() -> list:
    response = (
        supabase.table("replies")
        .select("*")
        .eq("approved", True)
        .eq("sent", False)
        .execute()
    )
    return _all(response)


def db_update_reply(reply_id: str, updates: dict) -> dict:
    response = (
        supabase.table("replies")
        .update(updates)
        .eq("id", reply_id)
        .execute()
    )
    return _single(response)


# ─── SUPPRESSIONS ─────────────────────────────────────────────────────────────

def db_is_suppressed(email: str) -> bool:
    response = (
        supabase.table("suppressions")
        .select("id", count="exact")
        .eq("email_address", email.strip().lower())
        .execute()
    )
    return (response.count or 0) > 0


def db_add_suppression(email: str, reason: str) -> Optional[dict]:
    email = email.strip().lower()
    if db_is_suppressed(email):
        return None   # already suppressed — safe to call multiple times
    response = supabase.table("suppressions").insert({
        "id":            _uuid(),
        "email_address": email,
        "reason":        reason,
        "created_at":    _now(),
    }).execute()
    return _single(response)


def db_list_suppressions() -> list:
    response = (
        supabase.table("suppressions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return _all(response)