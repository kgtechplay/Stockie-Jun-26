from __future__ import annotations

from src.common.config import Settings
from src.data_manager.db.database_client import DatabaseClient
from src.data_manager.db.supabase_client import SupabaseDatabaseClient


def get_database_client(settings: Settings):
    provider = (settings.database_provider or "").lower()
    if provider == "supabase" or (settings.supabase_conn_str and provider != "azure_sql"):
        return SupabaseDatabaseClient(settings)
    return DatabaseClient(settings)
