# scripts/run_migration.py
"""
Run the SQL migration file against the configured Azure SQL database.
Safe to run multiple times (all statements are idempotent).

Usage:
    python scripts/run_migration.py
    python scripts/run_migration.py --file src/data_manager/db/migrations/001_create_trading_system_tables.sql
"""

import sys
import re
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient

load_dotenv()

DEFAULT_MIGRATION = project_root / "src" / "data_manager" / "db" / "migrations" / "001_create_trading_system_tables.sql"


def run_migration(sql_file: Path) -> None:
    print(f"Running migration: {sql_file}")
    sql = sql_file.read_text(encoding="utf-8")

    # Split on GO (SQL Server batch separator) - case-insensitive, own line
    batches = [b.strip() for b in re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE | re.IGNORECASE)]
    batches = [b for b in batches if b]

    settings = get_settings()
    db = DatabaseClient(settings)
    db.connect()
    db.conn.autocommit = True

    cursor = db.conn.cursor()
    ok = skipped = failed = 0

    for i, batch in enumerate(batches, 1):
        try:
            cursor.execute(batch)
            ok += 1
        except Exception as e:
            err = str(e).lower()
            # Already-exists errors are expected on re-runs - treat as skipped
            if any(k in err for k in ("already an object named", "already exists", "duplicate key")):
                skipped += 1
            else:
                print(f"[ERROR] Batch {i} failed:\n{batch[:200]}\n  -> {e}")
                failed += 1

    cursor.close()
    db.close()
    print(f"Done. {ok} batches ok, {skipped} skipped (already exist), {failed} failed.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(DEFAULT_MIGRATION), help="Path to .sql migration file")
    args = parser.parse_args()
    run_migration(Path(args.file))
