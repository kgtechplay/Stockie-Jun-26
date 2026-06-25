"""
Fetch global index OHLC data and persist it to Supabase.

Local CSV output is best-effort for developer runs. The database write is the
production path used by Render cron jobs.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.global_index_loader import (
    DEFAULT_GLOBAL_INDEX_OUTPUT_DIR,
    fetch_global_index_ohlc,
    write_global_index_ohlc_csv,
)

load_dotenv(project_root / ".env")


def run_load_daily_index_data(
    start_date: date | None = None,
    end_date: date | None = None,
    lookback: int = 7,
    write_local_output: bool = True,
) -> dict:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")

    resolved_end = end_date or date.today()
    resolved_start = start_date or (resolved_end - timedelta(days=lookback - 1))
    if resolved_start > resolved_end:
        raise ValueError("start_date must be <= end_date")

    print(f"Fetching global index OHLC {resolved_start} -> {resolved_end} ...")
    rows = fetch_global_index_ohlc(resolved_start, resolved_end)
    print(f"Fetched {len(rows)} global index OHLC rows")

    settings = get_settings()
    db = get_database_client(settings)
    if getattr(db, "db_kind", "") != "postgres":
        raise RuntimeError(
            "load_daily_index_data.py currently supports the Supabase/postgres provider only. "
            "Set DATABASE_PROVIDER=supabase."
        )

    db.connect()
    try:
        upserted = db.upsert_global_index_ohlc(rows)
    finally:
        db.close()
    print(f"Upserted {upserted} rows into GlobalIndexOhlc")

    local_output_path = None
    if write_local_output:
        try:
            local_output_path = write_global_index_ohlc_csv(rows, resolved_end)
            if local_output_path:
                print(f"Local output written to {local_output_path}")
        except Exception as exc:  # noqa: BLE001 - local output is optional for Render/cron.
            print(f"Skipping local global index OHLC output: {exc}")

    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "rows": len(rows),
        "upserted": upserted,
        "local_output_path": str(local_output_path) if local_output_path else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch global index OHLC data into Supabase")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD. Default: end - lookback + 1")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD. Default: today")
    parser.add_argument("--lookback", type=int, default=7, help="Calendar-day lookback when --start is omitted")
    parser.add_argument(
        "--no-local-output",
        action="store_true",
        help=f"Skip best-effort CSV output under {DEFAULT_GLOBAL_INDEX_OUTPUT_DIR}",
    )
    args = parser.parse_args()

    result = run_load_daily_index_data(
        start_date=date.fromisoformat(args.start) if args.start else None,
        end_date=date.fromisoformat(args.end) if args.end else None,
        lookback=args.lookback,
        write_local_output=not args.no_local_output,
    )
    print(result)


if __name__ == "__main__":
    main()