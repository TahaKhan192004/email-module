# main.py
from fastapi import FastAPI
from routers import campaigns, replies

app = FastAPI(
    title="CRM Email Module",
    description="Email marketing automation with Gmail + Gemini + Supabase",
    version="1.0.0",
)

app.include_router(campaigns.router)
app.include_router(replies.router)


@app.get("/")
def root():
    return {
        "status": "running",
        "docs": "http://YOUR_VPS_IP:8000/docs",
    }


@app.get("/health")
def health_check():
    from database.session import engine
    from sqlalchemy import text
    from redis import Redis
    from config import settings

    # Check database
    db_status = "error"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    # Check Redis
    redis_status = "error"
    try:
        r = Redis.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        redis_status = "ok"
    except Exception as e:
        redis_status = f"error: {str(e)[:50]}"

    return {
        "database": db_status,
        "redis": redis_status,
        "gmail": settings.gmail_address,
        "daily_limit": settings.daily_send_limit,
        "auto_reply": settings.auto_reply_enabled,
    }