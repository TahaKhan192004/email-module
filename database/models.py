# database/models.py
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean,
    Text, Enum as SAEnum, ForeignKey, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
import uuid

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


# ─── ENUMS ────────────────────────────────────────────────────────────────────

class CampaignStatus(enum.Enum):
    DRAFT     = "draft"
    ACTIVE    = "active"
    PAUSED    = "paused"
    COMPLETED = "completed"


class EmailStatus(enum.Enum):
    PENDING      = "pending"
    SENT         = "sent"
    REPLIED      = "replied"
    BOUNCED      = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    FAILED       = "failed"


class ReplyCategory(enum.Enum):
    INTERESTED = "interested"
    QUESTION   = "question"
    OBJECTION  = "objection"
    OOO        = "out_of_office"
    UNSUBSCRIBE = "unsubscribe"
    NEGATIVE   = "negative"
    UNKNOWN    = "unknown"


# ─── TABLES ───────────────────────────────────────────────────────────────────

class Lead(Base):
    __tablename__ = "leads"

    id              = Column(String, primary_key=True, default=gen_uuid)
    first_name      = Column(String)
    last_name       = Column(String)
    email           = Column(String, unique=True, index=True, nullable=False)
    business_name   = Column(String)
    industry        = Column(String)
    location        = Column(String)
    phone           = Column(String)
    website         = Column(String)
    source_platform = Column(String)   # yelp, google_maps, manual, etc.
    specifications  = Column(Text)     # extra scraped notes
    bundle_id       = Column(String, index=True)
    status          = Column(String, default="new")
    created_at      = Column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id               = Column(String, primary_key=True, default=gen_uuid)
    name             = Column(String, nullable=False)
    subject_template = Column(String)
    body_template    = Column(Text)
    sender_name      = Column(String)
    sender_email     = Column(String)
    reply_to         = Column(String)
    status           = Column(SAEnum(CampaignStatus), default=CampaignStatus.DRAFT)
    lead_bundle_ids  = Column(JSON)
    created_at       = Column(DateTime, default=datetime.utcnow)

    emails = relationship("EmailRecord", back_populates="campaign")


class EmailRecord(Base):
    __tablename__ = "email_records"

    id           = Column(String, primary_key=True, default=gen_uuid)
    campaign_id  = Column(String, ForeignKey("campaigns.id"))
    lead_id      = Column(String, ForeignKey("leads.id"))
    sequence_step = Column(Integer)
    sender_email = Column(String)
    subject      = Column(String)
    body         = Column(Text)
    status       = Column(SAEnum(EmailStatus), default=EmailStatus.PENDING)
    message_id   = Column(String, index=True)   # Gmail message ID for threading
    scheduled_at = Column(DateTime)
    sent_at      = Column(DateTime)
    notes        = Column(String)

    campaign = relationship("Campaign", back_populates="emails")
    lead     = relationship("Lead")
    replies  = relationship("Reply", back_populates="email")


class Reply(Base):
    __tablename__ = "replies"

    id                = Column(String, primary_key=True, default=gen_uuid)
    email_id          = Column(String, ForeignKey("email_records.id"))
    lead_id           = Column(String, ForeignKey("leads.id"))
    raw_content       = Column(Text)
    category          = Column(SAEnum(ReplyCategory))
    llm_response_draft = Column(Text)
    approved          = Column(Boolean, default=False)
    sent              = Column(Boolean, default=False)
    received_at       = Column(DateTime, default=datetime.utcnow)
    responded_at      = Column(DateTime)

    email = relationship("EmailRecord", back_populates="replies")
    lead  = relationship("Lead")


class Suppression(Base):
    __tablename__ = "suppressions"

    id            = Column(String, primary_key=True, default=gen_uuid)
    email_address = Column(String, unique=True, index=True)
    reason        = Column(String)
    created_at    = Column(DateTime, default=datetime.utcnow)