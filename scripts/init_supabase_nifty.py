"""
Initialize Supabase/Postgres tables and seed NIFTY reference/history data.

Requires:
    SUPABASE_CONN_STR=postgresql://...
    DATABASE_PROVIDER=supabase
    Kite credentials/access token

Creates:
    WatchedInstrument, UnderlyingSnapshot, OptionInstrument, OptionSnapshot,
    OptionSnapshotCalc

Seeds:
    - WatchedInstrument with NIFTY index metadata from Kite
    - UnderlyingSnapshot for NIFTY from 2026-04-01 through 2026-05-18
    - OptionInstrument with active NIFTY option instruments from Kite NFO dump
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time as dtime
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.common.models import WatchedInstrument
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.kite_client import KiteClient
from src.data_manager.kite_option_snapshot_builder import filter_options_for_underlyings

load_dotenv(project_root / ".env")


def find_nifty_index(kite_client: KiteClient) -> dict:
    for inst in kite_client.kite.instruments("NSE"):
        if inst.get("tradingsymbol") == "NIFTY 50" and str(inst.get("segment", "")).endswith("INDICES"):
            return inst
    raise RuntimeError("Could not find NSE:NIFTY 50 in Kite instruments dump.")


def seed_nifty_watched(db, nifty_inst: dict) -> int:
    row = WatchedInstrument(
        tradingsymbol="NIFTY",
        exchange=nifty_inst.get("exchange", "NSE"),
        name=nifty_inst.get("name") or "NIFTY 50",
        instrument_token=int(nifty_inst["instrument_token"]),
        segment=nifty_inst.get("segment"),
        tick_size=float(nifty_inst["tick_size"]) if nifty_inst.get("tick_size") is not None else None,
        lot_size=int(nifty_inst["lot_size"]) if nifty_inst.get("lot_size") is not None else None,
        instrument_type="INDEX",
        is_fo_enabled=True,
        is_active=True,
    )
    return db.upsert_watched_instruments([row])


def backfill_nifty_underlying(db, kite_client: KiteClient, token: int, start_date: date, end_date: date) -> dict:
    candles = kite_client.kite.historical_data(
        token,
        datetime.combine(start_date, dtime(9, 15)),
        datetime.combine(end_date, dtime(15, 30)),
        interval="day",
        continuous=False,
        oi=False,
    )
    loaded_at = datetime.now()
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
    return db.upsert_underlying_snapshots(rows)


def seed_nifty_option_instruments(db, kite_client: KiteClient) -> int:
    nfo_instruments = kite_client.fetch_instruments_nfo()
    option_contracts = filter_options_for_underlyings(nfo_instruments, ["NIFTY"])
    db.upsert_option_instruments(option_contracts)
    return len(option_contracts)


def init_supabase_nifty(start_date: date, end_date: date) -> dict:
    settings = get_settings()
    if not settings.supabase_conn_str:
        raise RuntimeError("SUPABASE_CONN_STR is required.")

    kite_client = KiteClient(settings)
    kite_client.authenticate()

    db = get_database_client(settings)
    db.connect()
    try:
        db.create_core_tables()
        nifty_inst = find_nifty_index(kite_client)
        watched_count = seed_nifty_watched(db, nifty_inst)
        underlying_summary = backfill_nifty_underlying(
            db,
            kite_client,
            int(nifty_inst["instrument_token"]),
            start_date,
            end_date,
        )
        option_count = seed_nifty_option_instruments(db, kite_client)
    finally:
        db.close()

    return {
        "provider": "supabase",
        "watched_upserted": watched_count,
        "underlying_snapshot": underlying_summary,
        "option_instruments_upserted": option_count,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Supabase NIFTY tables/data.")
    parser.add_argument("--start", default="2026-04-01", help="UnderlyingSnapshot start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-18", help="UnderlyingSnapshot end date YYYY-MM-DD")
    args = parser.parse_args()
    result = init_supabase_nifty(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(result)


if __name__ == "__main__":
    main()
