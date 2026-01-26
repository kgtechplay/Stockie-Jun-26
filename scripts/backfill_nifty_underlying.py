# scripts/backfill_nifty_banknifty_underlying_30d.py

import sys
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Tuple

from dotenv import load_dotenv

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.db_client import AzureSqlClient
from src.kite_client import KiteClient

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



def upsert_underlying_snapshots(
    db: AzureSqlClient,
    rows: List[
        Tuple[
            str,
            date,
            datetime,
            float | None,
            float | None,
            float | None,
            float | None,
            int | None,
        ]
    ],
) -> None:
    """
    Upsert rows into dbo.UnderlyingSnapshot.

    Each row: (underlying, trade_date, loaded_at, open_price, high_price, low_price, close_price, volume)
    """
    if not rows:
        print("No underlying snapshot rows to upsert.")
        return

    cursor = db.conn.cursor()
    cursor.fast_executemany = True

    # Prepare data for bulk insert
    insert_rows = []
    for (
        underlying,
        trade_date,
        loaded_at,
        open_p,
        high_p,
        low_p,
        close_p,
        volume,
    ) in rows:
        insert_rows.append((
            underlying,
            trade_date,
            loaded_at,
            open_p,
            high_p,
            low_p,
            close_p,
            volume,
        ))

    # Check which rows already exist (batch query)
    existing_keys = set()
    if insert_rows:
        # Get unique (underlying, trade_date) pairs
        unique_keys = list({(row[0], row[1]) for row in insert_rows})
        
        # Query existing rows in batches (SQL Server limit is 2100 parameters)
        # Use OR conditions for each pair
        batch_size = 500  # Conservative limit (2 params per pair)
        for i in range(0, len(unique_keys), batch_size):
            batch = unique_keys[i:i + batch_size]
            conditions = []
            params = []
            for underlying, trade_date in batch:
                conditions.append("(underlying = ? AND trade_date = ?)")
                params.extend([underlying, trade_date])
            
            cursor.execute(
                f"""
                SELECT underlying, trade_date
                FROM dbo.UnderlyingSnapshot
                WHERE {' OR '.join(conditions)}
                """,
                params,
            )
            for row in cursor.fetchall():
                existing_keys.add((row[0], row[1]))

    # Separate inserts and updates
    to_insert = []
    to_update = []
    
    for row in insert_rows:
        underlying, trade_date = row[0], row[1]
        if (underlying, trade_date) in existing_keys:
            to_update.append(row)
        else:
            to_insert.append(row)

    # Perform updates
    if to_update:
        cursor.executemany(
            """
            UPDATE dbo.UnderlyingSnapshot
            SET loaded_at = ?, open_price = ?, high_price = ?, low_price = ?,
                close_price = ?, volume = ?
            WHERE underlying = ? AND trade_date = ?
            """,
            [(row[2], row[3], row[4], row[5], row[6], row[7], row[0], row[1]) for row in to_update],
        )
        print(f"Updated {len(to_update)} existing rows")

    # Perform inserts (handle unique constraint violations)
    if to_insert:
        inserted_count = 0
        skipped_count = 0
        
        # Insert one by one to catch and skip duplicates
        for row in to_insert:
            try:
                cursor.execute(
                    """
                    INSERT INTO dbo.UnderlyingSnapshot (
                        underlying,
                        trade_date,
                        loaded_at,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                inserted_count += 1
            except Exception as e:
                error_msg = str(e).lower()
                # Check if it's a unique constraint violation
                if "unique" in error_msg or "duplicate" in error_msg or "violation" in error_msg:
                    skipped_count += 1
                    continue
                else:
                    # Re-raise if it's a different error
                    raise
        
        if inserted_count > 0:
            print(f"Inserted {inserted_count} new rows")
        if skipped_count > 0:
            print(f"Skipped {skipped_count} duplicate rows (already exist)")

    db.conn.commit()
    cursor.close()


def upsert_underlying_candles_5m(
    db: AzureSqlClient,
    rows: List[
        Tuple[
            str,
            date,
            datetime,
            float,
            float,
            float,
            float,
            int | None,
        ]
    ],
) -> None:
    """
    Upsert rows into dbo.UnderlyingCandle5m.

    Each row: (underlying, trade_date, candle_time, open_price, high_price, low_price, close_price, volume)
    """
    if not rows:
        print("No 5-minute candle rows to upsert.")
        return

    cursor = db.conn.cursor()
    cursor.fast_executemany = True

    # Prepare data for bulk insert
    insert_rows = []
    for (
        underlying,
        trade_date,
        candle_time,
        open_p,
        high_p,
        low_p,
        close_p,
        volume,
    ) in rows:
        insert_rows.append((
            underlying,
            trade_date,
            candle_time,
            open_p,
            high_p,
            low_p,
            close_p,
            volume,
        ))

    # Check which rows already exist (batch query)
    existing_keys = set()
    if insert_rows:
        # Get unique (underlying, candle_time) pairs
        unique_keys = list({(row[0], row[2]) for row in insert_rows})
        
        # Query existing rows in batches (SQL Server limit is 2100 parameters)
        batch_size = 500  # Conservative limit (2 params per pair)
        for i in range(0, len(unique_keys), batch_size):
            batch = unique_keys[i:i + batch_size]
            conditions = []
            params = []
            for underlying, candle_time in batch:
                conditions.append("(underlying = ? AND candle_time = ?)")
                params.extend([underlying, candle_time])
            
            cursor.execute(
                f"""
                SELECT underlying, candle_time
                FROM dbo.UnderlyingCandle5m
                WHERE {' OR '.join(conditions)}
                """,
                params,
            )
            for row in cursor.fetchall():
                existing_keys.add((row[0], row[1]))

    # Separate inserts and updates
    to_insert = []
    to_update = []
    
    for row in insert_rows:
        underlying, candle_time = row[0], row[2]
        if (underlying, candle_time) in existing_keys:
            to_update.append(row)
        else:
            to_insert.append(row)

    # Perform updates
    if to_update:
        cursor.executemany(
            """
            UPDATE dbo.UnderlyingCandle5m
            SET trade_date = ?, open_price = ?, high_price = ?, low_price = ?,
                close_price = ?, volume = ?
            WHERE underlying = ? AND candle_time = ?
            """,
            [(row[1], row[3], row[4], row[5], row[6], row[7], row[0], row[2]) for row in to_update],
        )
        print(f"Updated {len(to_update)} existing 5-minute candle rows")

    # Perform inserts (handle unique constraint violations)
    if to_insert:
        inserted_count = 0
        skipped_count = 0
        
        # Insert one by one to catch and skip duplicates
        for row in to_insert:
            try:
                cursor.execute(
                    """
                    INSERT INTO dbo.UnderlyingCandle5m (
                        underlying,
                        trade_date,
                        candle_time,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                inserted_count += 1
            except Exception as e:
                error_msg = str(e).lower()
                # Check if it's a unique constraint violation
                if "unique" in error_msg or "duplicate" in error_msg or "violation" in error_msg:
                    skipped_count += 1
                    continue
                else:
                    # Re-raise if it's a different error
                    raise
        
        if inserted_count > 0:
            print(f"Inserted {inserted_count} new 5-minute candle rows")
        if skipped_count > 0:
            print(f"Skipped {skipped_count} duplicate 5-minute candle rows (already exist)")

    db.conn.commit()
    cursor.close()


def main() -> None:
    settings = get_settings()

    db = AzureSqlClient(settings)
    db.connect()

    kite_client = KiteClient(settings)
    kite_client.authenticate()

    start_date = date(2025, 1, 1)
    end_date = date(2025, 12, 31)
    print(f"Backfilling underlying NIFTY/BANKNIFTY daily candles for {start_date} to {end_date}")

    index_tokens = get_index_tokens(kite_client)

    # Historical API window for daily candles
    # Start from start_date at market open, end at end of end_date
    from_dt = datetime.combine(start_date, dtime(9, 15))
    to_dt = datetime.combine(end_date, dtime(15, 30))

    # Daily candle rows
    daily_rows: List[
        Tuple[str, date, datetime, float | None, float | None, float | None, float | None, int | None]
    ] = []

    # 5-minute candle rows
    candle_5m_rows: List[
        Tuple[str, date, datetime, float, float, float, float, int | None]
    ] = []

    # Current timestamp for loaded_at
    current_time = datetime.now()

    # Market hours for 5-minute candles (9:15 AM to 3:25 PM IST)
    market_start = dtime(9, 15)
    market_end = dtime(15, 25)

    for underlying, token in index_tokens.items():
        print(f"Fetching daily candles for {underlying} ({token})...")
        try:
            daily_candles = kite_client.kite.historical_data(
                token,
                from_dt,
                to_dt,
                interval="day",
                continuous=False,
                oi=False,
            )
        except Exception as e:
            print(f"[WARN] Failed fetching daily historical for {underlying}: {e}")
            daily_candles = []

        for c in daily_candles:
            c_dt = c["date"].replace(tzinfo=None)
            trade_dt = c_dt.date()

            if trade_dt < start_date or trade_dt > end_date:
                continue

            # Extract OHLCV from daily candle
            o = float(c["open"]) if c.get("open") is not None else None
            h = float(c["high"]) if c.get("high") is not None else None
            l = float(c["low"]) if c.get("low") is not None else None
            cl = float(c["close"]) if c.get("close") is not None else None
            vol = int(c["volume"]) if c.get("volume") is not None else None

            daily_rows.append(
                (
                    underlying,
                    trade_dt,       # trade_date (date of the daily candle)
                    current_time,   # loaded_at (timestamp when data is loaded)
                    o,              # open_price
                    h,              # high_price
                    l,              # low_price
                    cl,             # close_price
                    vol,            # volume
                )
            )

        # Fetch 5-minute candles for the same date range
        # Kite API limitation: max 100 days per request for intraday candles
        print(f"Fetching 5-minute candles for {underlying} ({token})...")
        candles_5m = []
        
        # Split date range into chunks of max 100 days
        chunk_size_days = 100
        current_start = start_date
        
        while current_start <= end_date:
            # Calculate chunk end date (max 100 days from start)
            chunk_end = min(current_start + timedelta(days=chunk_size_days - 1), end_date)
            
            # Convert to datetime for API call
            chunk_from_dt = datetime.combine(current_start, dtime(9, 15))
            chunk_to_dt = datetime.combine(chunk_end, dtime(15, 30))
            
            print(f"  Fetching 5-minute candles chunk: {current_start} to {chunk_end}...")
            try:
                chunk_candles = kite_client.kite.historical_data(
                    token,
                    chunk_from_dt,
                    chunk_to_dt,
                    interval="5minute",
                    continuous=False,
                    oi=False,
                )
                candles_5m.extend(chunk_candles)
                print(f"  Fetched {len(chunk_candles)} candles for this chunk")
            except Exception as e:
                print(f"  [WARN] Failed fetching 5-minute historical chunk for {underlying} ({current_start} to {chunk_end}): {e}")
                # Continue with next chunk even if this one fails
            
            # Move to next chunk
            current_start = chunk_end + timedelta(days=1)
        
        print(f"  Total 5-minute candles fetched: {len(candles_5m)}")

        for c in candles_5m:
            c_dt = c["date"].replace(tzinfo=None)
            trade_dt = c_dt.date()
            candle_time = c_dt

            if trade_dt < start_date or trade_dt > end_date:
                continue

            # Filter candles to market hours (9:15 AM to 3:25 PM IST)
            candle_time_only = candle_time.time()
            if candle_time_only < market_start or candle_time_only > market_end:
                continue

            # Extract OHLCV from 5-minute candle (all required, not null)
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])
            vol = int(c["volume"]) if c.get("volume") is not None else None

            candle_5m_rows.append(
                (
                    underlying,
                    trade_dt,       # trade_date (date of the candle)
                    candle_time,    # candle_time (start time of the 5-minute candle)
                    o,              # open_price
                    h,              # high_price
                    l,              # low_price
                    cl,             # close_price
                    vol,            # volume
                )
            )

    # Insert daily candles (handle duplicates gracefully)
    print(f"\nPrepared {len(daily_rows)} UnderlyingSnapshot rows")
    try:
        upsert_underlying_snapshots(db, daily_rows)
        print("UnderlyingSnapshot upsert complete.")
    except Exception as e:
        error_msg = str(e).lower()
        if "unique" in error_msg or "duplicate" in error_msg or "violation" in error_msg:
            print(f"[WARN] Some duplicate rows in UnderlyingSnapshot, continuing with 5-minute candles...")
        else:
            print(f"[ERROR] Failed to upsert UnderlyingSnapshot: {e}")
            raise

    # Insert 5-minute candles (handle duplicates gracefully)
    print(f"\nPrepared {len(candle_5m_rows)} UnderlyingCandle5m rows")
    try:
        upsert_underlying_candles_5m(db, candle_5m_rows)
        print("UnderlyingCandle5m upsert complete.")
    except Exception as e:
        error_msg = str(e).lower()
        if "unique" in error_msg or "duplicate" in error_msg or "violation" in error_msg:
            print(f"[WARN] Some duplicate rows in UnderlyingCandle5m, continuing...")
        else:
            print(f"[ERROR] Failed to upsert UnderlyingCandle5m: {e}")
            raise

    db.close()
    print("Underlying backfill script done.")


if __name__ == "__main__":
    main()
