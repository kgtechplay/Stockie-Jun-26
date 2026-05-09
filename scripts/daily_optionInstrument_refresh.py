# scripts/load_option_instruments.py
"""
Load dbo.OptionInstrument for all active instruments in dbo.WatchedInstrument.

Works for both INDEX (NIFTY, BANKNIFTY) and STOCK (RELIANCE, HDFCBANK, etc.)
types by using the same filter_options_for_underlyings() function which matches
on the alphabetic prefix of the NFO tradingsymbol.

The NFO instruments dump is fetched once from Kite, then filtered in-memory
for all underlyings - no repeated API calls per instrument.

Usage:
    python scripts/load_option_instruments.py               # all active WatchedInstrument
    python scripts/load_option_instruments.py --type INDEX  # only INDEX
    python scripts/load_option_instruments.py --type STOCK  # only STOCK
    python scripts/load_option_instruments.py --dry-run     # count only, no DB write
"""

import sys
from pathlib import Path
from datetime import date

from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient
from src.data_manager.kite_client import KiteClient
from src.data_manager.kite_option_snapshot_builder import filter_options_for_underlyings

load_dotenv()


def run_load_option_instruments(
    instrument_type: str | None = None,
    dry_run: bool = False,
    underlyings: list[str] | None = None,
) -> dict:
    """
    Fetch NFO instruments from Kite and upsert into dbo.OptionInstrument
    for all active underlyings in WatchedInstrument.

    Args:
        instrument_type: Filter WatchedInstrument by type ('INDEX', 'STOCK', or None for all).
        dry_run: If True, print counts but do not write to DB.
        underlyings: Optional symbol allow-list. When provided, only these
            watched instruments are used.
    """
    settings = get_settings()

    # 1) Read underlyings from WatchedInstrument
    tdb = DatabaseClient(settings)
    tdb.connect()
    try:
        watched = tdb.get_watched_instruments(instrument_type=instrument_type)
    finally:
        tdb.close()

    if not watched:
        label = f"instrument_type={instrument_type}" if instrument_type else "all types"
        print(f"No active instruments found in WatchedInstrument ({label}).")
        return {"underlyings": [], "contracts_found": 0, "upserted": 0}

    if underlyings is not None:
        requested = {symbol.strip().upper() for symbol in underlyings if symbol.strip()}
        watched = [item for item in watched if item.tradingsymbol.upper() in requested]
        if not watched:
            print(f"No matching active watched instruments for: {sorted(requested)}")
            return {"underlyings": [], "contracts_found": 0, "upserted": 0}

    underlyings = [w.tradingsymbol for w in watched]
    print(f"Underlyings to load options for: {underlyings}")

    # 2) Authenticate Kite and fetch the full NFO dump (one API call)
    kite_client = KiteClient(settings)
    kite_client.authenticate()

    print("Fetching NFO instruments from Kite (this returns ~100k rows, takes ~5s)...")
    nfo_instruments = kite_client.fetch_instruments_nfo()
    print(f"Fetched {len(nfo_instruments):,} NFO instruments")

    # 3) Filter for all requested underlyings in one pass
    option_contracts = filter_options_for_underlyings(
        instruments_dump=nfo_instruments,
        underlyings=underlyings,
    )
    print(f"Matched {len(option_contracts):,} option contracts across {len(underlyings)} underlyings")

    # Show per-underlying breakdown
    from collections import Counter
    per_underlying = Counter(o.underlying for o in option_contracts)
    for sym, count in sorted(per_underlying.items()):
        print(f"  {sym}: {count:,} contracts")

    if dry_run:
        print("\n[DRY RUN] No changes written to DB.")
        return {"underlyings": underlyings, "contracts_found": len(option_contracts), "upserted": 0}

    if not option_contracts:
        print("Nothing to upsert.")
        return {"underlyings": underlyings, "contracts_found": 0, "upserted": 0}

    # 4) Upsert into dbo.OptionInstrument
    db = DatabaseClient(settings)
    db.connect()
    try:
        db.upsert_option_instruments(option_contracts)
        print(f"\nUpserted {len(option_contracts):,} option instruments into dbo.OptionInstrument.")
    finally:
        db.close()

    return {
        "underlyings": underlyings,
        "contracts_found": len(option_contracts),
        "upserted": len(option_contracts),
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Load OptionInstrument rows for all WatchedInstrument entries")
    parser.add_argument(
        "--type",
        dest="instrument_type",
        default=None,
        choices=["INDEX", "STOCK"],
        help="Filter by instrument_type (default: load all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count matching contracts without writing to DB",
    )
    args = parser.parse_args()

    result = run_load_option_instruments(
        instrument_type=args.instrument_type,
        dry_run=args.dry_run,
    )
    print(f"\nDone. contracts_found={result['contracts_found']:,}, upserted={result['upserted']:,}")


if __name__ == "__main__":
    main()
