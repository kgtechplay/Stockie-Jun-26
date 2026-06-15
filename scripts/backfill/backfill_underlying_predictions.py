# scripts/backfill/backfill_underlying_predictions.py
"""
Backfill UnderlyingPredictionDaily for a date range using already-stored UnderlyingSnapshot rows.

Does NOT call Kite. Reads OHLCV from the DB and computes regime + strategy signals for each date.

Usage:
    python scripts/backfill/backfill_underlying_predictions.py --start 2026-01-01 --end 2026-06-15
    python scripts/backfill/backfill_underlying_predictions.py --start 2025-06-01 --end 2025-12-31 --underlying NIFTY
    python scripts/backfill/backfill_underlying_predictions.py --start 2025-01-01 --end 2026-06-15 --skip-existing
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from scripts.analysis.analysis_common import (
    build_historical_underlying_views,
    connect_db,
)
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

LOOKBACK_DAYS = 120
WARMUP_DAYS = 150
MIN_HISTORY_DAYS = 20


def view_to_db_row(view) -> dict:
    from dataclasses import asdict
    d = asdict(view)
    reasons = d.pop("reasons", None)
    warnings = d.pop("warnings", None)
    signals = d.pop("strategy_signals", None)
    return {
        **d,
        "reasons": reasons,
        "warnings": warnings,
        "strategy_signals": signals,
    }


def fetch_existing_dates(db, symbol: str, start_date: date, end_date: date) -> set[date]:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = '"UnderlyingPredictionDaily"' if is_postgres else "dbo.UnderlyingPredictionDaily"
    ph = "%s" if is_postgres else "?"
    sql = f"""
        SELECT trade_date FROM {table}
        WHERE symbol = {ph} AND trade_date >= {ph} AND trade_date <= {ph}
    """
    with db.conn.cursor() as cur:
        cur.execute(sql, (symbol, start_date, end_date) if is_postgres else [symbol, start_date, end_date])
        rows = cur.fetchall()
    return {r[0] if isinstance(r[0], date) else r[0].date() for r in rows}


def run_backfill_underlying_predictions(
    start_date: date,
    end_date: date,
    underlyings: list[str] | None = None,
    skip_existing: bool = False,
    batch_size: int = 50,
) -> dict:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()

    try:
        if underlyings:
            symbols = [s.upper() for s in underlyings]
        else:
            watched = db.get_watched_instruments(instrument_type="INDEX")
            symbols = [w.tradingsymbol for w in watched if w.tradingsymbol in ("NIFTY", "BANKNIFTY")]
            if not symbols:
                symbols = ["NIFTY"]

        summary = {}
        for symbol in symbols:
            print(f"\nBackfilling predictions for {symbol} ({start_date} to {end_date})...")

            existing: set[date] = set()
            if skip_existing:
                existing = fetch_existing_dates(db, symbol, start_date, end_date)
                print(f"  {symbol}: {len(existing)} dates already in DB, will skip")

            views = build_historical_underlying_views(
                db=db,
                underlying=symbol,
                start_date=start_date,
                end_date=end_date,
                lookback_days=LOOKBACK_DAYS,
                warmup_days=WARMUP_DAYS,
                min_history_days=MIN_HISTORY_DAYS,
            )

            if not views:
                print(f"  {symbol}: no views built (insufficient OHLCV history?)")
                summary[symbol] = {"built": 0, "upserted": 0}
                continue

            if skip_existing:
                views = [v for v in views if v.trade_date not in {d.isoformat() for d in existing}]

            rows = [view_to_db_row(v) for v in views]
            upserted = 0
            for i in range(0, len(rows), batch_size):
                batch = rows[i: i + batch_size]
                db.upsert_underlying_prediction_daily(batch)
                upserted += len(batch)
                print(f"  {symbol}: upserted {upserted}/{len(rows)} rows...")

            summary[symbol] = {"built": len(views), "upserted": upserted}
            print(f"  {symbol}: done — {upserted} prediction rows upserted")
    finally:
        db.close()

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": symbols,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill UnderlyingPredictionDaily from stored OHLCV.")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--underlying", action="append", dest="underlyings", help="Symbol(s). Can repeat.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip dates already in UnderlyingPredictionDaily")
    parser.add_argument("--batch-size", type=int, default=50, help="DB upsert batch size. Default: 50")
    args = parser.parse_args()

    result = run_backfill_underlying_predictions(
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        underlyings=[s.strip().upper() for s in args.underlyings] if args.underlyings else None,
        skip_existing=args.skip_existing,
        batch_size=args.batch_size,
    )
    print("\nBackfill complete.")
    print(result)


if __name__ == "__main__":
    main()
