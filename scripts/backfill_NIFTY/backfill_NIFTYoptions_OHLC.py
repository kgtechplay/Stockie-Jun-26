# scripts/backfill_NIFTY/backfill_NIFTYoptions_OHLC.py
"""
Backfill daily option OHLC rows for NIFTY option instruments into OptionOhlc.

This stores daily option candles separately from OptionSnapshot and
OptionSnapshotCalc. It uses Kite historical_data(interval="day") and writes one
row per option instrument, trade date, interval, and data source.

Usage:
    python scripts/backfill_NIFTY/backfill_NIFTYoptions_OHLC.py --from-date 2026-04-01 --to-date 2026-06-26
    python scripts/backfill_NIFTY/backfill_NIFTYoptions_OHLC.py --from-date 2026-06-01 --to-date 2026-06-26 --max-instruments 25
"""

from __future__ import annotations

import argparse
import re
import sys
import time as time_module
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.db.database_client import DatabaseClient
from src.data_manager.kite_client import KiteClient


load_dotenv(project_root / ".env")

DATA_SOURCE = "KITE_HISTORICAL_DAY_OHLC"
DEFAULT_UNDERLYING = "NIFTY"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class OptionOhlcSettings:
    option_instrument_table: str = "dbo.OptionInstrument"
    option_ohlc_table: str = "dbo.OptionOhlc"
    batch_size: int = 250
    sleep_seconds: float = 0.35
    log_every: int = 25


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
    return name if "." in name else f"dbo.{name}"


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def chunked(items: list[Any], chunk_size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def ensure_option_ohlc_schema(db: DatabaseClient, settings: OptionOhlcSettings) -> None:
    if getattr(db, "db_kind", "") == "postgres":
        table = pg_table_name(settings.option_ohlc_table)
        instrument_table = pg_table_name(settings.option_instrument_table)
        cursor = db.conn.cursor()
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id bigserial PRIMARY KEY,
                option_instrument_id bigint NOT NULL REFERENCES {instrument_table}(id),
                underlying varchar(50) NOT NULL,
                trade_date date NOT NULL,
                candle_time timestamp NOT NULL,
                candle_interval varchar(20) NOT NULL,
                open_price double precision,
                high_price double precision,
                low_price double precision,
                close_price double precision,
                volume bigint,
                open_interest bigint,
                last_price double precision,
                exchange_timestamp timestamp,
                data_source varchar(50) NOT NULL,
                loaded_at timestamp NOT NULL,
                CONSTRAINT uq_option_ohlc_instrument_date_interval_source
                    UNIQUE (option_instrument_id, trade_date, candle_interval, data_source)
            );
            CREATE INDEX IF NOT EXISTS ix_option_ohlc_underlying_date
                ON {table} (underlying, trade_date);
            CREATE INDEX IF NOT EXISTS ix_option_ohlc_date_source
                ON {table} (trade_date, data_source);
            """
        )
        db.conn.commit()
        cursor.close()
        return

    table = safe_table_name(settings.option_ohlc_table)
    full_name = table_object_name(settings.option_ohlc_table)
    cursor = db.conn.cursor()
    cursor.execute(
        f"""
        IF OBJECT_ID('{full_name}', 'U') IS NULL
        BEGIN
            CREATE TABLE {table} (
                id BIGINT IDENTITY(1,1) PRIMARY KEY,
                option_instrument_id BIGINT NOT NULL,
                underlying VARCHAR(50) NOT NULL,
                trade_date DATE NOT NULL,
                candle_time DATETIME2 NOT NULL,
                candle_interval VARCHAR(20) NOT NULL,
                open_price FLOAT NULL,
                high_price FLOAT NULL,
                low_price FLOAT NULL,
                close_price FLOAT NULL,
                volume BIGINT NULL,
                open_interest BIGINT NULL,
                last_price FLOAT NULL,
                exchange_timestamp DATETIME2 NULL,
                data_source VARCHAR(50) NOT NULL,
                loaded_at DATETIME2 NOT NULL
            );
            CREATE UNIQUE INDEX UX_OptionOhlc_Instrument_Date_Interval_Source
                ON {table}(option_instrument_id, trade_date, candle_interval, data_source);
            CREATE INDEX IX_OptionOhlc_Underlying_Date
                ON {table}(underlying, trade_date);
            CREATE INDEX IX_OptionOhlc_Date_Source
                ON {table}(trade_date, data_source);
        END;
        """
    )
    db.conn.commit()
    cursor.close()


def load_option_instruments(
    db: DatabaseClient,
    settings: OptionOhlcSettings,
    underlying: str,
    from_date: date,
    to_date: date,
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
    expiry_expr = "expiry" if is_postgres else "CAST(expiry AS date)"

    sql = f"""
        SELECT id, instrument_token, exchange, underlying, tradingsymbol,
               strike, {expiry_expr} AS expiry, instrument_type
        FROM {table}
        WHERE underlying = {placeholder}
          AND instrument_token IS NOT NULL
          AND instrument_type IN ('CE', 'PE')
          AND {expiry_expr} >= {placeholder}
    """
    params: list[Any] = [underlying.upper(), from_date]

    if expiry_from:
        sql += f" AND {expiry_expr} >= {placeholder}"
        params.append(expiry_from)
    if expiry_to:
        sql += f" AND {expiry_expr} <= {placeholder}"
        params.append(expiry_to)
    else:
        # Keep the default range bounded. Contracts expiring long after the
        # backfill window cannot have candles for most historical backfills.
        sql += f" AND {expiry_expr} <= {placeholder}"
        params.append(to_date)
    if option_type:
        sql += f" AND instrument_type = {placeholder}"
        params.append(option_type.upper())
    if strike_min is not None:
        sql += f" AND strike >= {placeholder}"
        params.append(strike_min)
    if strike_max is not None:
        sql += f" AND strike <= {placeholder}"
        params.append(strike_max)

    sql += f" ORDER BY {expiry_expr}, strike, instrument_type"

    cursor = db.conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()

    instruments: list[dict[str, Any]] = []
    for row in rows:
        if is_postgres:
            row_id, token, exchange, row_underlying, symbol, strike, expiry, row_type = row
        else:
            row_id = row.id
            token = row.instrument_token
            exchange = row.exchange
            row_underlying = row.underlying
            symbol = row.tradingsymbol
            strike = row.strike
            expiry = row.expiry
            row_type = row.instrument_type
        instruments.append({
            "id": int(row_id),
            "instrument_token": int(token),
            "exchange": exchange or "NFO",
            "underlying": row_underlying,
            "tradingsymbol": symbol,
            "strike": float(strike),
            "expiry": _to_date(expiry),
            "instrument_type": row_type,
        })
        if max_instruments is not None and len(instruments) >= max_instruments:
            break
    return instruments


def candle_to_ohlc_row(
    instrument: dict[str, Any],
    candle: dict[str, Any],
    loaded_at: datetime,
) -> dict[str, Any] | None:
    candle_time = _to_datetime(candle.get("date"))
    if candle_time is None:
        return None

    return {
        "option_instrument_id": instrument["id"],
        "underlying": instrument["underlying"],
        "trade_date": candle_time.date(),
        "candle_time": candle_time,
        "candle_interval": "day",
        "open_price": _to_float(candle.get("open")),
        "high_price": _to_float(candle.get("high")),
        "low_price": _to_float(candle.get("low")),
        "close_price": _to_float(candle.get("close")),
        "volume": _to_int(candle.get("volume")),
        "open_interest": _to_int(candle.get("oi")),
        "last_price": None,
        "exchange_timestamp": None,
        "data_source": DATA_SOURCE,
        "loaded_at": loaded_at,
    }


def fetch_historical_option_ohlc(
    kite_client: KiteClient,
    instrument: dict[str, Any],
    from_date: date,
    to_date: date,
    include_oi: bool = True,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    from_dt = datetime.combine(from_date, time(9, 15))
    to_dt = datetime.combine(to_date, time(15, 30))
    attempts = [include_oi, False] if include_oi else [False]
    last_error: Exception | None = None

    for oi_flag in attempts:
        for attempt in range(max_retries + 1):
            try:
                return kite_client.kite.historical_data(
                    instrument_token=instrument["instrument_token"],
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="day",
                    continuous=False,
                    oi=oi_flag,
                )
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "too many requests" in message and attempt < max_retries:
                    time_module.sleep(2.0 * (attempt + 1))
                    continue
                if oi_flag is True:
                    break
                raise
    if last_error:
        raise last_error
    return []


def upsert_option_ohlc_rows(
    db: DatabaseClient,
    settings: OptionOhlcSettings,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0

    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = pg_table_name(settings.option_ohlc_table) if is_postgres else safe_table_name(settings.option_ohlc_table)
    placeholder = "%s" if is_postgres else "?"
    columns = [
        "option_instrument_id", "underlying", "trade_date", "candle_time", "candle_interval",
        "open_price", "high_price", "low_price", "close_price", "volume", "open_interest",
        "last_price", "exchange_timestamp", "data_source", "loaded_at",
    ]
    values = [tuple(row[col] for col in columns) for row in rows]

    if is_postgres:
        sql = f"""
            INSERT INTO {table}
                ({', '.join(columns)})
            VALUES ({', '.join([placeholder] * len(columns))})
            ON CONFLICT (option_instrument_id, trade_date, candle_interval, data_source)
            DO UPDATE SET
                underlying = EXCLUDED.underlying,
                candle_time = EXCLUDED.candle_time,
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume,
                open_interest = EXCLUDED.open_interest,
                last_price = EXCLUDED.last_price,
                exchange_timestamp = EXCLUDED.exchange_timestamp,
                loaded_at = EXCLUDED.loaded_at
        """
        cursor = db.conn.cursor()
        cursor.executemany(sql, values)
        db.conn.commit()
        cursor.close()
        return len(rows)

    cursor = db.conn.cursor()
    cursor.fast_executemany = True
    update_sql = f"""
        UPDATE {table}
        SET underlying = ?, candle_time = ?, open_price = ?, high_price = ?,
            low_price = ?, close_price = ?, volume = ?, open_interest = ?,
            last_price = ?, exchange_timestamp = ?, loaded_at = ?
        WHERE option_instrument_id = ?
          AND trade_date = ?
          AND candle_interval = ?
          AND data_source = ?;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO {table}
                ({', '.join(columns)})
            VALUES ({', '.join(['?'] * len(columns))});
        END
    """
    params = []
    for row in rows:
        update_values = [
            row["underlying"], row["candle_time"], row["open_price"], row["high_price"],
            row["low_price"], row["close_price"], row["volume"], row["open_interest"],
            row["last_price"], row["exchange_timestamp"], row["loaded_at"],
            row["option_instrument_id"], row["trade_date"], row["candle_interval"], row["data_source"],
        ]
        insert_values = [row[col] for col in columns]
        params.append(tuple(update_values + insert_values))
    cursor.executemany(update_sql, params)
    db.conn.commit()
    cursor.close()
    return len(rows)


def run_backfill_option_ohlc(
    from_date: date,
    to_date: date,
    underlying: str = DEFAULT_UNDERLYING,
    option_type: str | None = None,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_instruments: int | None = None,
    dry_run: bool = False,
    settings: OptionOhlcSettings | None = None,
) -> dict[str, int]:
    if from_date > to_date:
        raise ValueError("from_date must be <= to_date")

    settings = settings or OptionOhlcSettings()
    app_settings = get_settings()
    db = get_database_client(app_settings)
    db.connect()
    try:
        ensure_option_ohlc_schema(db, settings)
        instruments = load_option_instruments(
            db=db,
            settings=settings,
            underlying=underlying,
            from_date=from_date,
            to_date=to_date,
            option_type=option_type,
            expiry_from=expiry_from,
            expiry_to=expiry_to,
            strike_min=strike_min,
            strike_max=strike_max,
            max_instruments=max_instruments,
        )
    finally:
        db.close()

    print(f"Loaded {len(instruments):,} {underlying.upper()} option instruments for OHLC backfill.")
    if dry_run:
        return {"instruments": len(instruments), "rows_prepared": 0, "rows_upserted": 0, "errors": 0}

    kite_client = KiteClient(app_settings)
    kite_client.authenticate()

    rows_buffer: list[dict[str, Any]] = []
    rows_prepared = rows_upserted = errors = no_candles = 0
    loaded_at = datetime.now(UTC).replace(tzinfo=None)

    db = get_database_client(app_settings)
    db.connect()
    try:
        ensure_option_ohlc_schema(db, settings)
        for index, instrument in enumerate(instruments, 1):
            try:
                candles = fetch_historical_option_ohlc(kite_client, instrument, from_date, to_date)
            except Exception as exc:
                errors += 1
                print(f"[WARN] {instrument['tradingsymbol']} historical OHLC failed: {exc}")
                continue

            if not candles:
                no_candles += 1
            for candle in candles:
                row = candle_to_ohlc_row(instrument, candle, loaded_at)
                if row is None or row["trade_date"] < from_date or row["trade_date"] > to_date:
                    continue
                rows_buffer.append(row)
                rows_prepared += 1

            if len(rows_buffer) >= settings.batch_size:
                rows_upserted += upsert_option_ohlc_rows(db, settings, rows_buffer)
                rows_buffer.clear()

            if settings.sleep_seconds > 0:
                time_module.sleep(settings.sleep_seconds)
            if settings.log_every and index % settings.log_every == 0:
                print(
                    f"Processed {index:,}/{len(instruments):,} instruments | "
                    f"rows_prepared={rows_prepared:,}, rows_upserted={rows_upserted:,}, "
                    f"no_candles={no_candles:,}, errors={errors:,}"
                )

        if rows_buffer:
            rows_upserted += upsert_option_ohlc_rows(db, settings, rows_buffer)
    finally:
        db.close()

    result = {
        "instruments": len(instruments),
        "rows_prepared": rows_prepared,
        "rows_upserted": rows_upserted,
        "no_candles": no_candles,
        "errors": errors,
    }
    print(f"Option OHLC backfill completed: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill NIFTY option daily OHLC into OptionOhlc.")
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--underlying", default=DEFAULT_UNDERLYING)
    parser.add_argument("--option-type", choices=["CE", "PE"])
    parser.add_argument("--expiry-from", help="YYYY-MM-DD")
    parser.add_argument("--expiry-to", help="YYYY-MM-DD")
    parser.add_argument("--strike-min", type=float)
    parser.add_argument("--strike-max", type=float)
    parser.add_argument("--max-instruments", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--sleep-seconds", type=float, default=0.35)
    parser.add_argument("--log-every", type=int, default=25)
    args = parser.parse_args()

    run_backfill_option_ohlc(
        from_date=date.fromisoformat(args.from_date),
        to_date=date.fromisoformat(args.to_date),
        underlying=args.underlying.strip().upper(),
        option_type=args.option_type,
        expiry_from=date.fromisoformat(args.expiry_from) if args.expiry_from else None,
        expiry_to=date.fromisoformat(args.expiry_to) if args.expiry_to else None,
        strike_min=args.strike_min,
        strike_max=args.strike_max,
        max_instruments=args.max_instruments,
        dry_run=args.dry_run,
        settings=OptionOhlcSettings(
            batch_size=args.batch_size,
            sleep_seconds=args.sleep_seconds,
            log_every=args.log_every,
        ),
    )


if __name__ == "__main__":
    main()