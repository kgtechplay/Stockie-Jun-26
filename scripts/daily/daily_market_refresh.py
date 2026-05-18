"""
Daily market-data refresh for all active WatchedInstrument rows.

This is the single morning/manual entrypoint for:
  - UnderlyingSnapshot daily OHLC rows
  - UnderlyingCandle5m intraday candles
  - OptionSnapshot + OptionSnapshotCalc two-point option snapshots

Default date behavior:
  - Morning/pre-close run: refresh yesterday.
  - After market close: refresh today.
  - --lookback N extends the start date backward N calendar days from the target.

Usage:
    python scripts/daily_market_refresh.py
    python scripts/daily_market_refresh.py --lookback 3
    python scripts/daily_market_refresh.py --start 2026-05-08 --end 2026-05-08
    python scripts/daily_market_refresh.py --underlying NIFTY --underlying RELIANCE
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.kite_client import KiteClient


IST = pytz.timezone("Asia/Kolkata")
MARKET_CLOSE_IST = dtime(15, 35)


def _default_end_date() -> date:
    now_ist = datetime.now(IST)
    if now_ist.time() >= MARKET_CLOSE_IST:
        return now_ist.date()
    return now_ist.date() - timedelta(days=1)


def _resolve_date_range(
    start_raw: str | None,
    end_raw: str | None,
    lookback: int,
) -> tuple[date, date]:
    if lookback < 1:
        raise ValueError("--lookback must be >= 1")

    if start_raw or end_raw:
        if not start_raw or not end_raw:
            raise ValueError("--start and --end must be provided together")
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
    else:
        end_date = _default_end_date()
        start_date = end_date - timedelta(days=lookback - 1)

    if start_date > end_date:
        raise ValueError("start date must be <= end date")
    return start_date, end_date


def run_daily_market_refresh(
    start_date: date,
    end_date: date,
    underlyings: list[str] | None = None,
) -> dict:
    settings = get_settings()
    if settings.database_provider == "supabase" or settings.supabase_conn_str:
        return run_supabase_daily_market_refresh(start_date, end_date, underlyings)

    from src.services.backfill_service import BackfillRequest, BackfillService

    service = BackfillService()
    return service.run_backfill(
        BackfillRequest(
            start_date=start_date,
            end_date=end_date,
            underlyings=underlyings,
        )
    )


def run_supabase_daily_market_refresh(
    start_date: date,
    end_date: date,
    underlyings: list[str] | None = None,
) -> dict:
    symbols = underlyings or ["NIFTY"]
    if symbols != ["NIFTY"]:
        raise ValueError("Supabase daily_market_refresh currently supports only --underlying NIFTY")

    settings = get_settings()
    kite_client = KiteClient(settings)
    kite_client.authenticate()

    db = get_database_client(settings)
    db.connect()
    try:
        if hasattr(db, "create_core_tables"):
            db.create_core_tables()
        watched = db.get_watched_instruments(instrument_type="INDEX")
        nifty = next((w for w in watched if w.tradingsymbol == "NIFTY"), None)
        if not nifty or not nifty.instrument_token:
            raise RuntimeError("NIFTY is missing from WatchedInstrument. Run scripts/init_supabase_nifty.py first.")

        candles = kite_client.kite.historical_data(
            nifty.instrument_token,
            datetime.combine(start_date, dtime(9, 15)),
            datetime.combine(end_date, dtime(15, 30)),
            interval="day",
            continuous=False,
            oi=False,
        )
        loaded_at = datetime.now(IST).replace(tzinfo=None)
        rows = []
        for c in candles:
            c_dt = c["date"].replace(tzinfo=None)
            rows.append((
                "NIFTY",
                c_dt.date(),
                loaded_at,
                float(c["open"]) if c.get("open") is not None else None,
                float(c["high"]) if c.get("high") is not None else None,
                float(c["low"]) if c.get("low") is not None else None,
                float(c["close"]) if c.get("close") is not None else None,
                int(c["volume"]) if c.get("volume") is not None else None,
            ))
        summary = db.upsert_underlying_snapshots(rows)
    finally:
        db.close()

    return {
        "provider": "supabase",
        "underlyings": ["NIFTY"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "underlying_snapshot": summary,
    }


def main() -> None:
    load_dotenv(project_root / ".env")

    parser = argparse.ArgumentParser(
        description="Refresh daily underlying, 5m candles, and option snapshots for WatchedInstrument rows."
    )
    parser.add_argument("--start", help="Start date YYYY-MM-DD. Must be used with --end.")
    parser.add_argument("--end", help="End date YYYY-MM-DD. Must be used with --start.")
    parser.add_argument(
        "--lookback",
        type=int,
        default=1,
        help="Calendar days to refresh when --start/--end are omitted. Default: 1.",
    )
    parser.add_argument(
        "--underlying",
        action="append",
        dest="underlyings",
        help="Restrict to a symbol. Can be repeated.",
    )
    args = parser.parse_args()

    start_date, end_date = _resolve_date_range(args.start, args.end, args.lookback)
    symbols = [s.strip().upper() for s in args.underlyings] if args.underlyings else None

    print(f"Daily market refresh: {start_date} to {end_date}")
    print(f"Underlyings: {symbols or 'all active WatchedInstrument rows'}")

    result = run_daily_market_refresh(
        start_date=start_date,
        end_date=end_date,
        underlyings=symbols,
    )

    print("\nRefresh complete.")
    print(result)


if __name__ == "__main__":
    main()
