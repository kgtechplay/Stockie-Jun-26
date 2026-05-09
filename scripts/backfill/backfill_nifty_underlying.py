# scripts/backfill/backfill_nifty_underlying.py

import sys
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Tuple

from dotenv import load_dotenv

# Add project root
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient
from src.common.models import WatchedInstrument
from src.data_manager.kite_client import KiteClient

load_dotenv()

# NSE uses different tradingsymbols for indices; map to our canonical names
KITE_TO_CANONICAL: Dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}

# Default backfill window - override via --start / --end
BACKFILL_START = date(2025, 1, 1)
BACKFILL_END = date(2025, 12, 31)


def _resolve_tokens(
    kite_client: KiteClient,
    instruments: List[WatchedInstrument],
) -> Dict[str, int]:
    """
    Build tradingsymbol -> Kite instrument_token mapping.
    Uses instrument_token stored in WatchedInstrument if populated;
    falls back to a live Kite instruments-list lookup for any missing entries.
    """
    mapping: Dict[str, int] = {}
    need_lookup: List[WatchedInstrument] = []

    for inst in instruments:
        if inst.instrument_token:
            mapping[inst.tradingsymbol] = inst.instrument_token
        else:
            need_lookup.append(inst)

    if need_lookup:
        exchange = need_lookup[0].exchange or "NSE"
        kite_instr = kite_client.kite.instruments(exchange)
        need_set = {inst.tradingsymbol for inst in need_lookup}
        for ki in kite_instr:
            ts = ki.get("tradingsymbol", "")
            canonical = KITE_TO_CANONICAL.get(ts, ts)
            if canonical in need_set and canonical not in mapping:
                mapping[canonical] = ki["instrument_token"]
        missing = need_set - mapping.keys()
        if missing:
            print(f"[WARN] Could not resolve Kite tokens for: {missing}")

    return mapping


def upsert_underlying_snapshots(
    db: DatabaseClient,
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
) -> Dict[str, int]:
    """
    Upsert rows into dbo.UnderlyingSnapshot.

    Each row: (underlying, trade_date, loaded_at, open_price, high_price, low_price, close_price, volume)
    """
    if not rows:
        print("No underlying snapshot rows to upsert.")
        return {"prepared": 0, "updated": 0, "inserted": 0, "skipped_duplicates": 0}

    cursor = db.conn.cursor()
    cursor.fast_executemany = True

    insert_rows = []
    for (underlying, trade_date, loaded_at, open_p, high_p, low_p, close_p, volume) in rows:
        insert_rows.append((underlying, trade_date, loaded_at, open_p, high_p, low_p, close_p, volume))

    # Check which rows already exist
    existing_keys = set()
    if insert_rows:
        unique_keys = list({(row[0], row[1]) for row in insert_rows})
        batch_size = 500
        for i in range(0, len(unique_keys), batch_size):
            batch = unique_keys[i:i + batch_size]
            conditions = []
            params = []
            for underlying, trade_date in batch:
                conditions.append("(underlying = ? AND trade_date = ?)")
                params.extend([underlying, trade_date])
            cursor.execute(
                f"SELECT underlying, trade_date FROM dbo.UnderlyingSnapshot WHERE {' OR '.join(conditions)}",
                params,
            )
            for row in cursor.fetchall():
                td = row[1].date() if isinstance(row[1], datetime) else row[1]
                existing_keys.add((row[0], td))

    to_insert = []
    to_update = []
    for row in insert_rows:
        if (row[0], row[1]) in existing_keys:
            to_update.append(row)
        else:
            to_insert.append(row)

    updated_count = inserted_count = skipped_count = 0

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
        updated_count = len(to_update)
        print(f"Updated {updated_count} existing rows")

    if to_insert:
        batch_size = 5000
        for i in range(0, len(to_insert), batch_size):
            batch = to_insert[i:i + batch_size]
            try:
                cursor.executemany(
                    """
                    INSERT INTO dbo.UnderlyingSnapshot
                        (underlying, trade_date, loaded_at, open_price, high_price, low_price, close_price, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                inserted_count += len(batch)
            except Exception as e:
                err = str(e).lower()
                if "unique" in err or "duplicate" in err or "violation" in err:
                    for row in batch:
                        try:
                            cursor.execute(
                                """
                                INSERT INTO dbo.UnderlyingSnapshot
                                    (underlying, trade_date, loaded_at, open_price, high_price, low_price, close_price, volume)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                row,
                            )
                            inserted_count += 1
                        except Exception as e2:
                            if any(k in str(e2).lower() for k in ("unique", "duplicate", "violation")):
                                skipped_count += 1
                            else:
                                raise
                else:
                    raise
        if inserted_count:
            print(f"Inserted {inserted_count} new rows")
        if skipped_count:
            print(f"Skipped {skipped_count} duplicate rows")

    db.conn.commit()
    cursor.close()
    return {"prepared": len(rows), "updated": updated_count, "inserted": inserted_count, "skipped_duplicates": skipped_count}


def upsert_underlying_candles_5m(
    db: DatabaseClient,
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
) -> Dict[str, int]:
    """
    Upsert rows into dbo.UnderlyingCandle5m.

    Each row: (underlying, trade_date, candle_time, open_price, high_price, low_price, close_price, volume)
    """
    if not rows:
        print("No 5-minute candle rows to upsert.")
        return {"prepared": 0, "updated": 0, "inserted": 0, "skipped_duplicates": 0}

    cursor = db.conn.cursor()
    cursor.fast_executemany = True

    insert_rows = []
    for (underlying, trade_date, candle_time, open_p, high_p, low_p, close_p, volume) in rows:
        insert_rows.append((underlying, trade_date, candle_time, open_p, high_p, low_p, close_p, volume))

    existing_keys = set()
    if insert_rows:
        unique_keys = list({(row[0], row[2]) for row in insert_rows})
        batch_size = 500
        for i in range(0, len(unique_keys), batch_size):
            batch = unique_keys[i:i + batch_size]
            conditions = []
            params = []
            for underlying, candle_time in batch:
                conditions.append("(underlying = ? AND candle_time = ?)")
                params.extend([underlying, candle_time])
            cursor.execute(
                f"SELECT underlying, candle_time FROM dbo.UnderlyingCandle5m WHERE {' OR '.join(conditions)}",
                params,
            )
            for row in cursor.fetchall():
                ct = row[1].replace(tzinfo=None) if hasattr(row[1], "tzinfo") else row[1]
                existing_keys.add((row[0], ct))

    to_insert = []
    to_update = []
    for row in insert_rows:
        if (row[0], row[2]) in existing_keys:
            to_update.append(row)
        else:
            to_insert.append(row)

    updated_count = inserted_count = skipped_count = 0

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
        updated_count = len(to_update)
        print(f"Updated {updated_count} existing 5-minute candle rows")

    if to_insert:
        batch_size = 5000
        for i in range(0, len(to_insert), batch_size):
            batch = to_insert[i:i + batch_size]
            try:
                cursor.executemany(
                    """
                    INSERT INTO dbo.UnderlyingCandle5m
                        (underlying, trade_date, candle_time, open_price, high_price, low_price, close_price, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                inserted_count += len(batch)
            except Exception as e:
                err = str(e).lower()
                if "unique" in err or "duplicate" in err or "violation" in err:
                    for row in batch:
                        try:
                            cursor.execute(
                                """
                                INSERT INTO dbo.UnderlyingCandle5m
                                    (underlying, trade_date, candle_time, open_price, high_price, low_price, close_price, volume)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                row,
                            )
                            inserted_count += 1
                        except Exception as e2:
                            if any(k in str(e2).lower() for k in ("unique", "duplicate", "violation")):
                                skipped_count += 1
                            else:
                                raise
                else:
                    raise
            if len(to_insert) > batch_size:
                print(f"  Inserted batch {i // batch_size + 1}/{(len(to_insert) - 1) // batch_size + 1} ({inserted_count + skipped_count}/{len(to_insert)} rows done)")

        print(f"Inserted {inserted_count} new 5-minute candle rows")
        if skipped_count:
            print(f"Skipped {skipped_count} duplicate 5-minute candle rows")

    db.conn.commit()
    cursor.close()
    return {"prepared": len(rows), "updated": updated_count, "inserted": inserted_count, "skipped_duplicates": skipped_count}


def _get_missing_ranges(
    db: DatabaseClient,
    symbol: str,
    start_date: date,
    end_date: date,
) -> Tuple[List[Tuple[date, date]], List[Tuple[date, date]]]:
    """
    Return (snapshot_gaps, candle_gaps) — date sub-ranges within
    [start_date, end_date] not yet in the DB for `symbol`.
    Checks leading/trailing edges only; internal gaps are not detected.
    """
    cursor = db.conn.cursor()

    def _to_date(val):
        if val is None:
            return None
        return val.date() if isinstance(val, datetime) else val

    def _gaps(db_min, db_max) -> List[Tuple[date, date]]:
        if db_min is None:
            return [(start_date, end_date)]
        gaps = []
        if db_min > start_date:
            gaps.append((start_date, db_min - timedelta(days=1)))
        if db_max < end_date:
            gaps.append((db_max + timedelta(days=1), end_date))
        return gaps

    cursor.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM dbo.UnderlyingSnapshot "
        "WHERE underlying = ? AND trade_date >= ? AND trade_date <= ?",
        symbol, start_date, end_date,
    )
    r = cursor.fetchone()
    snap_gaps = _gaps(_to_date(r[0]), _to_date(r[1]))

    cursor.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM dbo.UnderlyingCandle5m "
        "WHERE underlying = ? AND trade_date >= ? AND trade_date <= ?",
        symbol, start_date, end_date,
    )
    r = cursor.fetchone()
    candle_gaps = _gaps(_to_date(r[0]), _to_date(r[1]))

    cursor.close()
    return snap_gaps, candle_gaps


def run_backfill_underlying(
    start_date: date,
    end_date: date,
    underlyings: List[str] | None = None,
) -> Dict[str, object]:
    settings = get_settings()

    # Load active INDEX instruments from WatchedInstrument
    tdb = DatabaseClient(settings)
    tdb.connect()
    try:
        watched = tdb.get_watched_instruments(instrument_type="INDEX")
    finally:
        tdb.close()

    # Filter to requested underlyings if specified (e.g. called from index_backfill_service)
    if underlyings:
        target_set = {u.upper() for u in underlyings}
        watched = [w for w in watched if w.tradingsymbol in target_set]

    if not watched:
        print("No active INDEX instruments found in WatchedInstrument.")
        return {
            "underlyings": [],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": {},
            "candles_5m": {},
        }

    target_underlyings = [w.tradingsymbol for w in watched]

    print(f"Backfilling {', '.join(target_underlyings)} daily + 5m candles for {start_date} to {end_date}")

    # Phase 1: resolve Kite tokens (fast — no historical API calls yet)
    kite_client = KiteClient(settings)
    kite_client.authenticate()
    token_map = _resolve_tokens(kite_client, watched)
    if not token_map:
        print("[ERROR] No instrument tokens resolved. Aborting.")
        return {
            "underlyings": target_underlyings,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": {},
            "candles_5m": {},
        }

    # Phase 2: check DB coverage per instrument, then close before Kite fetching
    db = DatabaseClient(settings)
    db.connect()
    coverage: Dict[str, Tuple[List[Tuple[date, date]], List[Tuple[date, date]]]] = {}
    print("\nChecking DB coverage...")
    for symbol in token_map:
        snap_gaps, candle_gaps = _get_missing_ranges(db, symbol, start_date, end_date)
        coverage[symbol] = (snap_gaps, candle_gaps)
        if not snap_gaps and not candle_gaps:
            print(f"  {symbol}: fully covered, skipping Kite fetch")
        else:
            snap_str = ", ".join(f"{a} to {b}" for a, b in snap_gaps) or "none"
            candle_str = ", ".join(f"{a} to {b}" for a, b in candle_gaps) or "none"
            print(f"  {symbol}: snap gaps=[{snap_str}]  candle gaps=[{candle_str}]")
    db.close()

    # Phase 3: fetch from Kite only for missing date ranges
    current_time = datetime.now()
    market_start = dtime(9, 15)
    market_end = dtime(15, 25)
    chunk_size_days = 100

    daily_rows: List[Tuple[str, date, datetime, float | None, float | None, float | None, float | None, int | None]] = []
    candle_5m_rows: List[Tuple[str, date, datetime, float, float, float, float, int | None]] = []

    for underlying, token in token_map.items():
        snap_gaps, candle_gaps = coverage[underlying]

        if snap_gaps:
            print(f"\n{underlying}: fetching daily snapshots...")
            for gap_start, gap_end in snap_gaps:
                print(f"  Gap: {gap_start} to {gap_end}")
                try:
                    daily_candles = kite_client.kite.historical_data(
                        token,
                        datetime.combine(gap_start, dtime(9, 15)),
                        datetime.combine(gap_end, dtime(15, 30)),
                        interval="day", continuous=False, oi=False,
                    )
                except Exception as e:
                    print(f"  [WARN] Failed daily fetch: {e}")
                    daily_candles = []
                for c in daily_candles:
                    c_dt = c["date"].replace(tzinfo=None)
                    trade_dt = c_dt.date()
                    if gap_start <= trade_dt <= gap_end:
                        daily_rows.append((
                            underlying, trade_dt, current_time,
                            float(c["open"]) if c.get("open") is not None else None,
                            float(c["high"]) if c.get("high") is not None else None,
                            float(c["low"]) if c.get("low") is not None else None,
                            float(c["close"]) if c.get("close") is not None else None,
                            int(c["volume"]) if c.get("volume") is not None else None,
                        ))
            print(f"  {underlying}: {len([r for r in daily_rows if r[0] == underlying])} daily rows queued")
        else:
            print(f"\n{underlying}: daily snapshots fully covered, skipping")

        if candle_gaps:
            print(f"{underlying}: fetching 5m candles...")
            for gap_start, gap_end in candle_gaps:
                print(f"  Gap: {gap_start} to {gap_end}")
                chunk_start = gap_start
                while chunk_start <= gap_end:
                    chunk_end = min(chunk_start + timedelta(days=chunk_size_days - 1), gap_end)
                    print(f"    Chunk: {chunk_start} to {chunk_end}...")
                    try:
                        chunk_candles = kite_client.kite.historical_data(
                            token,
                            datetime.combine(chunk_start, dtime(9, 15)),
                            datetime.combine(chunk_end, dtime(15, 30)),
                            interval="5minute", continuous=False, oi=False,
                        )
                        print(f"    Fetched {len(chunk_candles)} candles")
                        for c in chunk_candles:
                            c_dt = c["date"].replace(tzinfo=None)
                            trade_dt = c_dt.date()
                            if gap_start <= trade_dt <= gap_end and market_start <= c_dt.time() <= market_end:
                                candle_5m_rows.append((
                                    underlying, trade_dt, c_dt,
                                    float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]),
                                    int(c["volume"]) if c.get("volume") is not None else None,
                                ))
                    except Exception as e:
                        print(f"    [WARN] Failed chunk: {e}")
                    chunk_start = chunk_end + timedelta(days=1)
        else:
            print(f"{underlying}: 5m candles fully covered, skipping")

    # Phase 4: reconnect DB (fresh connection — avoids idle-timeout TCP drops)
    db.connect()

    print(f"\nPrepared {len(daily_rows)} UnderlyingSnapshot rows")
    snapshot_summary = {"prepared": 0, "updated": 0, "inserted": 0, "skipped_duplicates": 0}
    try:
        snapshot_summary = upsert_underlying_snapshots(db, daily_rows)
        print("UnderlyingSnapshot upsert complete.")
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "violation" in err:
            print("[WARN] Some duplicate rows in UnderlyingSnapshot, continuing...")
        else:
            print(f"[ERROR] UnderlyingSnapshot upsert failed: {e}")
            raise

    print(f"\nPrepared {len(candle_5m_rows)} UnderlyingCandle5m rows")
    candle_summary = {"prepared": 0, "updated": 0, "inserted": 0, "skipped_duplicates": 0}
    try:
        candle_summary = upsert_underlying_candles_5m(db, candle_5m_rows)
        print("UnderlyingCandle5m upsert complete.")
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "violation" in err:
            print("[WARN] Some duplicate rows in UnderlyingCandle5m, continuing...")
        else:
            print(f"[ERROR] UnderlyingCandle5m upsert failed: {e}")
            raise

    db.close()
    print("Underlying backfill done.")
    return {
        "underlyings": target_underlyings,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": snapshot_summary,
        "candles_5m": candle_summary,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Backfill underlying daily + 5m candles for all WatchedInstrument INDEX entries")
    parser.add_argument("--start", default=BACKFILL_START.isoformat(), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=BACKFILL_END.isoformat(), help="End date YYYY-MM-DD")
    args = parser.parse_args()

    run_backfill_underlying(
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
    )


if __name__ == "__main__":
    main()
