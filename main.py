# main.py
from fastapi import FastAPI
from routers import campaigns, replies

app = FastAPI(
    title="CRM Email Module",
    description="Email marketing automation — Supabase REST + Gemini + Gmail",
    version="2.0.0",
)

app.include_router(campaigns.router)
app.include_router(replies.router)


@app.get("/")
def root():
    return {"status": "running", "docs": "http://localhost:8000/docs"}


@app.get("/health")
def health_check():
    from config import settings

    # Test Supabase connection via REST (no IPv6 needed)
    db_status = "error"
    try:
        from database.supabase_client import supabase
        # A lightweight query — just fetch 1 row from any table
        supabase.table("leads").select("id").limit(1).execute()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:80]}"

    # Test Redis
    redis_status = "error"
    try:
        from redis import Redis
        r = Redis.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        redis_status = "ok"
    except Exception as e:
        redis_status = f"error: {str(e)[:80]}"

    return {
        "database":    db_status,
        "redis":       redis_status,
        "gmail":       settings.gmail_address,
        "daily_limit": settings.daily_send_limit,
        "auto_reply":  settings.auto_reply_enabled,
        "connection":  "supabase-rest (no IPv6 needed)",
    }