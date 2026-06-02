"""
Seed WatchedInstrument with index entries fetched live from Kite.

Replaces the old Azure SQL dbo.StockDB dependency — instruments are now
resolved directly from Kite's NSE instruments dump and upserted via the
configured database (Supabase or Azure SQL).

Usage:
    python scripts/populate_watched_instruments.py
    python scripts/populate_watched_instruments.py --symbols NIFTY,BANKNIFTY
    python scripts/populate_watched_instruments.py --list
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from src.common.config import get_settings
from src.common.models import WatchedInstrument
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.kite_client import KiteClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KITE_TO_CANONICAL = {
    "NIFTY 50":   "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY"]


def fetch_index_instruments(kite_client: KiteClient, symbols: list[str]) -> list[WatchedInstrument]:
    canonical_to_kite = {v: k for k, v in KITE_TO_CANONICAL.items()}
    target_kite_names = {canonical_to_kite.get(s, s) for s in symbols}

    nse_instruments = kite_client.kite.instruments("NSE")
    results: list[WatchedInstrument] = []
    for inst in nse_instruments:
        ts = inst.get("tradingsymbol", "")
        if ts not in target_kite_names:
            continue
        segment = str(inst.get("segment", ""))
        if "INDICES" not in segment.upper():
            continue
        canonical = KITE_TO_CANONICAL.get(ts, ts)
        results.append(WatchedInstrument(
            tradingsymbol=canonical,
            exchange=inst.get("exchange", "NSE"),
            name=inst.get("name") or ts,
            instrument_token=int(inst["instrument_token"]),
            segment=segment,
            tick_size=float(inst["tick_size"]) if inst.get("tick_size") is not None else None,
            lot_size=int(inst["lot_size"]) if inst.get("lot_size") is not None else None,
            instrument_type="INDEX",
            is_fo_enabled=True,
            is_active=True,
        ))
    return results


def seed(symbols: list[str]) -> None:
    settings = get_settings()
    kite_client = KiteClient(settings)
    kite_client.authenticate()

    instruments = fetch_index_instruments(kite_client, symbols)
    if not instruments:
        logger.warning("No matching instruments found in Kite for: %s", symbols)
        return

    db = get_database_client(settings)
    db.connect()
    try:
        count = db.upsert_watched_instruments(instruments)
        logger.info("Upserted %d WatchedInstrument rows: %s", count,
                    [i.tradingsymbol for i in instruments])
    finally:
        db.close()


def list_watched() -> None:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        instruments = db.get_watched_instruments()
    finally:
        db.close()

    if not instruments:
        print("WatchedInstrument table is empty.")
        return
    print(f"{'tradingsymbol':<20} {'exchange':<8} {'type':<14} {'fo':>4} {'active':>6}")
    print("-" * 60)
    for w in instruments:
        print(
            f"{w.tradingsymbol:<20} {w.exchange:<8} {w.instrument_type:<14}"
            f" {int(w.is_fo_enabled):>4} {int(w.is_active):>6}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed WatchedInstrument from Kite instruments")
    parser.add_argument("--list", action="store_true", help="List current WatchedInstrument rows")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help=f"Comma-separated canonical symbols to seed. Default: {','.join(DEFAULT_SYMBOLS)}",
    )
    args = parser.parse_args()

    if args.list:
        list_watched()
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        seed(symbols)


if __name__ == "__main__":
    main()
