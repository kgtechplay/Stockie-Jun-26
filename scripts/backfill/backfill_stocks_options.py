# scripts/backfill_stocks_options.py
"""
Backfill dbo.OptionSnapshot + dbo.OptionSnapshotCalc for STOCK option instruments.

For each row in dbo.OptionInstrument (underlying IN HDFCBANK, RELIANCE, etc.):
  - Date range: max(--start, fetch_date) to min(expiry, today)
  - Coverage-aware: skips date ranges already present in OptionSnapshot
  - 2 snapshots per trading day:
      09:15 IST  ->  open  price of the day candle
      15:15 IST  ->  close price of the day candle
  - Underlying price uses the stock's own NSE day candle (open/close)
  - Uses Kite interval="day" (one API call per option covers the full range)

Usage:
    python scripts/backfill_stocks_options.py
    python scripts/backfill_stocks_options.py --start 2025-01-01 --end 2026-04-30
    python scripts/backfill_stocks_options.py --underlying HDFCBANK,RELIANCE
"""

import sys
import time
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient
from src.common.models import WatchedInstrument, OptionInstrument, OptionData
from src.data_manager.kite_client import KiteClient
from src.data_manager.kite_option_snapshot_builder import (
    _years_to_expiry,
    _implied_volatility,
    _bs_greeks,
)

load_dotenv()

RISK_FREE_RATE = 0.07
API_DELAY_SECONDS = 0.4
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2
INSERT_BATCH_SIZE = 500   # flush OptionData to DB after this many rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_stock_tokens(
    kite_client: KiteClient,
    instruments: List[WatchedInstrument],
) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    need_lookup = []
    for inst in instruments:
        if inst.instrument_token:
            mapping[inst.tradingsymbol] = inst.instrument_token
        else:
            need_lookup.append(inst)
    if need_lookup:
        need_set = {inst.tradingsymbol for inst in need_lookup}
        print(f"Looking up Kite tokens for stocks: {need_set}")
        nse_instr = kite_client.kite.instruments("NSE")
        for ki in nse_instr:
            ts = ki.get("tradingsymbol", "")
            if ts in need_set and ts not in mapping:
                mapping[ts] = ki["instrument_token"]
        missing = need_set - mapping.keys()
        if missing:
            print(f"[WARN] Could not resolve Kite tokens for: {missing}")
    return mapping


def _fetch_underlying_day_candles(
    kite_client: KiteClient,
    token: int,
    start_date: date,
    end_date: date,
) -> Dict[date, dict]:
    """Fetch day-interval candles for a stock. Returns {trade_date: candle}."""
    try:
        candles = kite_client.kite.historical_data(
            token,
            datetime.combine(start_date, dtime(9, 0)),
            datetime.combine(end_date, dtime(16, 0)),
            interval="day",
            continuous=False,
            oi=False,
        )
    except Exception as e:
        print(f"[WARN] Failed fetching stock day candles (token={token}): {e}")
        return {}
    result: Dict[date, dict] = {}
    for c in candles:
        c_dt = c["date"]
        if hasattr(c_dt, "tzinfo"):
            c_dt = c_dt.replace(tzinfo=None)
        td = c_dt.date() if isinstance(c_dt, datetime) else c_dt
        result[td] = c
    return result


def _bulk_query_coverage(
    db: DatabaseClient,
    instrument_ids: List[int],
) -> Dict[int, Tuple[Optional[date], Optional[date]]]:
    """
    Bulk-query OptionSnapshot for min/max snapshot date per instrument.
    Returns {instrument_id: (min_date, max_date)}.  Missing = no snapshots yet.
    """
    coverage: Dict[int, Tuple[Optional[date], Optional[date]]] = {}
    if not instrument_ids:
        return coverage
    cursor = db.conn.cursor()
    chunk_size = 1000
    for i in range(0, len(instrument_ids), chunk_size):
        chunk = instrument_ids[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor.execute(
            f"""
            SELECT option_instrument_id,
                   CAST(MIN(snapshot_time) AS DATE),
                   CAST(MAX(snapshot_time) AS DATE)
            FROM dbo.OptionSnapshot
            WHERE option_instrument_id IN ({placeholders})
            GROUP BY option_instrument_id
            """,
            chunk,
        )
        for row in cursor.fetchall():
            iid = int(row[0])
            min_d = row[1].date() if isinstance(row[1], datetime) else row[1]
            max_d = row[2].date() if isinstance(row[2], datetime) else row[2]
            coverage[iid] = (min_d, max_d)
    cursor.close()
    return coverage


def _compute_gaps(
    db_min: Optional[date],
    db_max: Optional[date],
    start_date: date,
    end_date: date,
) -> List[Tuple[date, date]]:
    if db_min is None:
        return [(start_date, end_date)]
    gaps = []
    if db_min > start_date:
        gaps.append((start_date, db_min - timedelta(days=1)))
    if db_max < end_date:
        gaps.append((db_max + timedelta(days=1), end_date))
    return gaps


def _fetch_option_day_candles(
    kite_client: KiteClient,
    token: int,
    tradingsymbol: str,
    gap_start: date,
    gap_end: date,
    token_expired_flag: list,
) -> Optional[List[dict]]:
    """Fetch day-interval candles for one option over a gap range. Returns None on fatal error."""
    token_reload_attempted = False
    for retry in range(MAX_RETRIES):
        try:
            return kite_client.kite.historical_data(
                token,
                datetime.combine(gap_start, dtime(9, 0)),
                datetime.combine(gap_end, dtime(16, 0)),
                interval="day",
                continuous=False,
                oi=True,
            )
        except Exception as e:
            exc_type = type(e).__name__
            err = str(e).lower()
            is_rate_limit = "too many requests" in err or "rate limit" in err
            # Only treat Kite's TokenException as an access-token problem.
            # InputException("invalid token") means the instrument token is stale — skip, don't stop.
            is_bad_access_token = exc_type == "TokenException" or "access token" in err
            if is_bad_access_token:
                if not token_reload_attempted:
                    token_reload_attempted = True
                    time.sleep(0.5)
                    if kite_client.re_authenticate():
                        continue
                    print("[ERROR] Token reload failed. Run: python scripts/get_kite_access_token.py")
                token_expired_flag[0] = True
                return None
            elif is_rate_limit and retry < MAX_RETRIES - 1:
                wait = RETRY_DELAY_BASE * (2 ** retry)
                print(f"[WARN] Rate limited for {tradingsymbol}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"[WARN] Failed fetching {tradingsymbol} ({exc_type}): {e}")
                return None
    return None


def _build_option_data(
    inst: OptionInstrument,
    db_id: int,
    opt_candle: dict,
    underlying_candle: dict,
) -> List[OptionData]:
    """
    Produce 2 OptionData rows from one day candle pair:
      09:15 IST -> open prices
      15:15 IST -> close prices
    """
    c_dt = opt_candle["date"]
    if hasattr(c_dt, "tzinfo"):
        c_dt = c_dt.replace(tzinfo=None)
    trade_date = c_dt.date() if isinstance(c_dt, datetime) else c_dt

    opt_type = "C" if inst.instrument_type == "CE" or inst.tradingsymbol.endswith("CE") else "P"
    rows: List[OptionData] = []

    for snap_time, opt_px, und_px in [
        (dtime(9, 15),  opt_candle.get("open"),  underlying_candle.get("open")),
        (dtime(15, 15), opt_candle.get("close"), underlying_candle.get("close")),
    ]:
        if opt_px is None or und_px is None:
            continue
        opt_px = float(opt_px)
        S = float(und_px)
        snap_dt = datetime.combine(trade_date, snap_time)
        T = _years_to_expiry(inst.expiry, snap_dt)

        iv = delta = gamma = theta = vega = None
        if S > 0 and opt_px > 0 and T > 0:
            iv_val = _implied_volatility(opt_px, S, float(inst.strike), T, RISK_FREE_RATE, 0.0, opt_type)
            if iv_val is not None:
                g = _bs_greeks(S, float(inst.strike), T, RISK_FREE_RATE, 0.0, iv_val, opt_type)
                iv, delta, gamma, theta, vega = iv_val, g["delta"], g["gamma"], g["theta"], g["vega"]

        rows.append(OptionData(
            option_instrument_id=db_id,
            snapshot_time=snap_dt,
            underlying_price=S,
            last_price=opt_px,
            bid_price=None, bid_qty=None, ask_price=None, ask_qty=None,
            volume=int(opt_candle["volume"]) if opt_candle.get("volume") is not None else None,
            open_interest=int(opt_candle["oi"]) if opt_candle.get("oi") is not None else None,
            implied_volatility=iv,
            delta=delta, gamma=gamma, theta=theta, vega=vega,
        ))

    return rows


def _to_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Main backfill function
# ---------------------------------------------------------------------------

def run_backfill_stocks_options(
    global_start: Optional[date] = None,
    global_end: Optional[date] = None,
    underlyings: Optional[List[str]] = None,
) -> Dict[str, object]:
    settings = get_settings()
    today = date.today()
    eff_end = min(global_end, today) if global_end else today

    # Load STOCK watched instruments
    tdb = DatabaseClient(settings)
    tdb.connect()
    try:
        watched = tdb.get_watched_instruments(instrument_type="STOCK")
    finally:
        tdb.close()

    if underlyings:
        target_set = {u.upper() for u in underlyings}
        watched = [w for w in watched if w.tradingsymbol in target_set]

    if not watched:
        print("No active STOCK instruments found in WatchedInstrument.")
        return {"underlyings": [], "option_instruments_loaded": 0, "snapshots_inserted": 0}

    target_underlyings = [w.tradingsymbol for w in watched]
    print(f"Stock underlyings: {', '.join(target_underlyings)}")

    kite_client = KiteClient(settings)
    kite_client.authenticate()
    stock_tokens = _resolve_stock_tokens(kite_client, watched)

    # Phase 1: load option instruments + coverage from DB
    db = DatabaseClient(settings)
    db.connect()

    placeholders = ",".join("?" for _ in target_underlyings)
    cursor = db.conn.cursor()
    cursor.execute(
        f"""
        SELECT id, instrument_token, underlying, exchange, tradingsymbol, name,
               strike, expiry, instrument_type, lot_size, tick_size, segment, fetch_date
        FROM dbo.OptionInstrument
        WHERE underlying IN ({placeholders})
        """,
        target_underlyings,
    )
    rows = cursor.fetchall()
    cursor.close()

    option_instruments: List[OptionInstrument] = []
    token_to_db_id: Dict[int, int] = {}
    db_id_to_fetch_date: Dict[int, date] = {}
    db_id_to_expiry: Dict[int, date] = {}

    for r in rows:
        db_id = int(r.id)
        token = int(r.instrument_token)
        token_to_db_id[token] = db_id
        fetch_dt = _to_date(r.fetch_date) or today
        expiry_dt = _to_date(r.expiry) or today
        db_id_to_fetch_date[db_id] = fetch_dt
        db_id_to_expiry[db_id] = expiry_dt

        option_instruments.append(OptionInstrument(
            fetch_date=fetch_dt,
            underlying=r.underlying,
            exchange=r.exchange,
            tradingsymbol=r.tradingsymbol,
            instrument_token=token,
            name=r.name,
            strike=float(r.strike),
            expiry=expiry_dt,
            instrument_type=r.instrument_type,
            lot_size=int(r.lot_size) if r.lot_size is not None else 0,
            tick_size=float(r.tick_size) if r.tick_size is not None else None,
            segment=r.segment,
        ))

    print(f"Loaded {len(option_instruments)} option instruments from DB")

    if not option_instruments:
        db.close()
        return {"underlyings": target_underlyings, "option_instruments_loaded": 0, "snapshots_inserted": 0}

    # Refresh tokens from live NFO instrument list — DB tokens go stale when Kite re-lists contracts.
    print("Refreshing NFO instrument tokens from Kite...")
    try:
        nfo_live = kite_client.kite.instruments("NFO")
        live_ts_to_token: Dict[str, int] = {ki["tradingsymbol"]: ki["instrument_token"] for ki in nfo_live}
        refreshed = stale = 0
        for inst in option_instruments:
            live_tok = live_ts_to_token.get(inst.tradingsymbol)
            if live_tok is not None and live_tok != inst.instrument_token:
                old = inst.instrument_token
                db_id = token_to_db_id.pop(old, None)
                if db_id is not None:
                    token_to_db_id[live_tok] = db_id
                inst.instrument_token = live_tok
                refreshed += 1
            elif live_tok is None:
                stale += 1
        print(f"  Token refresh: {refreshed} updated, {stale} not found in live NFO list (will be skipped)")
    except Exception as e:
        print(f"[WARN] Could not refresh NFO tokens: {e}. Proceeding with DB tokens.")

    print("Checking existing OptionSnapshot coverage...")
    all_db_ids = list(db_id_to_fetch_date.keys())
    coverage = _bulk_query_coverage(db, all_db_ids)
    print(f"  {len(coverage)} instruments already have snapshots")

    # Close before Kite API calls to avoid idle TCP drops
    db.close()

    # Phase 2: fetch underlying day candles (one call per stock, full range)
    overall_start = global_start or min(db_id_to_fetch_date.values(), default=today)
    underlying_day_candles: Dict[str, Dict[date, dict]] = {}
    for symbol, token in stock_tokens.items():
        print(f"Fetching {symbol} day candles ({overall_start} to {eff_end})...")
        underlying_day_candles[symbol] = _fetch_underlying_day_candles(
            kite_client, token, overall_start, eff_end
        )
        print(f"  {symbol}: {len(underlying_day_candles[symbol])} trading days")

    # Phase 3: per-instrument fetch + accumulate OptionData rows
    total_inserted = 0
    token_expired = [False]
    pending_rows: List[OptionData] = []

    for idx, inst in enumerate(option_instruments, 1):
        if token_expired[0]:
            print(f"\n[INFO] Stopping - token expired. Processed {idx - 1}/{len(option_instruments)}")
            break

        db_id = token_to_db_id.get(inst.instrument_token)
        if db_id is None:
            continue

        # Per-instrument date range
        inst_start = global_start if global_start else db_id_to_fetch_date[db_id]
        inst_end = min(eff_end, db_id_to_expiry[db_id])
        if inst_start > inst_end:
            continue

        # Skip if fully covered
        db_min, db_max = coverage.get(db_id, (None, None))
        gaps = _compute_gaps(db_min, db_max, inst_start, inst_end)
        if not gaps:
            continue

        underlying = inst.underlying
        if underlying not in underlying_day_candles:
            continue
        und_candles = underlying_day_candles[underlying]

        for gap_start, gap_end in gaps:
            candles = _fetch_option_day_candles(
                kite_client, inst.instrument_token, inst.tradingsymbol,
                gap_start, gap_end, token_expired,
            )
            if token_expired[0]:
                break
            if not candles:
                continue

            for c in candles:
                c_dt = c["date"]
                if hasattr(c_dt, "tzinfo"):
                    c_dt = c_dt.replace(tzinfo=None)
                trade_date = c_dt.date() if isinstance(c_dt, datetime) else c_dt
                if not (gap_start <= trade_date <= gap_end):
                    continue
                und_c = und_candles.get(trade_date)
                if not und_c:
                    continue
                pending_rows.extend(_build_option_data(inst, db_id, c, und_c))

        if idx % 100 == 0:
            print(f"  Processed {idx}/{len(option_instruments)} options, {len(pending_rows)} rows pending...")

        # Flush periodically
        if len(pending_rows) >= INSERT_BATCH_SIZE:
            db.connect()
            db.bulk_insert_option_data(pending_rows)
            db.close()
            total_inserted += len(pending_rows)
            print(f"  Flushed {len(pending_rows)} rows (total: {total_inserted})")
            pending_rows = []

        if idx < len(option_instruments) and not token_expired[0]:
            time.sleep(API_DELAY_SECONDS)

    # Final flush
    if pending_rows:
        db.connect()
        db.bulk_insert_option_data(pending_rows)
        db.close()
        total_inserted += len(pending_rows)
        print(f"  Flushed final {len(pending_rows)} rows (total: {total_inserted})")

    print(f"\nDone. Total OptionSnapshot rows inserted: {total_inserted}")
    return {
        "underlyings": target_underlyings,
        "option_instruments_loaded": len(option_instruments),
        "snapshots_inserted": total_inserted,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Backfill OptionSnapshot (2/day at 09:15 + 15:15) for STOCK instruments"
    )
    parser.add_argument(
        "--start", default=None,
        help="Global start date YYYY-MM-DD (default: per-instrument fetch_date)",
    )
    parser.add_argument(
        "--end", default=None,
        help="Global end date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--underlying", default=None,
        help="Comma-separated underlyings to restrict (e.g. HDFCBANK,RELIANCE)",
    )
    args = parser.parse_args()

    run_backfill_stocks_options(
        global_start=date.fromisoformat(args.start) if args.start else None,
        global_end=date.fromisoformat(args.end) if args.end else None,
        underlyings=[u.strip().upper() for u in args.underlying.split(",")] if args.underlying else None,
    )


if __name__ == "__main__":
    main()
