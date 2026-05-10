# scripts/daily_fetch_stocks_universe.py
"""
Build / refresh stocks_universe.csv from Kite + NSE constituent lists.

Pipeline:
  Step 1 — NSE constituent lists
            Downloads Kite instrument data + sector from NSE's official index
            CSVs (Nifty 500 / Midcap 150 / Smallcap 250 / Microcap 250).
            Only rows with a confirmed sector are kept → nse_stocks.csv

  Step 2 — Yahoo Finance  (opt-in via --yfinance; skipped by default)
            For every stock NOT already covered by Step 1, queries yfinance
            serially (rate-limited) to get sector + industry.
            Only rows where yfinance returned a sector → yf_stocks.csv

  Step 3 — Merge
            NSE rows take priority. yfinance rows are added for symbols not
            in the NSE set. Result → stocks_universe.csv

Schema (all files, matches dbo.WatchedInstrument):
  tradingsymbol, exchange, name, instrument_token, segment, tick_size,
  lot_size, instrument_type, sector, industry, is_fo_enabled, is_active

Run order each trading day:
  1. daily_get_kite_access_token.py
  2. daily_fetch_stocks_universe.py        ← this script
  3. daily_optionInstrument_refresh.py
  4. daily_market_refresh.py

Usage:
    python scripts/daily_fetch_stocks_universe.py              # NSE only (fast, ~30s)
    python scripts/daily_fetch_stocks_universe.py --yfinance   # also enrich via Yahoo Finance
    python scripts/daily_fetch_stocks_universe.py --fo-only
    python scripts/daily_fetch_stocks_universe.py --output path/to/file.csv
    python scripts/daily_fetch_stocks_universe.py --yf-delay 0.6
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

_STOCK_TYPES = {"EQ", "BE"}
_INDEX_TYPES  = {"INDEX"}

CSV_FIELDS = [
    "tradingsymbol", "exchange", "name", "instrument_token",
    "segment", "tick_size", "lot_size", "instrument_type",
    "sector", "industry", "is_fo_enabled", "is_active",
]

_NSE_CONSTITUENT_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv",
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _our_type(kite_row: dict) -> str | None:
    seg   = (kite_row.get("segment") or "").strip().upper()
    ktype = (kite_row.get("instrument_type") or "").strip().upper()
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


def _write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Step 0: Kite base universe
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_kite_base(fo_only: bool = False) -> list[dict]:
    """
    Fetch all valid NSE instruments from Kite.
    Returns a list of row dicts with sector/industry = None (to be filled later).
    """
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

        # Hyphens in the tradingsymbol mark non-equity series even when Kite
        # labels them EQ: -SG (state govt bonds), -GB (gold bonds), -BZ/-ST
        # (suspended), -N (NCDs). All real equity symbols are purely alphanumeric.
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
        f"  Classified: {n_stocks:,} stocks  |  {n_indices:,} indices  "
        f"|  {n_fo:,} FO-enabled  |  {skipped:,} skipped"
    )
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Step 1: NSE constituent lists
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_nse_sector_map() -> dict[str, str]:
    """
    Download NSE index constituent CSVs → {tradingsymbol: sector}.
    NSE's 'Industry' column is the sector classification.
    First match wins (largest index listed first).
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
        label = url.rsplit("/", 1)[-1]
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            added = 0
            for row in csv.DictReader(io.StringIO(resp.text)):
                sym    = (row.get("Symbol") or "").strip().upper()
                sector = (row.get("Industry") or "").strip()
                if sym and sector and sym not in sector_map:
                    sector_map[sym] = sector
                    added += 1
            print(f"  {label}: {added:,} symbols")
        except Exception as exc:
            print(f"  [WARN] {label} failed: {exc}")

    return sector_map


def build_nse_dataset(base_rows: list[dict]) -> list[dict]:
    """
    Step 1: Apply NSE sector map to the Kite base.
    Returns only rows where a sector was found (indices always included).
    Writes nse_stocks.csv.
    """
    print("\n── Step 1: NSE constituent lists ──────────────────────────────")
    sector_map = _fetch_nse_sector_map()

    nse_rows: list[dict] = []
    for row in base_rows:
        r = dict(row)  # copy so base_rows stays pristine
        if r["instrument_type"] == "INDEX":
            nse_rows.append(r)
            continue
        sector = sector_map.get(r["tradingsymbol"].upper())
        if sector:
            r["sector"]   = sector
            r["industry"] = sector   # NSE does not publish sub-industry
            nse_rows.append(r)

    n_stock_rows = sum(1 for r in nse_rows if r["instrument_type"] == "STOCK")
    n_idx_rows   = sum(1 for r in nse_rows if r["instrument_type"] == "INDEX")
    print(f"  NSE dataset: {n_stock_rows:,} stocks with sector  |  {n_idx_rows:,} indices")

    out = project_root / "nse_stocks.csv"
    _write_csv(nse_rows, out)
    print(f"  Saved → {out}")
    return nse_rows


# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Yahoo Finance
# ──────────────────────────────────────────────────────────────────────────────

def build_yfinance_dataset(
    base_rows: list[dict],
    already_covered: set[str],
    delay: float = 0.4,
) -> list[dict]:
    """
    Step 2: Query yfinance serially for stocks not already covered by NSE.
    Returns only rows where yfinance returned a sector.
    Writes yf_stocks.csv.

    Serial + delay avoids Yahoo Finance's 'Invalid Crumb' 401 that occurs
    when parallel sessions exhaust the session token.
    """
    print("\n── Step 2: Yahoo Finance ───────────────────────────────────────")

    try:
        import logging
        import yfinance as yf
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        logging.getLogger("urllib3").setLevel(logging.CRITICAL)
        logging.getLogger("peewee").setLevel(logging.CRITICAL)
    except ImportError:
        print("  [WARN] yfinance not installed. Install with: pip install yfinance")
        return []

    candidates = [
        r for r in base_rows
        if r["instrument_type"] == "STOCK"
        and r["tradingsymbol"].upper() not in already_covered
    ]
    total = len(candidates)
    print(f"  {total:,} stocks to query (not covered by NSE lists)  delay={delay}s/req")
    if total == 0:
        return []

    yf_rows: list[dict] = []
    for i, row in enumerate(candidates, 1):
        try:
            info     = yf.Ticker(f"{row['tradingsymbol']}.NS").info
            sector   = info.get("sector")
            industry = info.get("industry")
            if sector:
                r = dict(row)
                r["sector"]   = sector
                r["industry"] = industry or sector
                yf_rows.append(r)
        except Exception:
            pass

        if i % 100 == 0 or i == total:
            print(f"  {i:,}/{total:,} queried  |  {len(yf_rows):,} with sector")

        if i < total:
            time.sleep(delay)

    out = project_root / "yf_stocks.csv"
    _write_csv(yf_rows, out)
    print(f"  Yahoo Finance dataset: {len(yf_rows):,} stocks  |  Saved → {out}")
    return yf_rows


# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Merge
# ──────────────────────────────────────────────────────────────────────────────

def merge_datasets(
    nse_rows: list[dict],
    yf_rows: list[dict],
    all_base_rows: list[dict],
) -> list[dict]:
    """
    Step 3: Merge NSE + yfinance datasets into a single deduplicated list.

    Priority:
      1. NSE row  — official sector classification, preferred
      2. yfinance row  — secondary, for stocks outside NSE index families
      3. Base Kite row — no sector data but included for completeness

    Deduplication key: tradingsymbol (case-insensitive).
    """
    print("\n── Step 3: Merge ───────────────────────────────────────────────")

    seen: dict[str, dict] = {}

    # 1. NSE rows (highest priority)
    for r in nse_rows:
        seen[r["tradingsymbol"].upper()] = r

    # 2. yfinance rows (fill gaps)
    yf_added = 0
    for r in yf_rows:
        key = r["tradingsymbol"].upper()
        if key not in seen:
            seen[key] = r
            yf_added += 1

    # 3. Remaining base rows (no sector — include for completeness)
    base_added = 0
    for r in all_base_rows:
        key = r["tradingsymbol"].upper()
        if key not in seen:
            seen[key] = r
            base_added += 1

    merged = list(seen.values())
    with_sector = sum(1 for r in merged if r["sector"])
    print(
        f"  NSE: {len(nse_rows):,} rows  +  yfinance: {yf_added:,} new  "
        f"+  no-sector: {base_added:,} rows"
    )
    print(
        f"  Total: {len(merged):,} rows  |  {with_sector:,} with sector  "
        f"|  {len(merged) - with_sector:,} without"
    )
    return merged


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

def run_fetch(
    fo_only: bool = False,
    use_yfinance: bool = False,
    yf_delay: float = 0.4,
    output_path: Path | None = None,
) -> Path:
    # ── Step 0: Kite base ──
    print("── Step 0: Kite instrument base ────────────────────────────────")
    base_rows = _fetch_kite_base(fo_only=fo_only)

    # ── Step 1: NSE constituent lists ──
    nse_rows = build_nse_dataset(base_rows)
    nse_covered = {r["tradingsymbol"].upper() for r in nse_rows if r["instrument_type"] == "STOCK"}

    # ── Step 2: Yahoo Finance (opt-in) ──
    yf_rows: list[dict] = []
    if use_yfinance:
        yf_rows = build_yfinance_dataset(base_rows, nse_covered, delay=yf_delay)
    else:
        print("\n── Step 2: Yahoo Finance  [skipped — pass --yfinance to enable] ──")

    # ── Step 3: Merge ──
    merged = merge_datasets(nse_rows, yf_rows, base_rows)

    out = output_path or (project_root / "stocks_universe.csv")
    _write_csv(merged, out)
    print(f"\nFinal output → {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily refresh of stocks_universe.csv from Kite + NSE constituent lists."
    )
    parser.add_argument(
        "--yfinance",
        action="store_true",
        help="Also enrich remaining stocks via Yahoo Finance (slow, opt-in).",
    )
    parser.add_argument(
        "--fo-only",
        action="store_true",
        help="Only include F&O-enabled stocks (indices always included).",
    )
    parser.add_argument(
        "--yf-delay",
        type=float,
        default=0.4,
        help="Seconds between Yahoo Finance requests (default: 0.4). Increase if you see 401s.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Final merged CSV path (default: stocks_universe.csv at project root)",
    )
    args = parser.parse_args()

    print("Daily stocks universe refresh")
    print(f"  yfinance : {'enabled' if args.yfinance else 'disabled (use --yfinance to enable)'}")
    print(f"  scope    : {'FO-only + indices' if args.fo_only else 'all stocks + indices'}")
    print()

    run_fetch(
        fo_only=args.fo_only,
        use_yfinance=args.yfinance,
        yf_delay=args.yf_delay,
        output_path=Path(args.output) if args.output else None,
    )


if __name__ == "__main__":
    main()
