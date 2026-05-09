"""
Seed dbo.WatchedInstrument with NIFTY and BANKNIFTY from dbo.StockDB.

Run once after applying migration 001. Safe to re-run (idempotent).

Usage:
    python scripts/populate_watched_instruments.py
    python scripts/populate_watched_instruments.py --list      # show current rows
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
from src.data_manager.db.database_client import DatabaseClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def seed(db: DatabaseClient) -> None:
    count = db.seed_watched_from_stockdb()
    if count == 0:
        logger.info("No new rows inserted ass NIFTY/BANKNIFTY already present in WatchedInstrument.")
    else:
        logger.info("Inserted %d new WatchedInstrument rows.", count)


def list_watched(db: DatabaseClient) -> None:
    instruments = db.get_watched_instruments()
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
    parser = argparse.ArgumentParser(description="Seed WatchedInstrument from StockDB")
    parser.add_argument("--list", action="store_true", help="List current WatchedInstrument rows")
    args = parser.parse_args()

    settings = get_settings()
    db = DatabaseClient(settings)
    db.connect()
    try:
        if args.list:
            list_watched(db)
        else:
            seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
