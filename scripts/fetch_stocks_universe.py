# scripts/fetch_stocks_universe.py
"""
Fetch all NSE equity stocks and indices from Kite, mark F&O eligibility from NFO,
and optionally enrich sector/industry via Yahoo Finance.

Output: stocks_universe.csv at the project root.
Schema matches dbo.WatchedInstrument:
  tradingsymbol, exchange, name, instrument_token, segment, tick_size, lot_size,
  instrument_type, sector, industry, is_fo_enabled, is_active

instrument_type values:
  STOCK        - NSE equity (EQ / BE series)
  INDEX        - NSE index (NIFTY 50, NIFTY BANK, NIFTY IT, etc.)

is_fo_enabled is derived by checking whether the symbol appears as an underlying
in the live NFO instruments dump (no separate API call needed).

Usage:
    python scripts/fetch_stocks_universe.py
    python scripts/fetch_stocks_universe.py --fo-only
    python scripts/fetch_stocks_universe.py --no-yfinance
    python scripts/fetch_stocks_universe.py --output path/to/file.csv
    python scripts/fetch_stocks_universe.py --workers 12
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

from src.common.config import get_settings
from src.data_manager.kite_client import KiteClient

# Kite instrument_type values that map to our STOCK / INDEX types
_EQ_TYPES = {"EQ", "BE", "BL", "SM", "ST", "TB", "N"}  # equity series on NSE
_INDEX_TYPES = {"INDEX"}

CSV_FIELDS = [
    "tradingsymbol", "exchange", "name", "instrument_token",
    "segment", "tick_size", "lot_size", "instrument_type",
    "sector", "industry", "is_fo_enabled", "is_active",
]


def _our_type(kite_row: dict) -> str | None:
    """Map a Kite instruments row to our instrument_type, or None to skip."""
    ktype = (kite_row.get("instrument_type") or "").strip().upper()
    seg   = (kite_row.get("segment") or "").strip().upper()

    if ktype in _EQ_TYPES:
        return "STOCK"
    if ktype in _INDEX_TYPES or seg == "NSE-INDEX":
        return "INDEX"
    return None


def _build_fo_set(nfo_instruments: list[dict]) -> set[str]:
    """
    Return the set of underlying tradingsymbols that have active NFO contracts.
    Kite's `name` field in the NFO dump is the underlying symbol (e.g. "RELIANCE",
    "NIFTY", "BANKNIFTY").
    """
    fo: set[str] = set()
    for row in nfo_instruments:
        name = (row.get("name") or "").strip().upper()
        if name:
            fo.add(name)
    return fo


def _yfinance_sector(tradingsymbol: str) -> tuple[str, str | None, str | None]:
    """Fetch sector and industry for one NSE symbol via yfinance. Never raises."""
    try:
        import yfinance as yf
        info = yf.Ticker(f"{tradingsymbol}.NS").info
        return tradingsymbol, info.get("sector"), info.get("industry")
    except Exception:
        return tradingsymbol, None, None


def _enrich_sectors(
    rows: list[dict],
    workers: int,
) -> None:
    """Mutates rows in-place, adding sector/industry from Yahoo Finance."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        print(
            "[WARN] yfinance is not installed — sector/industry will be blank.\n"
            "       Install with: pip install yfinance"
        )
        return

    stock_syms = [r["tradingsymbol"] for r in rows if r["instrument_type"] == "STOCK"]
    total = len(stock_syms)
    print(f"Enriching {total:,} stocks via Yahoo Finance ({workers} workers) — this takes a few minutes...")

    sector_map: dict[str, tuple[str | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_yfinance_sector, sym): sym for sym in stock_syms}
        done = 0
        hit = 0
        for fut in as_completed(futures):
            sym, sector, industry = fut.result()
            sector_map[sym] = (sector, industry)
            done += 1
            if sector:
                hit += 1
            if done % 200 == 0 or done == total:
                print(f"  {done:,}/{total:,} fetched  |  {hit:,} with sector data")

    for row in rows:
        pair = sector_map.get(row["tradingsymbol"])
        if pair:
            row["sector"], row["industry"] = pair


def run_fetch(
    fo_only: bool = False,
    use_yfinance: bool = True,
    output_path: Path | None = None,
    workers: int = 8,
) -> Path:
    settings = get_settings()
    kite = KiteClient(settings)
    kite.authenticate()

    print("Fetching NSE instruments from Kite...")
    nse_instruments = kite.kite.instruments("NSE")
    print(f"  {len(nse_instruments):,} total NSE instruments")

    print("Fetching NFO instruments from Kite (for F&O flag)...")
    nfo_instruments = kite.fetch_instruments_nfo()
    print(f"  {len(nfo_instruments):,} total NFO instruments")

    fo_set = _build_fo_set(nfo_instruments)
    print(f"  {len(fo_set):,} unique F&O underlyings identified")

    rows: list[dict] = []
    skipped = 0
    for inst in nse_instruments:
        our_type = _our_type(inst)
        if our_type is None:
            skipped += 1
            continue

        sym = (inst.get("tradingsymbol") or "").strip()
        if not sym:
            skipped += 1
            continue

        is_fo = sym.upper() in fo_set or (inst.get("name") or "").strip().upper() in fo_set

        if fo_only and our_type == "STOCK" and not is_fo:
            continue

        rows.append({
            "tradingsymbol":    sym,
            "exchange":         (inst.get("exchange") or "NSE").strip(),
            "name":             (inst.get("name") or "").strip() or None,
            "instrument_token": inst.get("instrument_token"),
            "segment":          (inst.get("segment") or "").strip() or None,
            "tick_size":        inst.get("tick_size"),
            "lot_size":         inst.get("lot_size"),
            "instrument_type":  our_type,
            "sector":           None,
            "industry":         None,
            "is_fo_enabled":    int(is_fo),
            "is_active":        1,
        })

    n_stocks  = sum(1 for r in rows if r["instrument_type"] == "STOCK")
    n_indices = sum(1 for r in rows if r["instrument_type"] == "INDEX")
    n_fo      = sum(1 for r in rows if r["is_fo_enabled"])
    print(f"\nClassified: {n_stocks:,} stocks  |  {n_indices:,} indices  |  {n_fo:,} FO-enabled  |  {skipped:,} skipped (bonds/ETFs etc.)")

    if use_yfinance:
        _enrich_sectors(rows, workers)

    out = output_path or (project_root / "stocks_universe.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows):,} rows → {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch all NSE stocks and indices to stocks_universe.csv"
    )
    parser.add_argument(
        "--fo-only",
        action="store_true",
        help="Only include F&O-enabled stocks (indices always included)",
    )
    parser.add_argument(
        "--no-yfinance",
        action="store_true",
        help="Skip Yahoo Finance sector/industry enrichment (faster, sector columns blank)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: stocks_universe.csv at project root)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel workers for Yahoo Finance lookups (default: 8)",
    )
    args = parser.parse_args()

    run_fetch(
        fo_only=args.fo_only,
        use_yfinance=not args.no_yfinance,
        output_path=Path(args.output) if args.output else None,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
