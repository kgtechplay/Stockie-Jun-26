# scripts/backfill_NIFTY/backfill_NIFTYoptions_from_historical.py
"""
Backfill dbo.OptionSnapshot from historical 5-minute option candles
for ALL option instruments of a given underlying.

This version intentionally removes selected-chain logic:
  - no ATM calculation
  - no strike range filter by spot
  - no nearest-expiry slicing
  - no max_expiries logic

It:
  - reads option contracts from dbo.OptionInstrument
  - filters only by underlying, option type, expiry >= trade_date, and optional CLI filters
  - fetches only the 09:15 and 15:10 5-minute candles
  - stores candle close as last_price
  - keeps bid/ask quote-only fields NULL
  - sets data_source = KITE_HISTORICAL_5M_CLOSE_PROXY
  - optionally calculates dbo.OptionSnapshotCalc after OptionSnapshot upsert

Important:
  Kite may still return no candles for expired options, even if tokens exist.
  This script removes chain selection; it does not remove Kite's expired-option-data limitation.
"""

from __future__ import annotations

import argparse
import re
import sys
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.db.database_client import DatabaseClient
from src.data_manager.kite_client import KiteClient
from scripts.Common.calculate_option_snapshot_calc import Settings as CalcSettings
from scripts.Common.calculate_option_snapshot_calc import calculate_snapshot_ids


load_dotenv(project_root / ".env")

DATA_SOURCE = "KITE_HISTORICAL_5M_CLOSE_PROXY"

KITE_TO_CANONICAL_INDEX = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}

DEFAULT_INDEX_TOKENS = {
    "NIFTY": 256265,
    "BANKNIFTY": 260105,
}

SNAPSHOT_WINDOWS: dict[str, tuple[time, time]] = {
    # label       candle start, snapshot timestamp stored in DB
    "OPEN_0915": (time(9, 15), time(9, 15)),
    "CLOSE_1515": (time(15, 10), time(15, 15)),
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class HistoricalAllOptionsBackfillSettings:
    option_instrument_table: str = "dbo.OptionInstrument"
    option_snapshot_table: str = "dbo.OptionSnapshot"
    sleep_seconds: float = 0.35
    option_batch_size: int = 10
    batch_sleep_seconds: float = 2.0
    calc_batch_size: int = 500
    log_every: int = 25
    debug_no_candle: bool = False


def safe_table_name(name: str) -> str:
    parts = name.split(".")
    if len(parts) not in (1, 2):
        raise ValueError(f"Invalid table name: {name}")

    for part in parts:
        if not _IDENTIFIER_RE.match(part):
            raise ValueError(f"Invalid table identifier: {part}")

    return ".".join(f"[{part}]" for part in parts)


def pg_table_name(name: str) -> str:
    short_name = name.split(".")[-1]
    if not _IDENTIFIER_RE.match(short_name):
        raise ValueError(f"Invalid table identifier: {short_name}")
    return f'"{short_name}"'


def table_object_name(name: str) -> str:
    if "." in name:
        return name
    return f"dbo.{name}"


def ensure_snapshot_schema(db: DatabaseClient, snapshot_table: str) -> None:
    if getattr(db, "db_kind", "") == "postgres":
        if hasattr(db, "create_core_tables"):
            try:
                db.create_core_tables()
            except Exception as exc:
                db.conn.rollback()
                print(f"[WARN] Supabase schema check skipped after error: {exc}")
        return

    full_name = table_object_name(snapshot_table)
    table = safe_table_name(snapshot_table)

    cursor = db.conn.cursor()

    cursor.execute(
        f"""
        IF COL_LENGTH('{full_name}', 'data_source') IS NULL
        BEGIN
            ALTER TABLE {table}
            ADD data_source VARCHAR(50) NULL;
        END
        """
    )

    cursor.execute(
        f"""
        IF NOT EXISTS (
            SELECT 1
            FROM sys.indexes
            WHERE name = 'UX_OptionSnapshot_Instrument_Date_Label'
              AND object_id = OBJECT_ID('{full_name}')
        )
        BEGIN
            CREATE UNIQUE INDEX UX_OptionSnapshot_Instrument_Date_Label
            ON {table}(option_instrument_id, trade_date, snapshot_label);
        END
        """
    )

    db.conn.commit()
    cursor.close()


def iter_dates(from_date: date, to_date: date) -> Iterable[date]:
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def chunked(items: list[Any], chunk_size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def is_kite_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "incorrect `api_key` or `access_token`" in message or "invalid api_key or access_token" in message


def resolve_underlying_tokens(
    db: DatabaseClient,
    kite_client: KiteClient,
    underlyings: list[str],
) -> dict[str, int]:
    requested = {u.upper() for u in underlyings}
    mapping: dict[str, int] = {}

    try:
        watched = db.get_watched_instruments()
        for inst in watched:
            symbol = inst.tradingsymbol.upper()
            if symbol in requested and inst.instrument_token:
                mapping[symbol] = int(inst.instrument_token)
    except Exception as exc:
        print(f"[WARN] Could not read watched instruments for underlying tokens: {exc}")

    unresolved = requested - mapping.keys()

    for symbol in list(unresolved):
        default_token = DEFAULT_INDEX_TOKENS.get(symbol)
        if default_token:
            mapping[symbol] = default_token
            unresolved.remove(symbol)

    if unresolved:
        try:
            nse_instr = kite_client.kite.instruments("NSE")
            for ki in nse_instr:
                ts = ki.get("tradingsymbol", "")
                canonical = KITE_TO_CANONICAL_INDEX.get(ts, ts).upper()
                if canonical in unresolved:
                    mapping[canonical] = int(ki["instrument_token"])
            unresolved = requested - mapping.keys()
        except Exception as exc:
            print(f"[WARN] Could not resolve underlying tokens from Kite NSE instruments: {exc}")

    if unresolved:
        print(f"[WARN] Could not resolve underlying tokens for: {sorted(unresolved)}")

    return mapping


def fetch_one_5m_candle(
    kite_client: KiteClient,
    instrument_token: int,
    trade_date: date,
    candle_start: time,
    include_oi: bool = True,
) -> dict[str, Any] | None:
    """
    Fetch one exact 5-minute candle.

    Uses a 10-minute query window but returns only the candle whose timestamp
    exactly matches candle_start.

    For options, first tries oi=True. If that errors, retries with oi=False.
    """
    from_dt = datetime.combine(trade_date, candle_start)
    to_dt = from_dt + timedelta(minutes=10)

    oi_attempts = [include_oi, False] if include_oi else [False]
    last_error: Exception | None = None

    for oi_flag in oi_attempts:
        try:
            candles = kite_client.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval="5minute",
                continuous=False,
                oi=oi_flag,
            )
        except Exception as exc:
            last_error = exc
            if oi_flag is True:
                continue
            raise

        if not candles:
            continue

        for candle in candles:
            candle_dt = candle.get("date")
            if not isinstance(candle_dt, datetime):
                continue

            candle_time = candle_dt.replace(tzinfo=None).time()
            if candle_time == candle_start:
                return candle

    if last_error:
        raise last_error

    return None


def get_underlying_snapshot_price(
    kite_client: KiteClient,
    underlying_token: int,
    trade_date: date,
    candle_start: time,
    sleep_seconds: float,
) -> float | None:
    candle = fetch_one_5m_candle(
        kite_client=kite_client,
        instrument_token=underlying_token,
        trade_date=trade_date,
        candle_start=candle_start,
        include_oi=False,
    )

    time_module.sleep(sleep_seconds)

    if not candle:
        return None

    return float(candle["close"])


def fetch_5m_candles_range(
    kite_client: KiteClient,
    instrument_token: int,
    from_date: date,
    to_date: date,
    include_oi: bool = True,
) -> list[dict[str, Any]]:
    from_dt = datetime.combine(from_date, time(9, 0))
    to_dt = datetime.combine(to_date, time(15, 30))

    oi_attempts = [include_oi, False] if include_oi else [False]
    last_error: Exception | None = None

    for oi_flag in oi_attempts:
        try:
            return kite_client.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval="5minute",
                continuous=False,
                oi=oi_flag,
            )
        except Exception as exc:
            last_error = exc
            if oi_flag is True:
                continue
            raise

    if last_error:
        raise last_error

    return []


def build_underlying_price_map_from_range(
    candles: list[dict[str, Any]],
    from_date: date,
    to_date: date,
) -> dict[tuple[date, str], float]:
    wanted_times = {
        candle_start: snapshot_label
        for snapshot_label, (candle_start, _snapshot_clock_time) in SNAPSHOT_WINDOWS.items()
    }
    prices: dict[tuple[date, str], float] = {}

    for candle in candles:
        candle_dt = candle.get("date")
        if not isinstance(candle_dt, datetime):
            continue

        candle_dt = candle_dt.replace(tzinfo=None)
        trade_dt = candle_dt.date()
        if trade_dt < from_date or trade_dt > to_date or trade_dt.weekday() >= 5:
            continue

        snapshot_label = wanted_times.get(candle_dt.time())
        if not snapshot_label:
            continue

        close_price = candle.get("close")
        if close_price is not None:
            prices[(trade_dt, snapshot_label)] = float(close_price)

    return prices


def load_all_option_instruments_for_snapshot(
    db: DatabaseClient,
    settings: HistoricalAllOptionsBackfillSettings,
    underlying: str,
    trade_date: date,
    option_type: str | None = None,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_instruments: int | None = None,
) -> list[dict[str, Any]]:
    """
    Load all option instruments for this underlying and trade date.

    Key filter:
      expiry >= trade_date

    This avoids fetching contracts that were already expired before the snapshot date.

    Optional filters are provided only to make testing safer:
      --expiry-from
      --expiry-to
      --strike-min
      --strike-max
      --max-instruments
    """
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = pg_table_name(settings.option_instrument_table) if is_postgres else safe_table_name(settings.option_instrument_table)
    placeholder = "%s" if is_postgres else "?"

    sql = f"""
        SELECT
            id,
            instrument_token,
            underlying,
            tradingsymbol,
            strike,
            CAST(expiry AS date) AS expiry,
            instrument_type
        FROM {table}
        WHERE underlying = {placeholder}
          AND instrument_token IS NOT NULL
          AND CAST(expiry AS date) >= {placeholder}
          AND instrument_type IN ('CE', 'PE')
    """

    params: list[Any] = [underlying, trade_date]

    if option_type:
        sql += f" AND instrument_type = {placeholder}"
        params.append(option_type)

    if expiry_from:
        sql += f" AND CAST(expiry AS date) >= {placeholder}"
        params.append(expiry_from)

    if expiry_to:
        sql += f" AND CAST(expiry AS date) <= {placeholder}"
        params.append(expiry_to)

    if strike_min is not None:
        sql += f" AND strike >= {placeholder}"
        params.append(strike_min)

    if strike_max is not None:
        sql += f" AND strike <= {placeholder}"
        params.append(strike_max)

    sql += " ORDER BY CAST(expiry AS date), strike, instrument_type"

    cursor = db.conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()

    instruments: list[dict[str, Any]] = []

    for row in rows:
        if is_postgres:
            instrument_id, instrument_token, row_underlying, tradingsymbol, strike, expiry, instrument_type = row
        else:
            instrument_id = row.id
            instrument_token = row.instrument_token
            row_underlying = row.underlying
            tradingsymbol = row.tradingsymbol
            strike = row.strike
            expiry = row.expiry
            instrument_type = row.instrument_type

        if instrument_token is None:
            continue

        instruments.append({
            "id": int(instrument_id),
            "instrument_token": int(instrument_token),
            "underlying": row_underlying,
            "tradingsymbol": tradingsymbol,
            "strike": float(strike),
            "expiry": _to_date(expiry),
            "instrument_type": instrument_type,
        })

        if max_instruments is not None and len(instruments) >= max_instruments:
            break

    return instruments


def load_all_option_instruments_for_range(
    db: DatabaseClient,
    settings: HistoricalAllOptionsBackfillSettings,
    underlying: str,
    from_date: date,
    option_type: str | None = None,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_instruments: int | None = None,
) -> list[dict[str, Any]]:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = pg_table_name(settings.option_instrument_table) if is_postgres else safe_table_name(settings.option_instrument_table)
    placeholder = "%s" if is_postgres else "?"

    sql = f"""
        SELECT
            id,
            instrument_token,
            underlying,
            tradingsymbol,
            strike,
            CAST(expiry AS date) AS expiry,
            instrument_type
        FROM {table}
        WHERE underlying = {placeholder}
          AND instrument_token IS NOT NULL
          AND CAST(expiry AS date) >= {placeholder}
          AND instrument_type IN ('CE', 'PE')
    """

    params: list[Any] = [underlying, from_date]

    if option_type:
        sql += f" AND instrument_type = {placeholder}"
        params.append(option_type)

    if expiry_from:
        sql += f" AND CAST(expiry AS date) >= {placeholder}"
        params.append(expiry_from)

    if expiry_to:
        sql += f" AND CAST(expiry AS date) <= {placeholder}"
        params.append(expiry_to)

    if strike_min is not None:
        sql += f" AND strike >= {placeholder}"
        params.append(strike_min)

    if strike_max is not None:
        sql += f" AND strike <= {placeholder}"
        params.append(strike_max)

    sql += " ORDER BY CAST(expiry AS date), strike, instrument_type"

    cursor = db.conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()

    instruments: list[dict[str, Any]] = []

    for row in rows:
        if is_postgres:
            instrument_id, instrument_token, row_underlying, tradingsymbol, strike, expiry, instrument_type = row
        else:
            instrument_id = row.id
            instrument_token = row.instrument_token
            row_underlying = row.underlying
            tradingsymbol = row.tradingsymbol
            strike = row.strike
            expiry = row.expiry
            instrument_type = row.instrument_type

        if instrument_token is None:
            continue

        instruments.append({
            "id": int(instrument_id),
            "instrument_token": int(instrument_token),
            "underlying": row_underlying,
            "tradingsymbol": tradingsymbol,
            "strike": float(strike),
            "expiry": _to_date(expiry),
            "instrument_type": instrument_type,
        })

        if max_instruments is not None and len(instruments) >= max_instruments:
            break

    return instruments


def build_snapshot_row_from_candle(
    instrument: dict[str, Any],
    candle: dict[str, Any],
    trade_date: date,
    snapshot_label: str,
    snapshot_time: datetime,
    underlying_price: float,
) -> dict[str, Any]:
    return {
        "option_instrument_id": instrument["id"],
        "snapshot_time": snapshot_time,
        "underlying_price": underlying_price,

        # Historical proxy value
        "last_price": float(candle["close"]),

        # Historical candles do not have executable market depth
        "bid_price": None,
        "bid_qty": None,
        "ask_price": None,
        "ask_qty": None,

        "volume": int(candle["volume"]) if candle.get("volume") is not None else None,
        "open_interest": int(candle["oi"]) if candle.get("oi") is not None else None,

        "trade_date": trade_date,
        "snapshot_label": snapshot_label,

        # Quote-only fields unavailable from historical candles
        "exchange_timestamp": None,
        "last_trade_time": None,
        "last_quantity": None,
        "average_price": None,
        "buy_quantity": None,
        "sell_quantity": None,
        "oi_day_high": None,
        "oi_day_low": None,
        "bid_orders": None,
        "ask_orders": None,

        "data_source": DATA_SOURCE,
    }


def upsert_option_snapshot(
    db: DatabaseClient,
    settings: HistoricalAllOptionsBackfillSettings,
    row: dict[str, Any],
) -> int | None:
    if getattr(db, "db_kind", "") == "postgres":
        table = pg_table_name(settings.option_snapshot_table)
        sql = f"""
            INSERT INTO {table}
            (
                option_instrument_id,
                snapshot_time,
                underlying_price,
                last_price,
                bid_price,
                bid_qty,
                ask_price,
                ask_qty,
                volume,
                open_interest,
                trade_date,
                snapshot_label,
                exchange_timestamp,
                last_trade_time,
                last_quantity,
                average_price,
                buy_quantity,
                sell_quantity,
                oi_day_high,
                oi_day_low,
                bid_orders,
                ask_orders,
                data_source
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (option_instrument_id, trade_date, snapshot_label)
            DO UPDATE SET
                snapshot_time = EXCLUDED.snapshot_time,
                underlying_price = EXCLUDED.underlying_price,
                last_price = EXCLUDED.last_price,
                bid_price = EXCLUDED.bid_price,
                bid_qty = EXCLUDED.bid_qty,
                ask_price = EXCLUDED.ask_price,
                ask_qty = EXCLUDED.ask_qty,
                volume = EXCLUDED.volume,
                open_interest = EXCLUDED.open_interest,
                exchange_timestamp = EXCLUDED.exchange_timestamp,
                last_trade_time = EXCLUDED.last_trade_time,
                last_quantity = EXCLUDED.last_quantity,
                average_price = EXCLUDED.average_price,
                buy_quantity = EXCLUDED.buy_quantity,
                sell_quantity = EXCLUDED.sell_quantity,
                oi_day_high = EXCLUDED.oi_day_high,
                oi_day_low = EXCLUDED.oi_day_low,
                bid_orders = EXCLUDED.bid_orders,
                ask_orders = EXCLUDED.ask_orders,
                data_source = EXCLUDED.data_source
            RETURNING id
        """

        params = [
            row["option_instrument_id"],
            row["snapshot_time"],
            row["underlying_price"],
            row["last_price"],
            row["bid_price"],
            row["bid_qty"],
            row["ask_price"],
            row["ask_qty"],
            row["volume"],
            row["open_interest"],
            row["trade_date"],
            row["snapshot_label"],
            row["exchange_timestamp"],
            row["last_trade_time"],
            row["last_quantity"],
            row["average_price"],
            row["buy_quantity"],
            row["sell_quantity"],
            row["oi_day_high"],
            row["oi_day_low"],
            row["bid_orders"],
            row["ask_orders"],
            row["data_source"],
        ]

        cursor = db.conn.cursor()
        cursor.execute(sql, params)
        result = cursor.fetchone()
        cursor.close()
        return int(result[0]) if result is not None else None

    table = safe_table_name(settings.option_snapshot_table)

    sql = f"""
        UPDATE {table}
        SET
            snapshot_time = ?,
            underlying_price = ?,
            last_price = ?,
            bid_price = ?,
            bid_qty = ?,
            ask_price = ?,
            ask_qty = ?,
            volume = ?,
            open_interest = ?,
            exchange_timestamp = ?,
            last_trade_time = ?,
            last_quantity = ?,
            average_price = ?,
            buy_quantity = ?,
            sell_quantity = ?,
            oi_day_high = ?,
            oi_day_low = ?,
            bid_orders = ?,
            ask_orders = ?,
            data_source = ?
        WHERE option_instrument_id = ?
          AND trade_date = ?
          AND snapshot_label = ?;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO {table}
            (
                option_instrument_id,
                snapshot_time,
                underlying_price,
                last_price,
                bid_price,
                bid_qty,
                ask_price,
                ask_qty,
                volume,
                open_interest,
                trade_date,
                snapshot_label,
                exchange_timestamp,
                last_trade_time,
                last_quantity,
                average_price,
                buy_quantity,
                sell_quantity,
                oi_day_high,
                oi_day_low,
                bid_orders,
                ask_orders,
                data_source
            )
            VALUES
            (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            );
        END
    """

    update_params = [
        row["snapshot_time"],
        row["underlying_price"],
        row["last_price"],
        row["bid_price"],
        row["bid_qty"],
        row["ask_price"],
        row["ask_qty"],
        row["volume"],
        row["open_interest"],
        row["exchange_timestamp"],
        row["last_trade_time"],
        row["last_quantity"],
        row["average_price"],
        row["buy_quantity"],
        row["sell_quantity"],
        row["oi_day_high"],
        row["oi_day_low"],
        row["bid_orders"],
        row["ask_orders"],
        row["data_source"],
        row["option_instrument_id"],
        row["trade_date"],
        row["snapshot_label"],
    ]

    insert_params = [
        row["option_instrument_id"],
        row["snapshot_time"],
        row["underlying_price"],
        row["last_price"],
        row["bid_price"],
        row["bid_qty"],
        row["ask_price"],
        row["ask_qty"],
        row["volume"],
        row["open_interest"],
        row["trade_date"],
        row["snapshot_label"],
        row["exchange_timestamp"],
        row["last_trade_time"],
        row["last_quantity"],
        row["average_price"],
        row["buy_quantity"],
        row["sell_quantity"],
        row["oi_day_high"],
        row["oi_day_low"],
        row["bid_orders"],
        row["ask_orders"],
        row["data_source"],
    ]

    cursor = db.conn.cursor()
    cursor.execute(sql, update_params + insert_params)

    result = cursor.execute(
        f"""
        SELECT id
        FROM {table}
        WHERE option_instrument_id = ?
          AND trade_date = ?
          AND snapshot_label = ?
        """,
        row["option_instrument_id"],
        row["trade_date"],
        row["snapshot_label"],
    ).fetchone()

    cursor.close()

    return int(result.id) if result is not None else None


def calculate_snapshot_ids_in_batches(
    db: DatabaseClient,
    snapshot_table: str,
    instrument_table: str,
    snapshot_ids: list[int],
    batch_size: int,
) -> dict[str, int]:
    totals = {
        "rows_processed": 0,
        "ok": 0,
        "non_ok": 0,
        "errors": 0,
    }

    if not snapshot_ids:
        return totals

    calc_settings = CalcSettings(
        option_snapshot_table=snapshot_table,
        option_instrument_table=instrument_table,
    )

    for batch in chunked(snapshot_ids, batch_size):
        result = calculate_snapshot_ids(db, calc_settings, batch)

        totals["rows_processed"] += result.get("rows_processed", 0)
        totals["ok"] += result.get("ok", 0)
        totals["non_ok"] += result.get("non_ok", 0)
        totals["errors"] += result.get("errors", 0)

    return totals


def run_backfill_all_options_from_historical_range(
    global_start: date,
    global_end: date,
    underlyings: list[str] | None = None,
    option_type: str | None = None,
    instrument_table: str = "dbo.OptionInstrument",
    snapshot_table: str = "dbo.OptionSnapshot",
    underlying_token: int | None = None,
    sleep_seconds: float = 0.35,
    option_batch_size: int = 10,
    batch_sleep_seconds: float = 2.0,
    skip_calc: bool = False,
    calc_batch_size: int = 500,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_instruments_per_snapshot: int | None = None,
    debug_no_candle: bool = False,
) -> dict[str, Any]:
    settings = HistoricalAllOptionsBackfillSettings(
        option_instrument_table=instrument_table,
        option_snapshot_table=snapshot_table,
        sleep_seconds=sleep_seconds,
        option_batch_size=max(1, option_batch_size),
        batch_sleep_seconds=max(0.0, batch_sleep_seconds),
        calc_batch_size=calc_batch_size,
        debug_no_candle=debug_no_candle,
    )

    app_settings = get_settings()
    kite_client = KiteClient(app_settings)
    kite_client.authenticate()

    db = get_database_client(app_settings)
    db.connect()

    totals = {
        "snapshots_attempted": 0,
        "instruments_seen": 0,
        "rows_upserted": 0,
        "skipped_no_underlying": 0,
        "skipped_no_instruments": 0,
        "skipped_no_option_candle": 0,
        "failed": 0,
        "calc_rows_processed": 0,
        "calc_ok": 0,
        "calc_non_ok": 0,
        "calc_errors": 0,
    }

    try:
        ensure_snapshot_schema(db, snapshot_table)

        if underlyings:
            target_underlyings = [u.strip().upper() for u in underlyings if u.strip()]
        else:
            target_underlyings = ["NIFTY"]

        underlying_tokens = resolve_underlying_tokens(db, kite_client, target_underlyings)

        if underlying_token and len(target_underlyings) == 1:
            underlying_tokens[target_underlyings[0]] = underlying_token

        print("Starting RANGE option historical proxy backfill")
        print(f"Date range                 : {global_start} to {global_end}")
        print(f"Underlyings                : {target_underlyings}")
        print(f"Option type                : {option_type or 'CE + PE'}")
        print(f"Expiry from                : {expiry_from or 'not set'}")
        print(f"Expiry to                  : {expiry_to or 'not set'}")
        print(f"Strike min                 : {strike_min if strike_min is not None else 'not set'}")
        print(f"Strike max                 : {strike_max if strike_max is not None else 'not set'}")
        print(f"Max instruments/range      : {max_instruments_per_snapshot or 'not set'}")
        print(f"Option batch size          : {settings.option_batch_size}")
        print(f"Batch sleep seconds        : {settings.batch_sleep_seconds}")
        print(f"Skip calc                  : {skip_calc}")
        print(f"Data source                : {DATA_SOURCE}")
        print("")

        for underlying in target_underlyings:
            token = underlying_tokens.get(underlying)

            if token is None:
                print(f"SKIP {underlying}: no underlying token")
                totals["skipped_no_underlying"] += 1
                continue

            underlying_candles = fetch_5m_candles_range(
                kite_client=kite_client,
                instrument_token=token,
                from_date=global_start,
                to_date=global_end,
                include_oi=False,
            )
            time_module.sleep(settings.sleep_seconds)
            underlying_prices = build_underlying_price_map_from_range(
                candles=underlying_candles,
                from_date=global_start,
                to_date=global_end,
            )

            instruments = load_all_option_instruments_for_range(
                db=db,
                settings=settings,
                underlying=underlying,
                from_date=global_start,
                option_type=option_type,
                expiry_from=expiry_from,
                expiry_to=expiry_to,
                strike_min=strike_min,
                strike_max=strike_max,
                max_instruments=max_instruments_per_snapshot,
            )

            if not instruments:
                totals["skipped_no_instruments"] += 1
                print(f"SKIP {underlying}: no option instruments")
                continue

            active_snapshot_keys = [
                (trade_dt, snapshot_label)
                for trade_dt in iter_dates(global_start, global_end)
                if trade_dt.weekday() < 5
                for snapshot_label in SNAPSHOT_WINDOWS
            ]
            totals["snapshots_attempted"] += len(active_snapshot_keys)

            print(
                f"START {underlying} range | "
                f"underlying_snapshot_prices={len(underlying_prices)} | "
                f"instruments={len(instruments)}"
            )

            candle_start_to_label = {
                candle_start: (snapshot_label, snapshot_clock_time)
                for snapshot_label, (candle_start, snapshot_clock_time) in SNAPSHOT_WINDOWS.items()
            }
            all_snapshot_ids: list[int] = []
            instrument_batches = list(chunked(instruments, settings.option_batch_size))
            processed = 0
            stop_for_auth_error = False

            for batch_no, instrument_batch in enumerate(instrument_batches, start=1):
                if stop_for_auth_error:
                    break

                batch_snapshot_ids: list[int] = []
                batch_no_candle = 0
                batch_failed = 0

                for inst in instrument_batch:
                    if stop_for_auth_error:
                        break

                    processed += 1
                    totals["instruments_seen"] += 1

                    try:
                        option_candles = fetch_5m_candles_range(
                            kite_client=kite_client,
                            instrument_token=inst["instrument_token"],
                            from_date=global_start,
                            to_date=global_end,
                            include_oi=True,
                        )
                        time_module.sleep(settings.sleep_seconds)

                        matched_for_instrument = 0
                        for candle in option_candles:
                            candle_dt = candle.get("date")
                            if not isinstance(candle_dt, datetime):
                                continue

                            candle_dt = candle_dt.replace(tzinfo=None)
                            trade_dt = candle_dt.date()
                            if trade_dt < global_start or trade_dt > global_end:
                                continue
                            if trade_dt.weekday() >= 5:
                                continue
                            expiry = inst.get("expiry")
                            if expiry is not None and expiry < trade_dt:
                                continue

                            label_and_time = candle_start_to_label.get(candle_dt.time())
                            if not label_and_time:
                                continue

                            snapshot_label, snapshot_clock_time = label_and_time
                            underlying_price = underlying_prices.get((trade_dt, snapshot_label))
                            if underlying_price is None:
                                totals["skipped_no_underlying"] += 1
                                continue

                            row = build_snapshot_row_from_candle(
                                instrument=inst,
                                candle=candle,
                                trade_date=trade_dt,
                                snapshot_label=snapshot_label,
                                snapshot_time=datetime.combine(trade_dt, snapshot_clock_time),
                                underlying_price=underlying_price,
                            )

                            snapshot_id = upsert_option_snapshot(db, settings, row)
                            if snapshot_id is not None:
                                all_snapshot_ids.append(snapshot_id)
                                batch_snapshot_ids.append(snapshot_id)
                            totals["rows_upserted"] += 1
                            matched_for_instrument += 1

                        expected_for_instrument = sum(
                            1
                            for trade_dt, snapshot_label in active_snapshot_keys
                            if inst.get("expiry") is None
                            or inst["expiry"] >= trade_dt
                            and (trade_dt, snapshot_label) in underlying_prices
                        )
                        if matched_for_instrument < expected_for_instrument:
                            missing = expected_for_instrument - matched_for_instrument
                            totals["skipped_no_option_candle"] += missing
                            batch_no_candle += missing

                        if processed % settings.log_every == 0:
                            print(
                                f"  progress {processed}/{len(instruments)} | "
                                f"batch={batch_no}/{len(instrument_batches)} | "
                                f"upserted_total={totals['rows_upserted']} | "
                                f"no_candle_total={totals['skipped_no_option_candle']}"
                            )

                    except Exception as exc:
                        totals["failed"] += 1
                        batch_failed += 1
                        if is_kite_auth_error(exc):
                            stop_for_auth_error = True
                        print(
                            "FAILED option range | "
                            f"{underlying} | {inst.get('tradingsymbol')} | "
                            f"token={inst.get('instrument_token')} | {exc}"
                        )
                        if stop_for_auth_error:
                            print("ABORT range backfill: Kite authentication failed. Refresh KITE_ACCESS_TOKEN and rerun.")
                            break

                db.conn.commit()

                if not skip_calc and batch_snapshot_ids:
                    calc_result = calculate_snapshot_ids_in_batches(
                        db=db,
                        snapshot_table=snapshot_table,
                        instrument_table=instrument_table,
                        snapshot_ids=batch_snapshot_ids,
                        batch_size=settings.calc_batch_size,
                    )
                    totals["calc_rows_processed"] += calc_result["rows_processed"]
                    totals["calc_ok"] += calc_result["ok"]
                    totals["calc_non_ok"] += calc_result["non_ok"]
                    totals["calc_errors"] += calc_result["errors"]

                print(
                    f"  batch {batch_no}/{len(instrument_batches)} done | "
                    f"processed={processed}/{len(instruments)} | "
                    f"batch_rows={len(batch_snapshot_ids)} | "
                    f"batch_missing_candles={batch_no_candle} | "
                    f"batch_failed={batch_failed} | "
                    f"range_rows={len(all_snapshot_ids)}"
                )

                if batch_no < len(instrument_batches) and settings.batch_sleep_seconds > 0:
                    time_module.sleep(settings.batch_sleep_seconds)

            if stop_for_auth_error:
                break

            print(
                f"DONE {underlying} range | "
                f"instruments={len(instruments)} | "
                f"snapshot_rows={len(all_snapshot_ids)} | "
                f"upserted_total={totals['rows_upserted']} | "
                f"no_candle_total={totals['skipped_no_option_candle']}"
            )

    finally:
        db.close()

    result = {
        "underlyings": underlyings or ["NIFTY"],
        "start_date": global_start.isoformat(),
        "end_date": global_end.isoformat(),
        "data_source": DATA_SOURCE,
        "mode": "RANGE_OPTION_INSTRUMENTS_NO_CHAIN_SELECTION",
        **totals,
    }

    print("")
    print("Backfill completed")
    print(result)

    return result


def run_backfill_all_options_from_historical(
    global_start: date,
    global_end: date,
    underlyings: list[str] | None = None,
    option_type: str | None = None,
    instrument_table: str = "dbo.OptionInstrument",
    snapshot_table: str = "dbo.OptionSnapshot",
    underlying_token: int | None = None,
    sleep_seconds: float = 0.35,
    option_batch_size: int = 10,
    batch_sleep_seconds: float = 2.0,
    skip_calc: bool = False,
    calc_batch_size: int = 500,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_instruments_per_snapshot: int | None = None,
    debug_no_candle: bool = False,
) -> dict[str, Any]:
    settings = HistoricalAllOptionsBackfillSettings(
        option_instrument_table=instrument_table,
        option_snapshot_table=snapshot_table,
        sleep_seconds=sleep_seconds,
        option_batch_size=max(1, option_batch_size),
        batch_sleep_seconds=max(0.0, batch_sleep_seconds),
        calc_batch_size=calc_batch_size,
        debug_no_candle=debug_no_candle,
    )

    app_settings = get_settings()

    kite_client = KiteClient(app_settings)
    kite_client.authenticate()

    db = get_database_client(app_settings)
    db.connect()

    totals = {
        "snapshots_attempted": 0,
        "instruments_seen": 0,
        "rows_upserted": 0,
        "skipped_no_underlying": 0,
        "skipped_no_instruments": 0,
        "skipped_no_option_candle": 0,
        "failed": 0,
        "calc_rows_processed": 0,
        "calc_ok": 0,
        "calc_non_ok": 0,
        "calc_errors": 0,
    }

    try:
        ensure_snapshot_schema(db, snapshot_table)

        if underlyings:
            target_underlyings = [u.strip().upper() for u in underlyings if u.strip()]
        else:
            target_underlyings = ["NIFTY"]

        underlying_tokens = resolve_underlying_tokens(db, kite_client, target_underlyings)

        if underlying_token and len(target_underlyings) == 1:
            underlying_tokens[target_underlyings[0]] = underlying_token

        print("Starting ALL-option historical proxy backfill")
        print(f"Date range                 : {global_start} to {global_end}")
        print(f"Underlyings                : {target_underlyings}")
        print(f"Option type                : {option_type or 'CE + PE'}")
        print(f"Expiry from                : {expiry_from or 'not set'}")
        print(f"Expiry to                  : {expiry_to or 'not set'}")
        print(f"Strike min                 : {strike_min if strike_min is not None else 'not set'}")
        print(f"Strike max                 : {strike_max if strike_max is not None else 'not set'}")
        print(f"Max instruments/snapshot   : {max_instruments_per_snapshot or 'not set'}")
        print(f"Option batch size          : {settings.option_batch_size}")
        print(f"Batch sleep seconds        : {settings.batch_sleep_seconds}")
        print(f"Skip calc                  : {skip_calc}")
        print(f"Data source                : {DATA_SOURCE}")
        print("")

        for underlying in target_underlyings:
            token = underlying_tokens.get(underlying)

            if token is None:
                print(f"SKIP {underlying}: no underlying token")
                totals["skipped_no_underlying"] += 1
                continue

            for trade_dt in iter_dates(global_start, global_end):
                if trade_dt.weekday() >= 5:
                    continue

                for snapshot_label, (candle_start, snapshot_clock_time) in SNAPSHOT_WINDOWS.items():
                    totals["snapshots_attempted"] += 1

                    try:
                        underlying_price = get_underlying_snapshot_price(
                            kite_client=kite_client,
                            underlying_token=token,
                            trade_date=trade_dt,
                            candle_start=candle_start,
                            sleep_seconds=settings.sleep_seconds,
                        )

                        if underlying_price is None:
                            totals["skipped_no_underlying"] += 1
                            print(
                                f"SKIP {underlying} {trade_dt} {snapshot_label}: "
                                f"no underlying candle"
                            )
                            continue

                        instruments = load_all_option_instruments_for_snapshot(
                            db=db,
                            settings=settings,
                            underlying=underlying,
                            trade_date=trade_dt,
                            option_type=option_type,
                            expiry_from=expiry_from,
                            expiry_to=expiry_to,
                            strike_min=strike_min,
                            strike_max=strike_max,
                            max_instruments=max_instruments_per_snapshot,
                        )

                        if not instruments:
                            totals["skipped_no_instruments"] += 1
                            print(
                                f"SKIP {underlying} {trade_dt} {snapshot_label}: "
                                f"no option instruments"
                            )
                            continue

                        print(
                            f"START {underlying} {trade_dt} {snapshot_label} | "
                            f"spot={underlying_price:.2f} | instruments={len(instruments)}"
                        )

                        snapshot_time = datetime.combine(trade_dt, snapshot_clock_time)
                        snapshot_ids: list[int] = []
                        processed_in_snapshot = 0
                        instrument_batches = list(chunked(instruments, settings.option_batch_size))

                        for batch_no, instrument_batch in enumerate(instrument_batches, start=1):
                            batch_snapshot_ids: list[int] = []
                            batch_no_candle = 0
                            batch_failed = 0

                            for inst in instrument_batch:
                                processed_in_snapshot += 1
                                idx = processed_in_snapshot
                                try:
                                    totals["instruments_seen"] += 1

                                    candle = fetch_one_5m_candle(
                                        kite_client=kite_client,
                                        instrument_token=inst["instrument_token"],
                                        trade_date=trade_dt,
                                        candle_start=candle_start,
                                        include_oi=True,
                                    )

                                    time_module.sleep(settings.sleep_seconds)

                                    if not candle:
                                        totals["skipped_no_option_candle"] += 1
                                        batch_no_candle += 1

                                        if settings.debug_no_candle:
                                            print(
                                                "NO_CANDLE | "
                                                f"{underlying} | {trade_dt} | {snapshot_label} | "
                                                f"{inst['tradingsymbol']} | "
                                                f"token={inst['instrument_token']} | "
                                                f"expiry={inst['expiry']} | "
                                                f"strike={inst['strike']} | "
                                                f"type={inst['instrument_type']}"
                                            )

                                        continue

                                    row = build_snapshot_row_from_candle(
                                        instrument=inst,
                                        candle=candle,
                                        trade_date=trade_dt,
                                        snapshot_label=snapshot_label,
                                        snapshot_time=snapshot_time,
                                        underlying_price=underlying_price,
                                    )

                                    snapshot_id = upsert_option_snapshot(db, settings, row)

                                    if snapshot_id is not None:
                                        snapshot_ids.append(snapshot_id)
                                        batch_snapshot_ids.append(snapshot_id)

                                    totals["rows_upserted"] += 1

                                    if idx % settings.log_every == 0:
                                        print(
                                            f"  progress {idx}/{len(instruments)} | "
                                            f"batch={batch_no}/{len(instrument_batches)} | "
                                            f"upserted_total={totals['rows_upserted']} | "
                                            f"no_candle_total={totals['skipped_no_option_candle']}"
                                        )

                                except Exception as exc:
                                    totals["failed"] += 1
                                    batch_failed += 1
                                    print(
                                        "FAILED option | "
                                        f"{underlying} | {trade_dt} | {snapshot_label} | "
                                        f"{inst.get('tradingsymbol')} | "
                                        f"token={inst.get('instrument_token')} | {exc}"
                                    )

                            db.conn.commit()

                            if not skip_calc and batch_snapshot_ids:
                                calc_result = calculate_snapshot_ids_in_batches(
                                    db=db,
                                    snapshot_table=snapshot_table,
                                    instrument_table=instrument_table,
                                    snapshot_ids=batch_snapshot_ids,
                                    batch_size=settings.calc_batch_size,
                                )

                                totals["calc_rows_processed"] += calc_result["rows_processed"]
                                totals["calc_ok"] += calc_result["ok"]
                                totals["calc_non_ok"] += calc_result["non_ok"]
                                totals["calc_errors"] += calc_result["errors"]

                            if len(instrument_batches) > 1:
                                print(
                                    f"  batch {batch_no}/{len(instrument_batches)} done | "
                                    f"processed={processed_in_snapshot}/{len(instruments)} | "
                                    f"batch_rows={len(batch_snapshot_ids)} | "
                                    f"batch_no_candle={batch_no_candle} | "
                                    f"batch_failed={batch_failed} | "
                                    f"snapshot_rows={len(snapshot_ids)}"
                                )

                            if batch_no < len(instrument_batches) and settings.batch_sleep_seconds > 0:
                                time_module.sleep(settings.batch_sleep_seconds)

                        print(
                            f"DONE {underlying} {trade_dt} {snapshot_label} | "
                            f"spot={underlying_price:.2f} | "
                            f"instruments={len(instruments)} | "
                            f"snapshot_rows={len(snapshot_ids)} | "
                            f"upserted_total={totals['rows_upserted']} | "
                            f"no_candle_total={totals['skipped_no_option_candle']}"
                        )

                    except Exception as exc:
                        totals["failed"] += 1
                        print(
                            f"FAILED snapshot | {underlying} | {trade_dt} | "
                            f"{snapshot_label} | {exc}"
                        )

    finally:
        db.close()

    result = {
        "underlyings": underlyings or ["NIFTY"],
        "start_date": global_start.isoformat(),
        "end_date": global_end.isoformat(),
        "data_source": DATA_SOURCE,
        "mode": "ALL_OPTION_INSTRUMENTS_NO_CHAIN_SELECTION",
        **totals,
    }

    print("")
    print("Backfill completed")
    print(result)

    return result


def run_backfill_options_from_historical(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """
    Backward-compatible alias used by BackfillService and batch scripts.

    The implementation was renamed to run_backfill_all_options_from_historical
    when chain selection was removed. Keep this wrapper so existing runtime
    imports continue to work.
    """
    return run_backfill_all_options_from_historical(*args, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill OptionSnapshot with ALL historical 5m option candle proxy rows."
    )

    parser.add_argument("--from-date", "--start", dest="from_date", default=None, help="YYYY-MM-DD. Default: 2026-01-01 missing-coverage scan.")
    parser.add_argument("--to-date", "--end", dest="to_date", default=None, help="YYYY-MM-DD. Default: today.")

    parser.add_argument("--instrument-table", default="dbo.OptionInstrument")
    parser.add_argument("--snapshot-table", default="dbo.OptionSnapshot")

    parser.add_argument(
        "--underlying",
        action="append",
        default=None,
        help="Underlying symbol. Can be repeated. Default: NIFTY",
    )

    parser.add_argument(
        "--underlying-token",
        type=int,
        default=None,
        help="Optional underlying Kite token when one underlying is supplied",
    )

    parser.add_argument("--option-type", choices=["CE", "PE"], default=None)

    parser.add_argument("--expiry-from", default=None, help="Optional expiry lower bound YYYY-MM-DD")
    parser.add_argument("--expiry-to", default=None, help="Optional expiry upper bound YYYY-MM-DD")

    parser.add_argument("--strike-min", type=float, default=None)
    parser.add_argument("--strike-max", type=float, default=None)

    parser.add_argument(
        "--max-instruments-per-snapshot",
        type=int,
        default=None,
        help="Optional safety cap for testing. Do not use for full production backfill.",
    )

    parser.add_argument("--sleep-seconds", type=float, default=0.35)
    parser.add_argument(
        "--option-batch-size",
        type=int,
        default=10,
        help="Number of option instruments to process before committing/calculating and pausing. Default: 10.",
    )
    parser.add_argument(
        "--batch-sleep-seconds",
        type=float,
        default=2.0,
        help="Pause after each option batch. Default: 2.0 seconds.",
    )
    parser.add_argument("--calc-batch-size", type=int, default=500)
    parser.add_argument("--skip-calc", action="store_true")
    parser.add_argument("--debug-no-candle", action="store_true")
    parser.add_argument(
        "--range-fetch",
        action="store_true",
        help="Fetch each instrument's 5-minute history once for the full date range.",
    )

    args = parser.parse_args()

    runner = (
        run_backfill_all_options_from_historical_range
        if args.range_fetch
        else run_backfill_all_options_from_historical
    )

    runner(
        global_start=date.fromisoformat(args.from_date) if args.from_date else date(2026, 1, 1),
        global_end=date.fromisoformat(args.to_date) if args.to_date else date.today(),
        underlyings=[u.strip().upper() for u in args.underlying] if args.underlying else None,
        option_type=args.option_type,
        instrument_table=args.instrument_table,
        snapshot_table=args.snapshot_table,
        underlying_token=args.underlying_token,
        sleep_seconds=args.sleep_seconds,
        option_batch_size=args.option_batch_size,
        batch_sleep_seconds=args.batch_sleep_seconds,
        skip_calc=args.skip_calc,
        calc_batch_size=args.calc_batch_size,
        expiry_from=date.fromisoformat(args.expiry_from) if args.expiry_from else None,
        expiry_to=date.fromisoformat(args.expiry_to) if args.expiry_to else None,
        strike_min=args.strike_min,
        strike_max=args.strike_max,
        max_instruments_per_snapshot=args.max_instruments_per_snapshot,
        debug_no_candle=args.debug_no_candle,
    )


if __name__ == "__main__":
    main()
