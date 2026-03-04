"""
DB smoke test: loads repo-root .env and validates Azure SQL connectivity.

Usage (PowerShell):
  cd C:\\Cursor_Github\\OT_v1
  .\\.venv\\Scripts\\python scripts\\db_smoke.py
"""

from __future__ import annotations

from pathlib import Path

import os

import pyodbc
from dotenv import load_dotenv


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    load_dotenv(env_path)

    cs = (os.getenv("AZURE_SQL_CONN_STR") or "").strip()
    print("AZURE_SQL_CONN_STR_present=", bool(cs))
    if not cs:
        print("Missing AZURE_SQL_CONN_STR in .env")
        return 2

    try:
        cn = pyodbc.connect(cs, timeout=10)
        cur = cn.cursor()
        cur.execute("select 1")
        row = cur.fetchone()
        print("select_1=", row[0] if row else None)
        cn.close()
        print("OK")
        return 0
    except Exception as e:
        # Do not print the connection string itself (may contain secrets)
        print("CONNECT_FAIL:", type(e).__name__, str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



