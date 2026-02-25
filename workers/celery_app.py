# workers/celery_app.py
from celery import Celery
from celery.schedules import crontab
from config import settings

celery_app = Celery(
    "crm_email_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.send_tasks",
        "workers.reply_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,   # one task at a time per worker
    task_acks_late=True,            # confirm task only after it completes
)

# Scheduled tasks (run by Celery Beat)
celery_app.conf.beat_schedule = {
    "process-pending-emails-every-10-min": {
        "task": "workers.send_tasks.process_pending_emails",
        "schedule": 600,   # every 10 minutes
    },
    "send-approved-replies-every-5-min": {
        "task": "workers.reply_tasks.auto_send_approved_replies",
        "schedule": 300,   # every 5 minutes
    },
}