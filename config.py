# config.py
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    
    supabase_service_key: str
    

    # Redis / Celery
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    # Gmail
    gmail_address: str
    gmail_app_password: str

    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-1.5-flash"
    gemini_pro_model: str = "gemini-1.5-pro"

    # Company
    company_name: str
    company_address: str
    primary_sender_name: str
    primary_sender_email: str
    reply_to_email: str
    calendly_link: str
    unsubscribe_base_url: str

    # App
    app_env: str = "development"
    secret_key: str
    auto_reply_enabled: bool = False
    daily_send_limit: int = 20
    auto_reply_min_delay: int = 1800
    auto_reply_max_delay: int = 10800

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()