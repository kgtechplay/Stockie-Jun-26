# scripts/daily_intraday_stock_option_snapshot.py

import sys
from pathlib import Path
from datetime import date, datetime, time as dtime
from typing import Dict, List, Set

from dotenv import load_dotenv

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.db_client import AzureSqlClient
from src.kite_client import KiteClient
from src.stock_fetcher import extract_stock_instruments
from src.option_fetcher import (
    filter_options_for_underlyings,
    _normalize_underlying,
    _years_to_expiry,           # CHANGED: use same helpers as backfill
    _implied_volatility,
    _bs_greeks,
)
from src.models import StockInstrument, OptionInstrument, OptionData

RISK_FREE_RATE = 0.07  # annualized for IV/Greeks
load_dotenv()


# ---------- helpers ----------

def build_underlying_mapping(stocks: List[StockInstrument]) -> Dict[str, int]:
    """
    Map canonical underlying -> instrument_token for stocks/indices.

    Uses stock.name if present, else tradingsymbol, passed through _normalize_underlying().
    Only keeps the first instrument_token per canonical underlying.
    """
    mapping: Dict[str, int] = {}
    for s in stocks:
        label = s.name or s.tradingsymbol
        norm = _normalize_underlying(label)
        if not norm:
            continue
        if norm not in mapping:
            mapping[norm] = s.instrument_token
    return mapping


# ---------- main ----------

def main() -> None:
    settings = get_settings()

    # Current timestamp (local) for this snapshot
    now = datetime.now()
    today = now.date()
    print(f"Running intraday refresh at {now.isoformat()}")

    # Decide which snapshot(s) this run should create.
    # If you schedule two jobs:
    #  - ~09:20 -> will process 09:15 bar
    #  - ~15:20 -> will process 15:15 bar
    MORNING_SNAP = dtime(9, 15)
    CLOSE_SNAP = dtime(15, 15)

    if now.time() < dtime(12, 0):
        snap_times = [MORNING_SNAP]
        print("This looks like morning run; will create 09:15 snapshot.")
    else:
        snap_times = [CLOSE_SNAP]
        print("This looks like close run; will create 15:15 snapshot.")

    # Init Kite + DB
    kite_client = KiteClient(settings)
    kite_client.authenticate()

    db = AzureSqlClient(settings)
    db.connect()

    # ---------------------------------------------------------
    # 1) Incremental update of StockDB via upsert_stock_instruments
    # ---------------------------------------------------------
    print("Fetching NSE/BSE equity + index instruments from Kite...")
    instruments_dump = kite_client.fetch_instruments_equity_indices()
    print(f"Got {len(instruments_dump)} raw instruments")

    stocks = extract_stock_instruments(instruments_dump)
    print(f"Filtered down to {len(stocks)} StockInstrument rows (stocks + indices)")

    print("Upserting stocks/indices into StockDB (append-only)...")
    db.upsert_stock_instruments(stocks)
    print("StockDB upsert complete.\n")

    # Build canonical underlyings from today's stock/indices instruments
    underlying_to_token = build_underlying_mapping(stocks)
    underlyings = sorted(underlying_to_token.keys())
    print(f"Canonical underlyings from stocks/indices: {len(underlyings)}")

    # For now, we care mainly about NIFTY/BANKNIFTY; you can extend this list.
    interesting_underlyings = [u for u in ("NIFTY", "BANKNIFTY") if u in underlying_to_token]
    print(f"Underlyings with index tokens available: {interesting_underlyings}")

    # ---------------------------------------------------------
    # 2) Incremental update of OptionInstrument (append-only CE/PE)
    # ---------------------------------------------------------
    print("Fetching NFO instruments dump from Kite...")
    nfo_dump = kite_client.fetch_instruments_nfo()
    print(f"Got {len(nfo_dump)} raw NFO instruments")

    option_instruments_from_kite: List[OptionInstrument] = filter_options_for_underlyings(
        instruments_dump=nfo_dump,
        underlyings=underlyings,
    )
    print(f"Option instruments mapped to these underlyings: {len(option_instruments_from_kite)}")

    print("Upserting options into OptionInstrument (append-only)...")
    db.upsert_option_instruments(option_instruments_from_kite)
    print("OptionInstrument upsert complete.\n")

    # ---------------------------------------------------------
    # 3) Build 5-minute historical snapshots for TODAY at 09:15 / 15:15
    # ---------------------------------------------------------
    print("Loading NIFTY/BANKNIFTY options from DB...")
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT
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

    option_instruments_db: List[OptionInstrument] = []
    tokens: Set[int] = set()

    for r in rows:
        token = int(r.instrument_token)
        tokens.add(token)
        option_instruments_db.append(
            OptionInstrument(
                fetch_date=today,
                underlying=r.underlying,
                exchange=r.exchange,
                tradingsymbol=r.tradingsymbol,
                instrument_token=token,
                name=r.name,
                strike=float(r.strike),
                expiry=r.expiry,
                instrument_type=r.instrument_type,
                lot_size=int(r.lot_size) if r.lot_size is not None else 0,
                tick_size=float(r.tick_size) if r.tick_size is not None else None,
                segment=r.segment,
            )
        )

    print(f"Loaded {len(option_instruments_db)} options from OptionInstrument DB")

    # Map instrument_token -> OptionInstrument.id (DB PK)
    token_to_id = db.get_option_instrument_ids_by_token(tokens)
    print(f"Resolved {len(token_to_id)} instrument_tokens to DB ids")

    # 3a) Fetch 5-min index candles for today and keep only 09:15 / 15:15
    from_dt = datetime.combine(today, dtime(9, 15))
    to_dt = now  # up to "now"; 15:15 bar is available by ~15:20

    index_snapshots: Dict[str, Dict[datetime, float]] = {}

    for underlying in interesting_underlyings:
        idx_token = underlying_to_token[underlying]
        print(f"Fetching 5-min candles for index {underlying} ({idx_token})...")
        try:
            candles = kite_client.kite.historical_data(
                idx_token,
                from_dt,
                to_dt,
                interval="5minute",
                continuous=False,
                oi=False,
            )
        except Exception as e:
            print(f"[WARN] Failed fetching index historical for {underlying}: {e}")
            continue

        snap_map: Dict[datetime, float] = {}
        for c in candles:
            c_dt = c["date"].replace(tzinfo=None)
            if c_dt.date() != today:
                continue
            if c_dt.time() not in snap_times:
                continue
            snap_map[c_dt] = float(c["close"])  # use close of the 5-min bar as underlying price

        index_snapshots[underlying] = snap_map
        print(f"Index {underlying}: got {len(snap_map)} snap candles for today")

    # 3b) Fetch 5-min option candles and build OptionData for matching snapshots
    snapshots: List[OptionData] = []

    for idx, inst in enumerate(option_instruments_db, 1):
        if inst.underlying not in index_snapshots:
            continue

        try:
            opt_candles = kite_client.kite.historical_data(
                inst.instrument_token,
                from_dt,
                to_dt,
                interval="5minute",
                continuous=False,
                oi=True,
            )
        except Exception as e:
            print(f"[WARN] Failed fetching historical for option {inst.tradingsymbol}: {e}")
            continue

        idx_snap_map = index_snapshots[inst.underlying]

        for c in opt_candles:
            c_dt = c["date"].replace(tzinfo=None)
            if c_dt.date() != today:
                continue
            if c_dt.time() not in snap_times:
                continue

            S = idx_snap_map.get(c_dt)
            if S is None:
                # No matching index candle; skip defensively
                continue

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

            db_id = token_to_id.get(inst.instrument_token)
            if db_id is None:
                continue  # safety

            od = OptionData(
                option_instrument_id=db_id,
                snapshot_time=c_dt,  # NOTE: 5-min candle timestamp (09:15 / 15:15)
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
            print(f"Processed {idx} / {len(option_instruments_db)} options...")

    print(f"Total OptionData rows for this run: {len(snapshots)}")

    if snapshots:
        # IMPORTANT: make sure your DB has a unique constraint on
        # (option_instrument_id, snapshot_time) and that bulk_insert_option_data
        # does an upsert/merge or you only run this script once per window.
        db.bulk_insert_option_data(snapshots)
        print("Inserted OptionData snapshot rows for today's 5-min candles.")
    else:
        print("No OptionData rows generated for this run.")

    db.close()
    print("Daily intraday snapshot run complete.")


if __name__ == "__main__":
    main()
