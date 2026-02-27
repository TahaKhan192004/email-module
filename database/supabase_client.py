# database/supabase_client.py
# Replaces database/session.py entirely
# All DB access now goes through Supabase REST API (HTTPS) — no IPv6 needed

from supabase import create_client, Client
from config import settings
from functools import lru_cache


@lru_cache()
def get_supabase() -> Client:
    """
    Returns a singleton Supabase client.
    Uses SUPABASE_URL + SUPABASE_SERVICE_KEY from .env
    Service key is required (not anon key) so we can bypass Row Level Security
    and do full CRUD from the backend.
    """
    return create_client(settings.supabase_url, settings.supabase_service_key)


# Convenience shortcut used throughout the app
supabase: Client = get_supabase()