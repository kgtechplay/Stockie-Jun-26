# scripts/backfill_nifty_banknifty_options_30d.py

import sys
import time
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List

from dotenv import load_dotenv

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.db_client import AzureSqlClient
from src.kite_client import KiteClient
from src.option_fetcher import (
    _years_to_expiry,
    _implied_volatility,
    _bs_greeks,
)
from src.models import OptionInstrument, OptionData

RISK_FREE_RATE = 0.07
# Rate limiting: Kite Connect allows ~3 requests/second, so use 0.4s delay to be safe
API_DELAY_SECONDS = 0.4
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # Base delay for exponential backoff (seconds)
load_dotenv()


def get_index_tokens(kite_client: KiteClient) -> Dict[str, int]:
    """
    Find instrument_token for NIFTY 50 and NIFTY BANK on NSE.

    Zerodha usually marks these as normal EQ instruments with special
    tradingsymbols (e.g. 'NIFTY 50', 'NIFTY BANK' or 'NIFTY', 'BANKNIFTY'),
    not with instrument_type='INDEX'. So we match by tradingsymbol.
    """
    nse_instr = kite_client.kite.instruments("NSE")
    mapping: Dict[str, int] = {}

    for inst in nse_instr:
        ts = inst.get("tradingsymbol")
        if ts in ("NIFTY 50", "NIFTY"):
            mapping["NIFTY"] = inst["instrument_token"]
        elif ts in ("NIFTY BANK", "BANKNIFTY"):
            mapping["BANKNIFTY"] = inst["instrument_token"]

    if "NIFTY" not in mapping or "BANKNIFTY" not in mapping:
        print(f"[WARN] Index token mapping incomplete: {mapping}")

        # OPTIONAL: you can add hard-coded fallbacks if you like:
        # if "NIFTY" not in mapping:
        #     mapping["NIFTY"] = 256265
        # if "BANKNIFTY" not in mapping:
        #     mapping["BANKNIFTY"] = 260105

    return mapping


def fetch_index_5min_snapshots(
    kite_client: KiteClient,
    index_tokens: Dict[str, int],
    start_date: date,
    end_date: date,
    snap_times: List[dtime],
) -> Dict[str, Dict[datetime, dict]]:
    """
    For NIFTY & BANKNIFTY, fetch 5-min candles between start_date and end_date,
    but only keep those whose time is in snap_times (e.g., 09:15 and 15:15).

    Returns: index_name -> {snapshot_datetime -> candle_dict}
    """
    from_dt = datetime.combine(start_date, min(snap_times))
    to_dt = datetime.combine(end_date + timedelta(days=1), dtime(0, 0))

    result: Dict[str, Dict[datetime, dict]] = {}
    for idx_name, token in index_tokens.items():
        try:
            candles = kite_client.kite.historical_data(
                token,
                from_dt,
                to_dt,
                interval="5minute",
                continuous=False,
                oi=False,
            )
        except Exception as e:
            print(f"[WARN] Failed fetching index historical for {idx_name}: {e}")
            continue

        snap_map: Dict[datetime, dict] = {}
        for c in candles:
            c_dt = c["date"].replace(tzinfo=None)
            if c_dt.date() < start_date or c_dt.date() > end_date:
                continue
            if c_dt.time() not in snap_times:
                continue
            snap_map[c_dt] = c

        result[idx_name] = snap_map

    return result


def main() -> None:
    settings = get_settings()

    db = AzureSqlClient(settings)
    db.connect()

    kite_client = KiteClient(settings)
    kite_client.authenticate()

    today = date.today()  # Backfill run date
    start_date = date(2025, 12, 26)
    end_date = date(2025, 12, 30)
    print(f"Backfilling NIFTY/BANKNIFTY options for {start_date} to {end_date}")

    # 1) Read option instruments for NIFTY/BANKNIFTY from DB
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT
            id,
            instrument_token,
            underlying,
            exchange,
            tradingsymbol,
            name,
            strike,
            expiry,
            instrument_type,
            lot_size,
            tick_size,
            segment
        FROM dbo.OptionInstrument
        WHERE underlying IN ('NIFTY', 'BANKNIFTY')
        """
    )
    rows = cursor.fetchall()
    cursor.close()

    option_instruments: List[OptionInstrument] = []
    token_to_db_id: Dict[int, int] = {}

    for r in rows:
        db_id = int(r.id)
        token = int(r.instrument_token)
        token_to_db_id[token] = db_id

        raw_expiry = r.expiry

        if isinstance(raw_expiry, datetime):
            expiry_date = raw_expiry.date()
        elif isinstance(raw_expiry, date):
            expiry_date = raw_expiry
        else:
            # assume string like '2025-12-25' or '2025-12-25 00:00:00'
            expiry_date = datetime.strptime(str(raw_expiry)[:10], "%Y-%m-%d").date()

        option_instruments.append(
            OptionInstrument(
                fetch_date=today,  # backfill run date, not the trade date
                underlying=r.underlying,
                exchange=r.exchange,
                tradingsymbol=r.tradingsymbol,
                instrument_token=token,
                name=r.name,
                strike=float(r.strike),
                expiry=expiry_date,
                instrument_type=r.instrument_type,
                lot_size=int(r.lot_size) if r.lot_size is not None else 0,
                tick_size=float(r.tick_size) if r.tick_size is not None else None,
                segment=r.segment,
            )
        )

    print(f"Loaded {len(option_instruments)} unique NIFTY/BANKNIFTY options from DB")

    if not option_instruments:
        print("No NIFTY/BANKNIFTY options found in OptionInstrument. Exiting.")
        db.close()
        return

    # 2) Fetch index 5-min snapshots (09:15 & 15:15) for NIFTY & BANKNIFTY
    snap_times = [dtime(9, 15), dtime(15, 15)]

    index_tokens = get_index_tokens(kite_client)
    index_5min = fetch_index_5min_snapshots(
        kite_client=kite_client,
        index_tokens=index_tokens,
        start_date=start_date,
        end_date=end_date,
        snap_times=snap_times,
    )
    print("Fetched index 5-min snap data for:", ", ".join(index_5min.keys()))

    # 3) Backfill option snapshots using option 5-min candles
    from_dt = datetime.combine(start_date, min(snap_times))
    to_dt = datetime.combine(end_date + timedelta(days=1), dtime(0, 0))

    snapshots: List[OptionData] = []
    token_expired = False

    for idx, inst in enumerate(option_instruments, 1):
        underlying = inst.underlying
        if underlying not in index_5min:
            continue

        idx_snap_map = index_5min[underlying]

        # Retry logic with exponential backoff for rate limiting
        opt_candles = None
        token_reload_attempted = False
        for retry in range(MAX_RETRIES):
            try:
                opt_candles = kite_client.kite.historical_data(
                    inst.instrument_token,
                    from_dt,
                    to_dt,
                    interval="5minute",
                    continuous=False,
                    oi=True,
                )
                break  # Success, exit retry loop
            except Exception as e:
                error_msg = str(e).lower()
                is_rate_limit = "too many requests" in error_msg or "rate limit" in error_msg
                is_invalid_token = "invalid token" in error_msg or ("token" in error_msg and ("expired" in error_msg or "invalid" in error_msg))
                
                if is_invalid_token:
                    # Try to re-authenticate once if we haven't already
                    if not token_reload_attempted:
                        print(f"\n[WARN] Invalid/expired access token detected for {inst.tradingsymbol}")
                        print(f"       Attempting to reload token from file/database...")
                        token_reload_attempted = True
                        # Small delay to ensure file write is complete if token was just refreshed
                        time.sleep(0.5)
                        # Try to re-authenticate with updated token
                        if kite_client.re_authenticate():
                            print(f"       ✅ Token reloaded successfully! Retrying API call...\n")
                            # Retry the API call with new token (continue loop)
                            continue
                        else:
                            print(f"       ❌ Failed to reload token. Please refresh your token by running:")
                            print(f"       python scripts/get_kite_access_token.py")
                            print(f"       Then re-run this script.\n")
                            token_expired = True
                            opt_candles = None
                            break
                    else:
                        # Already tried reloading, token still invalid
                        if not token_expired:  # Only print once
                            print(f"\n[ERROR] Token reload failed or token still invalid after reload.")
                            print(f"       Please refresh your token by running: python scripts/get_kite_access_token.py")
                            print(f"       Then re-run this script.\n")
                            token_expired = True
                        opt_candles = None
                        break
                elif is_rate_limit and retry < MAX_RETRIES - 1:
                    # Exponential backoff: wait 2s, 4s, 8s...
                    wait_time = RETRY_DELAY_BASE * (2 ** retry)
                    print(f"[WARN] Rate limited for {inst.tradingsymbol}, retrying in {wait_time}s... (attempt {retry + 1}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"[WARN] Failed fetching historical for option {inst.tradingsymbol}: {e}")
                    opt_candles = None
                    break
        
        # Exit early if token is expired (and reload failed)
        if token_expired:
            print(f"\n[INFO] Stopping backfill due to token expiration. Processed {idx-1} / {len(option_instruments)} options.")
            print(f"       Please refresh your token and re-run the script to continue from where it stopped.")
            break
        
        if opt_candles is None:
            continue

        for c in opt_candles:
            c_dt = c["date"].replace(tzinfo=None)
            trade_dt = c_dt.date()
            if trade_dt < start_date or trade_dt > end_date:
                continue
            if c_dt.time() not in snap_times:
                continue

            idx_candle = idx_snap_map.get(c_dt)
            if not idx_candle:
                continue

            S = float(idx_candle["close"])
            price = float(c["close"])
            opt_volume = c.get("volume")
            opt_oi = c.get("oi")

            T = _years_to_expiry(inst.expiry, c_dt)

            iv = delta = gamma = theta = vega = None
            if S > 0 and price > 0 and T > 0:
                opt_type = "C" if inst.instrument_type == "CE" or inst.tradingsymbol.endswith("CE") else "P"
                iv_val = _implied_volatility(
                    price=price,
                    S=S,
                    K=float(inst.strike),
                    T=T,
                    r=RISK_FREE_RATE,
                    q=0.0,
                    opt_type=opt_type,
                )
                if iv_val is not None:
                    greeks = _bs_greeks(
                        S=S,
                        K=float(inst.strike),
                        T=T,
                        r=RISK_FREE_RATE,
                        q=0.0,
                        sigma=iv_val,
                        opt_type=opt_type,
                    )
                    iv = iv_val
                    delta = greeks["delta"]
                    gamma = greeks["gamma"]
                    theta = greeks["theta"]
                    vega = greeks["vega"]

            db_id = token_to_db_id.get(inst.instrument_token)
            if db_id is None:
                continue  # safety

            od = OptionData(
                option_instrument_id=db_id,  # DB PK from OptionInstrument
                snapshot_time=c_dt,          # 5-min candle time (09:15 / 15:15)
                underlying_price=S,
                last_price=price,
                bid_price=None,
                bid_qty=None,
                ask_price=None,
                ask_qty=None,
                volume=int(opt_volume) if opt_volume is not None else None,
                open_interest=int(opt_oi) if opt_oi is not None else None,
                implied_volatility=iv,
                delta=delta,
                gamma=gamma,
                theta=theta,
                vega=vega,
            )
            snapshots.append(od)

        if idx % 100 == 0:
            print(f"  Processed {idx} / {len(option_instruments)} options...")
        
        # Add delay between API calls to respect rate limits (after processing each option)
        if idx < len(option_instruments):  # Don't delay after the last one
            time.sleep(API_DELAY_SECONDS)

    print(f"\nTotal backfill OptionData rows: {len(snapshots)}")

    if snapshots:
        db.bulk_insert_option_data(snapshots)
        print("Inserted backfill OptionData rows.")
    else:
        print("No OptionData rows to insert for backfill.")

    db.close()
    print("Backfill run complete.")


if __name__ == "__main__":
    main()
