# services/personalization.py
# Updated for Supabase REST migration:
# - lead and campaign are now plain dicts (not SQLAlchemy ORM objects)
# - Access data with lead["field"] instead of lead.field
# - Everything else (Gemini logic, prompts, validation) stays identical

import google.generativeai as genai
import json
import re
from config import settings

genai.configure(api_key=settings.gemini_api_key)

pro_model   = genai.GenerativeModel(settings.gemini_pro_model)   # email writing
flash_model = genai.GenerativeModel(settings.gemini_model)        # classification

STEP_GUIDANCE = {
    1: "First contact. Lead with curiosity and value. Soft CTA — just spark interest, no pressure.",
    2: "Second touch. Briefly reference first email. Add a new angle or insight. Slightly more direct.",
    3: "Third touch. Acknowledge they're busy. Share a quick result or one-liner case study. Ask one specific question.",
    4: "Fourth touch. Direct meeting request. Light urgency. Make it easy to say yes.",
    5: "Final email. Honest breakup tone — tell them you won't keep emailing. Very direct Calendly link.",
}

BANNED_PHRASES = [
    "i hope this email finds you well",
    "i hope this finds you well",
    "just following up",
    "touching base",
    "circle back",
    "synergy",
    "leverage",
    "per my last email",
    "as per",
    "don't hesitate to reach out",
    "reach out to me",
]


def validate_email_output(body: str) -> bool:
    """Reject bad LLM output before sending"""
    body_lower = body.lower()
    for phrase in BANNED_PHRASES:
        if phrase in body_lower:
            return False
    if "{{" in body or "}}" in body:  # unfilled template tokens
        return False
    if len(body) < 150:               # too short
        return False
    return True


def generate_personalized_email(
    lead: dict,           # ← plain dict from Supabase, was ORM object
    campaign: dict,       # ← plain dict from Supabase, was ORM object
    sequence_step: int,
    previous_subjects: list = None,
) -> dict:
    """
    Generate a unique personalized email for this lead at this sequence step.

    lead and campaign are plain dicts — access with lead["field"] or lead.get("field")
    Returns: {"subject": "...", "body": "..."}
    """

    prev_context = ""
    if previous_subjects:
        prev_context = (
            f"\nPrevious subject lines already sent to this person: {', '.join(previous_subjects)}"
            "\nDo NOT repeat the same angle or hook."
        )

    # ── Pull lead fields from dict ────────────────────────────────────────────
    first_name      = lead.get("first_name") or "there"
    business_name   = lead.get("business_name") or "their business"
    industry        = lead.get("industry") or "not specified"
    location        = lead.get("location") or "not specified"
    website         = lead.get("website") or "none"
    specifications  = lead.get("specifications") or "none"
    source_platform = lead.get("source_platform") or "online research"
    lead_email      = lead.get("email", "")

    # ── Pull campaign fields from dict ────────────────────────────────────────
    campaign_name   = campaign.get("name", "Outreach Campaign")
    body_template   = campaign.get("body_template", "")

    unsubscribe_url = f"{settings.unsubscribe_base_url}?email={lead_email}"

    prompt = f"""You write cold outreach emails for a digital agency that builds AI automation systems.

Write ONE email now for this recipient:

RECIPIENT INFO:
- First name: {first_name}
- Business name: {business_name}
- Industry: {industry}
- Location: {location}
- Website: {website}
- Extra notes: {specifications}
- Found via: {source_platform}

CAMPAIGN: {campaign_name}
EMAIL STEP: {sequence_step} of 5
GUIDANCE FOR THIS STEP: {STEP_GUIDANCE[sequence_step]}
{prev_context}

BASE TEMPLATE (rewrite completely in your own words — do not copy):
{body_template}

STRICT RULES:
1. Never start with "I hope this email finds you well" or any variant
2. Never use: synergy, leverage, circle back, touch base, just following up
3. Sound like a real human, not a bot or a marketer
4. Reference something SPECIFIC about their business or industry
5. Maximum ONE call to action
6. Body must be 100 to 160 words
7. Sign off with only: {settings.primary_sender_name}
8. Include this EXACT unsubscribe footer at the very bottom (do not change it):
   <p style="font-size:11px;color:#aaa;margin-top:20px;">{settings.company_name} · {settings.company_address}<br><a href="{unsubscribe_url}" style="color:#aaa;">Unsubscribe</a></p>

Calendly booking link (use in steps 4 and 5 only): {settings.calendly_link}

RESPOND with ONLY a valid JSON object in this exact format — no explanation, no markdown:
{{"subject": "subject line here", "body": "complete HTML body here"}}"""

    response = pro_model.generate_content(prompt)
    raw = response.text.strip()

    # Remove markdown code fences if Gemini added them
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    result = json.loads(raw)

    if not validate_email_output(result.get("body", "")):
        raise ValueError(
            f"Generated email failed validation for "
            f"lead {lead.get('id')} at step {sequence_step}"
        )

    return result


def classify_reply(text: str) -> str:
    """
    Use Gemini Flash to classify an inbound reply.
    Returns one of: interested | question | objection |
                    out_of_office | unsubscribe | negative | unknown
    """

    prompt = f"""Classify this email reply into exactly ONE of these categories:

interested    → positive, wants to learn more, open to a call
question      → asking about pricing, process, how it works, timeline
objection     → engaging but pushing back (too busy, too expensive, not now)
out_of_office → automated vacation or OOO auto-reply
unsubscribe   → asking to stop emails, remove them, not interested
negative      → rude, hostile, aggressive
unknown       → cannot determine

Email reply to classify:
---
{text[:600]}
---

Respond with ONLY the single category word. Nothing else."""

    response = flash_model.generate_content(prompt)
    category = response.text.strip().lower()

    valid_categories = [
        "interested", "question", "objection",
        "out_of_office", "unsubscribe", "negative", "unknown"
    ]
    return category if category in valid_categories else "unknown"


def generate_reply_response(
    original_email: str,
    reply_content: str,
    reply_category: str,
    lead: dict,           # ← plain dict from Supabase, was ORM object
) -> str:
    """
    Generate a human-sounding response to an inbound reply.
    lead is a plain dict — access with lead.get("field")
    """

    # ── Pull lead fields from dict ────────────────────────────────────────────
    first_name    = lead.get("first_name") or "there"
    business_name = lead.get("business_name") or "their company"

    guidance = {
        "interested": (
            f"They're interested — great! Be warm but not over-eager. "
            f"Suggest specific times or share the Calendly link: {settings.calendly_link}"
        ),
        "question": (
            "Answer their question clearly and concisely. "
            "Then naturally guide toward a short call to discuss further."
        ),
        "objection": (
            "Acknowledge their concern genuinely — don't dismiss it. "
            "Briefly reframe the value. Make the next step feel very low commitment."
        ),
    }

    prompt = f"""You are writing a reply email on behalf of {settings.primary_sender_name} at {settings.company_name}.

A potential client has replied to your outreach email.

LEAD: {first_name} at {business_name}
THEIR REPLY: {reply_content[:800]}
REPLY TYPE: {reply_category}
YOUR GOAL: {guidance.get(reply_category, 'Respond naturally and helpfully.')}

THE EMAIL YOU SENT THEM:
{original_email[:400]}

Write a reply that:
- Is 50 to 90 words maximum
- Sounds like a real person wrote it quickly, not a template
- Does NOT start with "Great!" or "Thanks for reaching out!" or "I appreciate your reply"
- Feels personal and direct
- Signs off naturally as: {settings.primary_sender_name}
- No subject line — just the body text

Write ONLY the reply body. Nothing else."""

    response = pro_model.generate_content(prompt)
    return response.text.strip()