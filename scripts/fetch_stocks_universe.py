# scripts/fetch_stocks_universe.py
"""
Fetch all NSE equity stocks and indices from Kite, mark F&O eligibility from NFO,
and enrich sector/industry from NSE's official index constituent CSVs.

Output: stocks_universe.csv at the project root.
Schema matches dbo.WatchedInstrument:
  tradingsymbol, exchange, name, instrument_token, segment, tick_size, lot_size,
  instrument_type, sector, industry, is_fo_enabled, is_active

Sector enrichment strategy:
  1. Primary  — NSE constituent lists (Nifty 500 + Nifty Midcap 150 + Nifty
                Smallcap 250). Fast, no auth, covers all FO-eligible stocks.
  2. Optional — Yahoo Finance via --yfinance flag. Uses serial requests with a
                short sleep to avoid Yahoo's crumb expiry (Invalid Crumb 401).
                Adds coverage for stocks outside the Nifty index families.

Usage:
    python scripts/fetch_stocks_universe.py
    python scripts/fetch_stocks_universe.py --fo-only
    python scripts/fetch_stocks_universe.py --yfinance          # secondary enrichment
    python scripts/fetch_stocks_universe.py --no-nse            # skip NSE lists
    python scripts/fetch_stocks_universe.py --output path/to/file.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

from src.common.config import get_settings
from src.data_manager.kite_client import KiteClient

# Only proper NSE equity series.
# Excluded: BL (block deal), SM (SME), ST (suspended T2T), TB (T+1),
#           SG (state govt bonds), GB (sovereign gold bonds), etc.
_STOCK_TYPES = {"EQ", "BE"}
_INDEX_TYPES  = {"INDEX"}

CSV_FIELDS = [
    "tradingsymbol", "exchange", "name", "instrument_token",
    "segment", "tick_size", "lot_size", "instrument_type",
    "sector", "industry", "is_fo_enabled", "is_active",
]

# NSE publishes index constituent CSVs with symbol + industry columns.
# Listed largest-to-smallest so the first match (most prominent index) wins.
_NSE_CONSTITUENT_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv",
]


# ──────────────────────────────────────────────────────────────
# Classification
# ──────────────────────────────────────────────────────────────

def _our_type(kite_row: dict) -> str | None:
    seg   = (kite_row.get("segment") or "").strip().upper()
    ktype = (kite_row.get("instrument_type") or "").strip().upper()
    # Index check first — some index rows have ktype that looks like equity
    if seg == "NSE-INDEX" or ktype in _INDEX_TYPES:
        return "INDEX"
    if ktype in _STOCK_TYPES:
        return "STOCK"
    return None


def _build_fo_set(nfo_instruments: list[dict]) -> set[str]:
    fo: set[str] = set()
    for row in nfo_instruments:
        name = (row.get("name") or "").strip().upper()
        if name:
            fo.add(name)
    return fo


# ──────────────────────────────────────────────────────────────
# Sector enrichment: NSE constituent lists (primary)
# ──────────────────────────────────────────────────────────────

def _fetch_nse_sector_map() -> dict[str, str]:
    """
    Download NSE index constituent CSVs and return symbol → sector map.

    NSE's 'Industry' column is the sector classification (e.g. 'FINANCIAL SERVICES',
    'INFORMATION TECHNOLOGY', 'AUTOMOBILE AND AUTO COMPONENTS').
    Returns empty dict if all downloads fail.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.nseindia.com/",
    }

    sector_map: dict[str, str] = {}

    for url in _NSE_CONSTITUENT_URLS:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            reader = csv.DictReader(io.StringIO(resp.text))
            added = 0
            for row in reader:
                sym      = (row.get("Symbol") or "").strip().upper()
                industry = (row.get("Industry") or "").strip()
                if sym and industry and sym not in sector_map:
                    sector_map[sym] = industry
                    added += 1
            name = url.rsplit("/", 1)[-1]
            print(f"  {name}: {added:,} new symbols")
        except Exception as exc:
            name = url.rsplit("/", 1)[-1]
            print(f"  [WARN] {name} failed: {exc}")

    return sector_map


def _apply_nse_sectors(rows: list[dict], sector_map: dict[str, str]) -> int:
    """Apply NSE sector map to rows in-place. Returns number of rows updated."""
    updated = 0
    for row in rows:
        sector = sector_map.get(row["tradingsymbol"].upper())
        if sector:
            row["sector"] = sector
            row["industry"] = sector   # NSE doesn't provide a separate sub-industry
            updated += 1
    return updated


# ──────────────────────────────────────────────────────────────
# Sector enrichment: Yahoo Finance (optional secondary)
# ──────────────────────────────────────────────────────────────

def _enrich_yfinance(rows: list[dict], delay: float = 0.4) -> int:
    """
    Serial yfinance lookups for stocks that still have no sector after NSE enrichment.

    Serial (1 worker) + a small sleep between calls avoids Yahoo Finance's
    'Invalid Crumb' 401 that occurs when many parallel sessions share/exhaust
    the session token.

    Returns number of rows enriched.
    """
    try:
        import logging
        import yfinance as yf
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        logging.getLogger("urllib3").setLevel(logging.CRITICAL)
        logging.getLogger("peewee").setLevel(logging.CRITICAL)
    except ImportError:
        print("[WARN] yfinance not installed — skipping. Install with: pip install yfinance")
        return 0

    missing = [r for r in rows if r["instrument_type"] == "STOCK" and not r["sector"]]
    total = len(missing)
    if total == 0:
        print("  No stocks left without sector data — skipping yfinance.")
        return 0

    print(f"  Yahoo Finance fallback for {total:,} remaining stocks (serial, ~{delay}s/req)...")
    updated = 0
    for i, row in enumerate(missing, 1):
        try:
            info = yf.Ticker(f"{row['tradingsymbol']}.NS").info
            sector   = info.get("sector")
            industry = info.get("industry")
            if sector:
                row["sector"]   = sector
                row["industry"] = industry or sector
                updated += 1
        except Exception:
            pass

        if i % 50 == 0 or i == total:
            print(f"  {i:,}/{total:,} checked  |  {updated:,} enriched")

        if i < total:
            time.sleep(delay)

    return updated


# ──────────────────────────────────────────────────────────────
# Main fetch
# ──────────────────────────────────────────────────────────────

def run_fetch(
    fo_only: bool = False,
    use_nse: bool = True,
    use_yfinance: bool = False,
    output_path: Path | None = None,
) -> Path:
    settings = get_settings()
    kite = KiteClient(settings)
    kite.authenticate()

    print("Fetching NSE instruments from Kite...")
    nse_instruments = kite.kite.instruments("NSE")
    print(f"  {len(nse_instruments):,} total NSE instruments")

    print("Fetching NFO instruments from Kite (for F&O flag)...")
    nfo_instruments = kite.fetch_instruments_nfo()
    fo_set = _build_fo_set(nfo_instruments)
    print(f"  {len(nfo_instruments):,} NFO instruments  |  {len(fo_set):,} unique F&O underlyings")

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

        # NSE equity tradingsymbols are pure alphanumeric (+ & for stocks like M&MFIN).
        # A hyphen in the symbol marks non-equity series: -SG (state govt bonds),
        # -GB (sovereign gold bonds), -BZ (suspended), -ST (suspended T2T), -N (NCDs).
        # Kite sometimes assigns instrument_type=EQ to these, so the ktype filter alone
        # is not enough — we must also exclude by tradingsymbol pattern.
        if our_type == "STOCK" and "-" in sym:
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
    print(
        f"\nClassified: {n_stocks:,} stocks  |  {n_indices:,} indices  "
        f"|  {n_fo:,} FO-enabled  |  {skipped:,} skipped"
    )

    # ── Primary enrichment: NSE constituent lists ──
    if use_nse:
        print("\nDownloading NSE constituent lists for sector data...")
        sector_map = _fetch_nse_sector_map()
        nse_hit = _apply_nse_sectors(rows, sector_map)
        fo_covered = sum(1 for r in rows if r["is_fo_enabled"] and r["sector"])
        print(f"  NSE sectors applied: {nse_hit:,} stocks  |  {fo_covered}/{n_fo} FO stocks covered")

    # ── Secondary enrichment: Yahoo Finance (opt-in) ──
    if use_yfinance:
        print("\nYahoo Finance enrichment (serial, rate-limited)...")
        yf_hit = _enrich_yfinance(rows)
        print(f"  Yahoo Finance added sectors for {yf_hit:,} additional stocks")

    out = output_path or (project_root / "stocks_universe.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    total_with_sector = sum(1 for r in rows if r["sector"])
    print(f"\nWrote {len(rows):,} rows → {out}  |  {total_with_sector:,} with sector data")
    return out


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

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
        "--no-nse",
        action="store_true",
        help="Skip NSE constituent list downloads",
    )
    parser.add_argument(
        "--yfinance",
        action="store_true",
        help=(
            "Also enrich remaining stocks via Yahoo Finance (serial, rate-limited). "
            "Requires: pip install yfinance"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: stocks_universe.csv at project root)",
    )
    args = parser.parse_args()

    run_fetch(
        fo_only=args.fo_only,
        use_nse=not args.no_nse,
        use_yfinance=args.yfinance,
        output_path=Path(args.output) if args.output else None,
    )


if __name__ == "__main__":
    main()
