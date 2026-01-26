import os
import sys
import math
import time as time_module
import pyodbc
from pathlib import Path
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
import pytz
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

# --- Config ---
ENTRY_TIME = time(9, 15)     # bar start
EXIT_TIME  = time(15, 25)    # bar start (last bar before 15:30 close)

UNDERLYINGS = ["NIFTY", "BANKNIFTY"]
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}  # practical default


def ist_dt(trade_date: date, t: time) -> datetime:
    """Return timezone-aware IST datetime for a trade date + time."""
    return IST.localize(datetime.combine(trade_date, t))


def round_to_step(x: float, step: int) -> int:
    return int(round(x / step) * step)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def safe_first(lst):
    return lst[0] if lst else None


def connect_kite() -> KiteConnect:
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise RuntimeError("KITE_API_KEY is missing in environment/.env")
    
    # Read access token from file (same pattern as other scripts)
    token_path = Path(os.getenv("KITE_ACCESS_TOKEN_PATH", ".secrets/kite_access_token.txt"))
    if not token_path.exists():
        raise RuntimeError(
            f"Access token file not found: {token_path}\n"
            "Run: python scripts/get_kite_access_token.py"
        )
    
    access_token = token_path.read_text(encoding="utf-8").strip()
    if not access_token:
        raise RuntimeError(
            f"Access token file is empty: {token_path}\n"
            "Run: python scripts/get_kite_access_token.py"
        )
    
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def connect_sql() -> pyodbc.Connection:
    conn_str = os.getenv("AZURE_SQL_CONN_STR")
    if not conn_str:
        raise RuntimeError("AZURE_SQL_CONN_STR is missing in environment/.env")
    return pyodbc.connect(conn_str)


def fetch_5m_bar(kite: KiteConnect, token: int, bar_start_ist: datetime, debug: bool = False) -> Optional[Dict[str, Any]]:
    """
    Fetch the single 5-min candle starting at bar_start_ist.
    Kite historical API expects naive datetime in many setups; we'll pass naive in IST local time.
    
    Note: Kite API may need a wider time window to find the exact candle. We request a small window
    around the target time and filter for the exact match.
    """
    # Add small delay to respect rate limits
    time_module.sleep(0.1)
    
    # Convert to naive datetime (Kite API expects naive datetime)
    start_naive = bar_start_ist.replace(tzinfo=None)
    
    # Request a wider window (e.g., 15 minutes) to ensure we capture the candle
    # Kite API might not return data if the window is too narrow
    window_start = start_naive - timedelta(minutes=5)
    window_end = start_naive + timedelta(minutes=10)

    try:
        # Try with oi=True first (for derivatives)
        try:
            bars = kite.historical_data(token, window_start, window_end, "5minute", oi=True, continuous=False)
        except TypeError:
            # Fallback if oi parameter not supported
            bars = kite.historical_data(token, window_start, window_end, "5minute", continuous=False)
        
        if not bars:
            if debug:
                print(f"  [DEBUG] No bars returned for token {token} at {start_naive}")
            return None

        # Find the bar that matches our target time (within 2 minutes tolerance)
        target_time = start_naive
        for b in bars:
            bar_time = b.get("date")
            if bar_time:
                # Handle both datetime objects and strings
                if isinstance(bar_time, str):
                    try:
                        bar_time = datetime.fromisoformat(bar_time.replace('Z', '+00:00'))
                    except:
                        continue
                if isinstance(bar_time, datetime):
                    bar_time = bar_time.replace(tzinfo=None)
                    # Check if this bar matches our target time (within 2 minutes)
                    time_diff = abs((bar_time - target_time).total_seconds())
                    if time_diff <= 120:  # 2 minutes tolerance
                        # Normalize keys
                        return {
                            "open": b.get("open"),
                            "high": b.get("high"),
                            "low": b.get("low"),
                            "close": b.get("close"),
                            "volume": b.get("volume"),
                            "oi": b.get("oi")  # may be absent
                        }
        
        if debug:
            print(f"  [DEBUG] No matching bar found for token {token} at {target_time}. Available bars: {[b.get('date') for b in bars[:3]]}")
        return None
        
    except Exception as e:
        if debug:
            print(f"  [DEBUG] Error fetching bar for token {token} at {start_naive}: {e}")
        return None


def build_instrument_maps(kite: KiteConnect):
    """
    Build lookup maps for:
    - spot index tokens (NSE)
    - India VIX token (NSE)
    - derivatives instruments (NFO) for FUT/OPT selection
    """
    nse = kite.instruments("NSE")
    nfo = kite.instruments("NFO")

    # Spot tokens for index symbols vary; use name/ tradingsymbol matching best-effort
    # Common: "NIFTY 50", "NIFTY BANK", "INDIA VIX"
    spot_map = {}
    vix_token = None

    for ins in nse:
        ts = ins.get("tradingsymbol", "")
        name = ins.get("name", "")
        if ts == "NIFTY 50" or name == "NIFTY 50":
            spot_map["NIFTY"] = ins["instrument_token"]
        if ts == "NIFTY BANK" or name == "NIFTY BANK":
            spot_map["BANKNIFTY"] = ins["instrument_token"]
        if ts == "INDIA VIX" or name == "INDIA VIX":
            vix_token = ins["instrument_token"]

    # For derivatives, filter once for speed
    fut_list = [x for x in nfo if x.get("instrument_type") == "FUT"]
    opt_list = [x for x in nfo if x.get("instrument_type") in ("CE", "PE")]

    return spot_map, vix_token, fut_list, opt_list


def pick_nearest_future(fut_list: List[Dict[str, Any]], underlying: str, trade_date: date) -> Optional[Dict[str, Any]]:
    cands = []
    for ins in fut_list:
        if ins.get("name") != underlying:
            continue
        exp = ins.get("expiry")
        if exp and exp >= trade_date:
            cands.append(ins)
    cands.sort(key=lambda x: x["expiry"])
    return safe_first(cands)


def pick_nearest_option(opt_list: List[Dict[str, Any]], underlying: str, trade_date: date, strike: int, opt_type: str) -> Optional[Dict[str, Any]]:
    cands = []
    for ins in opt_list:
        if ins.get("name") != underlying:
            continue
        if ins.get("instrument_type") != opt_type:
            continue
        exp = ins.get("expiry")
        if not exp or exp < trade_date:
            continue
        if int(ins.get("strike", 0)) != int(strike):
            continue
        cands.append(ins)
    cands.sort(key=lambda x: x["expiry"])
    return safe_first(cands)


def upsert_market_activity(conn: pyodbc.Connection, row: Dict[str, Any]):
    """
    Upsert by (underlying, trade_date, snapshot_type)
    """
    sql = """
    MERGE dbo.MarketActivitySnapshot AS tgt
    USING (SELECT ? AS underlying, ? AS trade_date, ? AS snapshot_type) AS src
       ON tgt.underlying = src.underlying
      AND tgt.trade_date = src.trade_date
      AND tgt.snapshot_type = src.snapshot_type
    WHEN MATCHED THEN UPDATE SET
        snapshot_time = ?,
        spot_open = ?, spot_high = ?, spot_low = ?, spot_close = ?,
        fut_expiry = ?, fut_token = ?, fut_symbol = ?,
        fut_open = ?, fut_high = ?, fut_low = ?, fut_close = ?, fut_volume = ?, fut_oi = ?,
        opt_expiry = ?, atm_strike = ?,
        ce_token = ?, pe_token = ?,
        ce_close = ?, pe_close = ?,
        ce_volume = ?, pe_volume = ?, opt_atm_volume_sum = ?,
        ce_oi = ?, pe_oi = ?, opt_atm_oi_sum = ?, pcr_oi = ?,
        vix_close = ?
    WHEN NOT MATCHED THEN INSERT (
        underlying, trade_date, snapshot_type, snapshot_time,
        spot_open, spot_high, spot_low, spot_close,
        fut_expiry, fut_token, fut_symbol,
        fut_open, fut_high, fut_low, fut_close, fut_volume, fut_oi,
        opt_expiry, atm_strike,
        ce_token, pe_token,
        ce_close, pe_close,
        ce_volume, pe_volume, opt_atm_volume_sum,
        ce_oi, pe_oi, opt_atm_oi_sum, pcr_oi,
        vix_close
    ) VALUES (
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?, ?, ?,
        ?, ?,
        ?, ?,
        ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?,
        ?
    );
    """

    params_update = [
        row["underlying"], row["trade_date"], row["snapshot_type"],
        row["snapshot_time"],
        row.get("spot_open"), row.get("spot_high"), row.get("spot_low"), row.get("spot_close"),
        row.get("fut_expiry"), row.get("fut_token"), row.get("fut_symbol"),
        row.get("fut_open"), row.get("fut_high"), row.get("fut_low"), row.get("fut_close"),
        row.get("fut_volume"), row.get("fut_oi"),
        row.get("opt_expiry"), row.get("atm_strike"),
        row.get("ce_token"), row.get("pe_token"),
        row.get("ce_close"), row.get("pe_close"),
        row.get("ce_volume"), row.get("pe_volume"), row.get("opt_atm_volume_sum"),
        row.get("ce_oi"), row.get("pe_oi"), row.get("opt_atm_oi_sum"), row.get("pcr_oi"),
        row.get("vix_close"),
    ]

    # INSERT values repeat the same fields (after update set)
    params_insert = [
        row["underlying"], row["trade_date"], row["snapshot_type"], row["snapshot_time"],
        row.get("spot_open"), row.get("spot_high"), row.get("spot_low"), row.get("spot_close"),
        row.get("fut_expiry"), row.get("fut_token"), row.get("fut_symbol"),
        row.get("fut_open"), row.get("fut_high"), row.get("fut_low"), row.get("fut_close"),
        row.get("fut_volume"), row.get("fut_oi"),
        row.get("opt_expiry"), row.get("atm_strike"),
        row.get("ce_token"), row.get("pe_token"),
        row.get("ce_close"), row.get("pe_close"),
        row.get("ce_volume"), row.get("pe_volume"), row.get("opt_atm_volume_sum"),
        row.get("ce_oi"), row.get("pe_oi"), row.get("opt_atm_oi_sum"), row.get("pcr_oi"),
        row.get("vix_close"),
    ]

    with conn.cursor() as cur:
        cur.execute(sql, params_update + params_insert)
    conn.commit()


def cook_for_date(kite: KiteConnect, conn: pyodbc.Connection,
                  spot_map: Dict[str, int], vix_token: Optional[int],
                  fut_list: List[Dict[str, Any]], opt_list: List[Dict[str, Any]],
                  trade_date: date):
    # Optional: skip weekends quickly (holidays will just return no bars and get skipped)
    if trade_date.weekday() >= 5:
        return
    
    # Show progress for each date (can be verbose, comment out if too much)
    # print(f"Processing {trade_date}...")

    for underlying in UNDERLYINGS:
        spot_token = spot_map.get(underlying)
        if not spot_token:
            continue

        # Pick nearest future for the day
        fut = pick_nearest_future(fut_list, underlying, trade_date)

        for snapshot_type, snap_t in [("ENTRY", ENTRY_TIME), ("EXIT", EXIT_TIME)]:
            snap_dt = ist_dt(trade_date, snap_t)

            # Spot bar
            spot_bar = fetch_5m_bar(kite, spot_token, snap_dt)
            if not spot_bar:
                # likely holiday / no session
                continue

            spot_close = float(spot_bar["close"]) if spot_bar["close"] is not None else None

            # Futures bar (volume/OI proxy)
            fut_bar = None
            fut_token = None
            if fut:
                fut_token = fut.get("instrument_token")
                if fut_token:
                    fut_bar = fetch_5m_bar(kite, fut_token, snap_dt, debug=False)
                    if not fut_bar:
                        # Only warn occasionally to avoid spam
                        if trade_date.day <= 3 or (trade_date.day % 10 == 0):
                            print(f"  [WARN] Future bar is None for {underlying} on {trade_date} at {snapshot_type}")
                            print(f"         Future: {fut.get('tradingsymbol')}, expiry: {fut.get('expiry')}, token: {fut_token}")
                else:
                    if trade_date.day == 1:
                        print(f"  [WARN] Future found but no instrument_token for {underlying} on {trade_date}")
            else:
                if trade_date.day == 1:
                    print(f"  [WARN] No future found for {underlying} on {trade_date}")

            # Options ATM selection
            step = STRIKE_STEP.get(underlying, 50)
            atm_strike = round_to_step(spot_close, step) if spot_close else None

            ce = pe = None
            ce_bar = pe_bar = None
            if atm_strike is not None:
                ce = pick_nearest_option(opt_list, underlying, trade_date, atm_strike, "CE")
                pe = pick_nearest_option(opt_list, underlying, trade_date, atm_strike, "PE")

                if ce:
                    ce_bar = fetch_5m_bar(kite, ce["instrument_token"], snap_dt)
                if pe:
                    pe_bar = fetch_5m_bar(kite, pe["instrument_token"], snap_dt)

            # VIX bar
            vix_bar = fetch_5m_bar(kite, vix_token, snap_dt) if vix_token else None

            # Aggregate option proxies
            ce_vol = ce_bar.get("volume") if ce_bar else None
            pe_vol = pe_bar.get("volume") if pe_bar else None
            vol_sum = (ce_vol or 0) + (pe_vol or 0) if (ce_vol is not None or pe_vol is not None) else None

            ce_oi = ce_bar.get("oi") if ce_bar else None
            pe_oi = pe_bar.get("oi") if pe_bar else None
            oi_sum = (ce_oi or 0) + (pe_oi or 0) if (ce_oi is not None or pe_oi is not None) else None
            pcr_oi = None
            if pe_oi is not None and ce_oi is not None and ce_oi != 0:
                pcr_oi = float(pe_oi) / float(ce_oi)

            row = {
                "underlying": underlying,
                "trade_date": trade_date,
                "snapshot_type": snapshot_type,
                "snapshot_time": snap_dt.replace(tzinfo=None),  # store naive IST or keep consistent with your DB convention

                "spot_open": spot_bar.get("open"),
                "spot_high": spot_bar.get("high"),
                "spot_low": spot_bar.get("low"),
                "spot_close": spot_bar.get("close"),

                "fut_expiry": fut.get("expiry") if fut else None,
                "fut_token": fut_token,
                "fut_symbol": fut.get("tradingsymbol") if fut else None,
                "fut_open": fut_bar.get("open") if fut_bar else None,
                "fut_high": fut_bar.get("high") if fut_bar else None,
                "fut_low": fut_bar.get("low") if fut_bar else None,
                "fut_close": fut_bar.get("close") if fut_bar else None,
                "fut_volume": fut_bar.get("volume") if fut_bar else None,
                "fut_oi": fut_bar.get("oi") if fut_bar else None,

                "opt_expiry": ce.get("expiry") if ce else (pe.get("expiry") if pe else None),
                "atm_strike": atm_strike,

                "ce_token": ce.get("instrument_token") if ce else None,
                "pe_token": pe.get("instrument_token") if pe else None,

                "ce_close": ce_bar.get("close") if ce_bar else None,
                "pe_close": pe_bar.get("close") if pe_bar else None,

                "ce_volume": ce_vol,
                "pe_volume": pe_vol,
                "opt_atm_volume_sum": vol_sum,

                "ce_oi": ce_oi,
                "pe_oi": pe_oi,
                "opt_atm_oi_sum": oi_sum,
                "pcr_oi": pcr_oi,

                "vix_close": vix_bar.get("close") if vix_bar else None,
            }

            upsert_market_activity(conn, row)


def main():
    # Example range (change as needed)
    start_date = date(2025, 7, 2)
    end_date   = date(2025, 7, 3)

    kite = connect_kite()
    conn = connect_sql()

    spot_map, vix_token, fut_list, opt_list = build_instrument_maps(kite)

    for d in daterange(start_date, end_date):
        cook_for_date(kite, conn, spot_map, vix_token, fut_list, opt_list, d)

    conn.close()


if __name__ == "__main__":
    main()
