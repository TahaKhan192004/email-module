# CRM Email Module — Complete System Documentation

> **Stack:** FastAPI · Supabase · Gemini AI · Redis · Celery · Gmail SMTP
> **Server:** AWS EC2 Ubuntu 22.04 ·

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Environment Variables](#environment-variables)
5. [Database Tables](#database-tables)
6. [How Everything Works Together](#how-everything-works-together)
7. [API Endpoints](#api-endpoints)
8. [Daily Workflow](#daily-workflow)
9. [Deployment & Server Management](#deployment--server-management)
10. [Troubleshooting](#troubleshooting)
11. [Gmail Limits & Warm-Up Plan](#gmail-limits--warm-up-plan)
12. [Future: Migrating to SendGrid](#future-migrating-to-sendgrid)

---

## System Overview

An automated email marketing module that:
- Sends **personalized cold outreach emails** to leads using Gemini AI
- Runs a **5-step drip sequence** per lead over ~21 days
- Handles **reply classification** and drafts responses automatically
- Operates **24/7 on AWS EC2** via background workers
- Uses **Supabase REST API** (no direct Postgres — avoids IPv6 issues on free tier)

---

## Architecture

```
Browser / curl / Frontend
        │
        ▼
┌─────────────────────────┐
│   FastAPI (port 8000)   │  ← HTTP API — campaign management,
│   main.py               │    webhooks, reply queue
└────────────┬────────────┘
             │
    ┌────────▼────────┐
    │      Redis       │  ← Task queue broker
    │  localhost:6379  │    Celery writes jobs here
    └────────┬────────┘
             │
    ┌────────▼────────────────┐
    │    Celery Worker         │  ← Picks up jobs from Redis
    │    workers/send_tasks    │    Sends emails, processes replies
    │    workers/reply_tasks   │
    └────────┬────────────────┘
             │
    ┌────────▼────────┐
    │  Celery Beat     │  ← Scheduler — triggers tasks on schedule
    │  Every 10 min:   │    process_pending_emails
    │  Every 5 min:    │    auto_send_approved_replies
    └─────────────────┘
             │
    ┌────────▼────────────────────────────┐
    │   External Services                  │
    │                                      │
    │   Supabase REST  → Database (HTTPS)  │
    │   Gemini Pro     → Email generation  │
    │   Gemini Flash   → Reply classify    │
    │   Gmail SMTP     → Send emails       │
    └──────────────────────────────────────┘
```

---

## Project Structure

```
/home/ubuntu/email-module/
│
├── .env                          ← All secrets (never pushed to GitHub)
├── main.py                       ← FastAPI app entry point
├── config.py                     ← Settings loaded from .env
├── requirements.txt              ← Python dependencies
│
├── database/
│   ├── __init__.py
│   ├── supabase_client.py        ← Supabase client singleton (HTTPS, no IPv6)
│   └── db.py                     ← All database query functions (db_*)
│
├── routers/
│   ├── __init__.py
│   ├── campaigns.py              ← Campaign CRUD + launch + analytics
│   └── replies.py                ← Simulate reply, approval queue, leads, unsubscribe
│
├── services/
│   ├── __init__.py
│   ├── sender.py                 ← Gmail SMTP send functions
│   ├── personalization.py        ← Gemini email generation + reply classification
│   ├── sequence_engine.py        ← Schedule 5-step email sequences
│   └── suppression.py            ← Unsubscribe / bounce blocklist
│
├── workers/
│   ├── __init__.py
│   ├── celery_app.py             ← Celery config + beat schedule
│   ├── send_tasks.py             ← process_pending_emails task
│   └── reply_tasks.py            ← send_single_reply, auto_send_approved_replies
│
└── schemas/
    └── __init__.py
```

---

## Environment Variables

File location: `/home/ubuntu/email-module/.env`
Never commit this file to GitHub — it's in `.gitignore`.

```env
# ── Supabase (REST client — no direct Postgres) ──────────────────
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGci...          # service_role key (not anon)

# ── Redis & Celery ────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# ── Gmail SMTP ────────────────────────────────────────────────────
GMAIL_ADDRESS=yourcompany.outreach@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx    # 16-char App Password

# ── Gemini AI ─────────────────────────────────────────────────────
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-1.5-flash             # used for reply classification
GEMINI_PRO_MODEL=gemini-1.5-pro           # used for email generation

# ── Company Info ──────────────────────────────────────────────────
COMPANY_NAME=YourCompany
COMPANY_ADDRESS=123 Main St, City, State 12345
PRIMARY_SENDER_NAME=Alex
PRIMARY_SENDER_EMAIL=yourcompany.outreach@gmail.com
REPLY_TO_EMAIL=yourcompany.outreach@gmail.com
CALENDLY_LINK=https://calendly.com/yourname/30min
UNSUBSCRIBE_BASE_URL=http://3.128.179.239:8000/unsubscribe

# ── App Settings ──────────────────────────────────────────────────
APP_ENV=production
SECRET_KEY=your-random-secret-key
AUTO_REPLY_ENABLED=false                  # true = auto-send, false = approval queue
DAILY_SEND_LIMIT=20                       # increase weekly during warm-up
AUTO_REPLY_MIN_DELAY=1800                 # 30 min minimum before auto-reply sends
AUTO_REPLY_MAX_DELAY=10800                # 3 hour maximum
```

**Key notes:**
- `SUPABASE_SERVICE_KEY` must be the `service_role` key — not the `anon` key. Service key bypasses Row Level Security.
- `GMAIL_APP_PASSWORD` is a 16-character password generated at Google Account → Security → App Passwords. Not your Gmail login password.
- `GEMINI_API_KEY` must never be pushed to GitHub — rotate it immediately if it ever appears in a commit.
- `AUTO_REPLY_ENABLED=false` means all reply drafts go to the approval queue for your review before sending. Recommended to keep false.

---

## Database Tables

All tables live in Supabase. Connected via REST API over HTTPS — no direct TCP/IPv6 connection needed.

### leads
Stores every potential contact scraped or added manually.

| Column | Type | Description |
|--------|------|-------------|
| id | text PK | UUID |
| email | text UNIQUE | Required — duplicates rejected |
| first_name | text | Used in email personalization |
| last_name | text | |
| business_name | text | Used in email personalization |
| industry | text | Helps Gemini write relevant emails |
| location | text | Helps Gemini write relevant emails |
| website | text | Gemini references this |
| source_platform | text | google_maps, yelp, manual, etc. |
| specifications | text | Extra notes — Gemini reads these |
| bundle_id | text | Group label for campaign targeting |
| status | text | new, contacted, replied, etc. |
| created_at | timestamptz | |

### campaigns
One row per outreach campaign.

| Column | Type | Description |
|--------|------|-------------|
| id | text PK | UUID |
| name | text | Internal label |
| body_template | text | Core message — Gemini rewrites this per lead |
| subject_template | text | Optional subject hint |
| sender_name | text | Display name in email From field |
| sender_email | text | |
| reply_to | text | |
| status | text | draft → active → paused → completed |
| lead_bundle_ids | jsonb | Array of bundle IDs targeted |
| created_at | timestamptz | |

### email_records
One row per email per lead per sequence step. 5 rows created per lead on launch.

| Column | Type | Description |
|--------|------|-------------|
| id | text PK | UUID |
| campaign_id | text FK | |
| lead_id | text FK | |
| sequence_step | integer | 1 through 5 |
| status | text | pending → sent → replied / bounced / failed |
| subject | text | Populated after Gemini generates the email |
| body | text | Full HTML — populated after generation |
| message_id | text | Gmail Message-ID for email threading |
| scheduled_at | timestamptz | When Celery should send this |
| sent_at | timestamptz | When it was actually sent |
| notes | text | Error messages if failed |

### replies
One row per inbound reply received.

| Column | Type | Description |
|--------|------|-------------|
| id | text PK | UUID |
| email_id | text FK | The email_record this is a reply to |
| lead_id | text FK | |
| raw_content | text | Full reply text |
| category | text | interested / question / objection / out_of_office / unsubscribe / negative / unknown |
| llm_response_draft | text | Gemini-generated reply draft |
| approved | boolean | false = in approval queue, true = approved to send |
| sent | boolean | false = not sent yet, true = sent or dismissed |
| received_at | timestamptz | |
| responded_at | timestamptz | When reply was sent |

### suppressions
Permanent blocklist. Anyone here is never emailed again.

| Column | Type | Description |
|--------|------|-------------|
| id | text PK | UUID |
| email_address | text UNIQUE | Lowercase, trimmed |
| reason | text | unsubscribe_reply, hard_bounce, spam_complaint, unsubscribe_link_click |
| created_at | timestamptz | |

---

## How Everything Works Together

### Campaign Launch Flow
```
POST /campaigns/{id}/launch
        │
        ▼
Get all leads from selected bundle_ids
        │
        ▼
Filter out suppressed emails
        │
        ▼
For each clean lead × 5 steps:
  Create EmailRecord with status=pending
  Schedule step 1: now
  Schedule step 2: day 3-5
  Schedule step 3: day 7-9
  Schedule step 4: day 12-15
  Schedule step 5: day 18-21
        │
        ▼
Bulk insert all records to Supabase
        │
        ▼
Trigger process_pending_emails.delay()
```

### Email Send Flow (every 10 minutes via Celery Beat)
```
process_pending_emails task fires
        │
        ▼
Count emails sent today → check daily limit
        │
        ▼
Fetch pending emails where scheduled_at <= now
        │
        ▼
For each pending email:
  ├── Check: is lead suppressed?         → skip if yes
  ├── Check: has lead already replied?   → skip if yes
  ├── Check: did previous step bounce?   → skip if yes
  │
  ▼
Fetch lead + campaign from Supabase
        │
        ▼
Send to Gemini Pro with lead context
→ Returns unique subject + HTML body
        │
        ▼
Send via Gmail SMTP
        │
        ▼
Update email_record: status=sent, sent_at=now, message_id=...
        │
        ▼
Sleep 2-5 seconds (throttle)
→ Repeat for next email
```

### Reply Flow (Gmail — manual injection)
```
You check Gmail inbox → see a reply
        │
        ▼
POST /simulate-reply  {from_email, text}
        │
        ▼
Find lead by email in Supabase
        │
        ▼
Check for unsubscribe keywords → suppress immediately if found
        │
        ▼
Classify with Gemini Flash
→ interested / question / objection / ooo / negative / unknown
        │
        ▼
For interested / question / objection:
  Generate draft response with Gemini Pro
  Save to replies table (approved=false)
  → Appears in approval queue
        │
        ▼
Mark original email_record status=replied
→ All future sequence steps for this lead are automatically cancelled
        │
        ▼
You review queue: GET /replies/queue
        │
        ▼
POST /replies/{id}/approve  (optionally edit draft first)
        │
        ▼
send_single_reply task fires
→ Sends via Gmail SMTP
→ reply.sent = true, reply.responded_at = now
```

---



---

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Server status check |
| GET | `/health` | Database + Redis connection status |

---

### Leads

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/leads/` | Add a single lead |
| GET | `/leads/` | List all leads (add `?bundle_id=x` to filter) |

**Add a lead:**
```bash
curl -X POST http://leads/ \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Sarah",
    "email": "sarah@smithdental.com",
    "business_name": "Smith Dental Clinic",
    "industry": "Dentistry",
    "location": "New York, NY",
    "website": "smithdental.com",
    "source_platform": "google_maps",
    "specifications": "3 locations, 200+ Google reviews",
    "bundle_id": "dental-nyc-jan2025"
  }'
```

---

### Campaigns

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/campaigns/` | Create campaign (starts as draft) |
| GET | `/campaigns/` | List all campaigns |
| POST | `/campaigns/{id}/launch` | Launch — schedules all emails |
| GET | `/campaigns/{id}/analytics` | Stats: sent, replied, failed per step |
| PATCH | `/campaigns/{id}/pause` | Pause — stops pending emails sending |
| PATCH | `/campaigns/{id}/resume` | Resume a paused campaign |

**Create + launch:**
```bash
# 1. Create
curl -X POST http:///campaigns/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Dental Clinics Outreach Q1",
    "body_template": "We help dental clinics automate patient follow-ups using AI. Most clients see 30% fewer no-shows within the first month.",
    "lead_bundle_ids": ["dental-nyc-jan2025"]
  }'

# 2. Launch (use id from step 1 response)
curl -X POST http:///campaigns/CAMPAIGN_ID/launch
```

---

### Replies

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/simulate-reply` | Inject a reply manually (Gmail workaround) |
| GET | `/replies/queue` | View drafts awaiting approval |
| POST | `/replies/{id}/approve` | Approve (optionally edit) and send |
| POST | `/replies/{id}/reject` | Dismiss without sending |
| GET | `/unsubscribe?email=x` | Unsubscribe link handler |

**Simulate a reply:**
```bash
curl -X POST http:///simulate-reply \
  -H "Content-Type: application/json" \
  -d '{
    "from_email": "sarah@smithdental.com",
    "text": "Hi, this looks interesting! What kind of results have other dental practices seen?"
  }'
```

**Approve with edit:**
```bash
curl -X POST http:///replies/REPLY_ID/approve \
  -H "Content-Type: application/json" \
  -d '{"edited_response": "Your edited reply text here..."}'
```

**Approve as-is:**
```bash
curl -X POST http:///replies/REPLY_ID/approve \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## Daily Workflow

### First Time — Setting Up a Campaign

```
1. Add leads
   POST /leads/  (one at a time)
   OR bulk insert directly in Supabase Table Editor

2. Verify leads loaded
   GET /leads/?bundle_id=your-bundle-id

3. Create campaign
   POST /campaigns/

4. Launch
   POST /campaigns/{id}/launch

5. Verify emails are sending (check after 15 minutes)
   GET /campaigns/{id}/analytics
   → total_sent should be > 0
```

### Every Day

```
1. Check Gmail inbox (yourcompany.outreach@gmail.com)
   → Look for replies to your outreach emails

2. For each reply, inject it:
   POST /simulate-reply  {from_email, text}

3. Check approval queue:
   GET /replies/queue

4. For each item in queue:
   → Read their reply and Gemini's draft
   → If draft is good:        POST /replies/{id}/approve  {}
   → If draft needs editing:  POST /replies/{id}/approve  {edited_response: "..."}
   → If handling manually:    POST /replies/{id}/reject   {}

5. Check campaign performance:
   GET /campaigns/{id}/analytics
```

---

## Deployment & Server Management

### Server Details

| Item | Value |
|------|-------|
| Provider | AWS EC2 |
| OS | Ubuntu 22.04 |
| Project path | /home/ubuntu/email-module |
| Python | 3.11 (venv at /home/ubuntu/email-module/venv) |
| API port | 8000 |

### SSH Access

```bash
ssh -i your-key.pem ubuntu@3.128.179.239
```

### Service Management

Three permanent background services managed by systemd:

| Service | What it does |
|---------|-------------|
| crm-api | FastAPI web server on port 8000 |
| crm-worker | Celery worker — processes send/reply tasks |
| crm-beat | Celery scheduler — fires tasks every 5/10 min |

```bash
# Check status of all 3
sudo systemctl status crm-api crm-worker crm-beat

# Restart all (do this after every code change)
sudo systemctl restart crm-api crm-worker crm-beat

# Stop all
sudo systemctl stop crm-api crm-worker crm-beat

# Start all
sudo systemctl start crm-api crm-worker crm-beat

# View live logs
sudo journalctl -u crm-api -f
sudo journalctl -u crm-worker -f
sudo journalctl -u crm-beat -f

# View last 50 lines of logs
sudo journalctl -u crm-api -n 50 --no-pager
```

### Deploying Code Changes

Every time you push new code to GitHub:

```bash
# 1. SSH into EC2
ssh -i your-key.pem ubuntu@3.128.179.239

# 2. Go to project folder
cd ~/email-module

# 3. Pull latest code
git pull origin main

# 4. Activate venv and update dependencies (if requirements.txt changed)
source venv/bin/activate
pip install -r requirements.txt

# 5. Restart all services
sudo systemctl restart crm-api crm-worker crm-beat

# 6. Verify
sudo systemctl status crm-api crm-worker crm-beat
curl http://localhost:8000/health
```

### Port 8000 Already in Use

If you accidentally ran uvicorn manually and systemd can't start:

```bash
sudo fuser -k 8000/tcp
sudo systemctl restart crm-api
```

### Force Send Emails Right Now

Don't want to wait for the 10-minute Celery Beat cycle:

```bash
cd ~/email-module
source venv/bin/activate
python3 -c "
from workers.send_tasks import process_pending_emails
print(process_pending_emails())
"
```

### Reset a Failed Email Record

If an email failed and you want to retry it:

```bash
cd ~/email-module
source venv/bin/activate
python3 -c "
from database.supabase_client import supabase
from datetime import datetime

supabase.table('email_records') \
  .update({'status': 'pending', 'notes': None, 'scheduled_at': datetime.utcnow().isoformat()}) \
  .eq('id', 'YOUR-RECORD-ID-HERE') \
  .execute()
print('Reset done')
"
```

### Check What's Scheduled in Redis

```bash
redis-cli ping                                      # verify Redis running
redis-cli monitor                                   # watch tasks in real time
celery -A workers.celery_app inspect active         # tasks running right now
celery -A workers.celery_app inspect scheduled      # tasks queued
```

---

## Troubleshooting

### crm-api fails to start
```bash
sudo journalctl -u crm-api -n 50 --no-pager
# Look for the actual error — common causes:
# - Port 8000 in use    → sudo fuser -k 8000/tcp
# - Missing .env value  → nano .env and check all fields present
# - Import error        → check the file mentioned in the traceback
```

### Emails not sending (total_sent stays 0)
```bash
# 1. Check worker is running
sudo systemctl status crm-worker

# 2. Check scheduled times — emails may be scheduled for future
cd ~/email-module && source venv/bin/activate
python3 -c "
from database.supabase_client import supabase
r = supabase.table('email_records').select('sequence_step,scheduled_at,status').eq('status','pending').execute()
for x in r.data: print(x)
"

# 3. Force them to send now
python3 -c "
from database.supabase_client import supabase
from datetime import datetime
supabase.table('email_records').update({'scheduled_at': datetime.utcnow().isoformat()}).eq('status','pending').eq('sequence_step',1).execute()
print('Done')
"

# 4. Trigger send
python3 -c "from workers.send_tasks import process_pending_emails; print(process_pending_emails())"
```

### Gemini API key leaked / 403 error
```bash
# 1. Revoke old key at https://aistudio.google.com/app/apikey
# 2. Generate new key
# 3. Update .env
nano ~/email-module/.env   # update GEMINI_API_KEY

# 4. Restart
sudo systemctl restart crm-api crm-worker crm-beat

# 5. Prevent future leaks — ensure .env is gitignored
cat ~/email-module/.gitignore   # should contain .env
```

### Supabase connection error
```bash
# Verify you're using service_role key (not anon key)
# Test connection directly
cd ~/email-module && source venv/bin/activate
python3 -c "
from database.supabase_client import supabase
r = supabase.table('leads').select('id').limit(1).execute()
print('Connected OK:', r)
"
```

### Reply queue always empty
The queue only shows replies injected via `/simulate-reply`.
Gmail does not push replies to the app automatically.
You must check Gmail manually and call `/simulate-reply` for each reply you see.

### crm-beat not firing tasks
```bash
sudo journalctl -u crm-beat -n 30 --no-pager
# If beat is running but tasks aren't firing, check celery_app.py beat_schedule
# Restart beat
sudo systemctl restart crm-beat
```

---

## Gmail Limits & Warm-Up Plan

Gmail SMTP has sending limits. Increase `DAILY_SEND_LIMIT` in `.env` gradually:

| Week | DAILY_SEND_LIMIT | Monthly equivalent |
|------|-----------------|-------------------|
| 1 | 20 | ~600 |
| 2 | 50 | ~1,500 |
| 3 | 100 | ~3,000 |
| 4 | 150 | ~4,500 |
| 5+ | 200 | ~6,000 |

**Hard limits:**
- Free Gmail: 500/day absolute max
- Google Workspace: 2,000/day absolute max
- Recommended: stay well below — Gmail suspends accounts that hit the limit repeatedly

After changing `DAILY_SEND_LIMIT`:
```bash
sudo systemctl restart crm-api crm-worker crm-beat
```

---

## Future: Migrating to SendGrid

When you're ready to scale beyond Gmail, only **one file changes**: `services/sender.py`.

Everything else — Supabase, Gemini, Redis, Celery, all routers, all workers — stays identical.

**What you gain with SendGrid:**
- 50,000–100,000 emails/month
- Open and click tracking
- Automatic bounce/spam handling via webhooks
- Inbound parse (replies arrive automatically — no more simulate-reply)
- Domain-based sending (outreach@yourcompany.com instead of Gmail)

**Migration steps when ready:**
1. Buy a domain + set up DNS (SPF, DKIM, DMARC)
2. Create SendGrid account + authenticate domain
3. Replace `services/sender.py` with the SendGrid version
4. Add `SENDGRID_API_KEY` to `.env`
5. Remove `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
6. Configure SendGrid Inbound Parse webhook → `http://YOUR_IP:8000/webhooks/inbound-email`
7. Restart services

That's it — the rest of the system is already built for scale.

---

*Last updated: February 2026*
*Server: AWS EC2 · 3.128.179.239 · Ubuntu 22.04*
