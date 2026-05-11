import os
from pathlib import Path
from dotenv import load_dotenv

_repo_root_env = Path(__file__).resolve().parents[2] / ".env"
# Load repo-root .env deterministically so running from different working dirs still works.
# Do not override existing environment variables (so deployment env wins).
load_dotenv(dotenv_path=_repo_root_env if _repo_root_env.exists() else None, override=False)


def _normalize_azure_sql_conn_str(conn_str: str) -> str:
    """
    Normalize common Azure SQL ODBC connection string formats.

    Common mistake: missing the 'Driver=' prefix, e.g.
      "{ODBC Driver 18 for SQL Server};Server=..."
    pyodbc/ODBC expects:
      "Driver={ODBC Driver 18 for SQL Server};Server=..."
    """
    s = (conn_str or "").strip()
    if not s:
        return ""

    # If a driver is already specified (any case), keep as-is.
    if "driver=" in s.lower():
        return s

    # If it looks like it starts with a driver name in braces or plain text, prepend Driver=
    lowered = s.lower()
    if lowered.startswith("{odbc driver") or lowered.startswith("{sql server}"):
        return f"Driver={s}"
    if lowered.startswith("odbc driver") or lowered.startswith("sql server"):
        return f"Driver={s}"

    return s


class Settings:
    def __init__(self) -> None:
        self.kite_api_key = os.getenv("KITE_API_KEY", "")
        self.kite_api_secret = os.getenv("KITE_API_SECRET", "")

        # we don't store access token in env, we read it from file
        self.kite_access_token_path = Path(
            os.getenv("KITE_ACCESS_TOKEN_PATH", "kite_access_token.txt")
        )

        self.azure_sql_conn_str = _normalize_azure_sql_conn_str(
            os.getenv("AZURE_SQL_CONN_STR", "")
        )
        self.target_underlyings = os.getenv(
            "TARGET_UNDERLYINGS", "NIFTY,BANKNIFTY"
        ).split(",")


def get_settings() -> Settings:
    return Settings()
