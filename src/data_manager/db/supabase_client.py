from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Any

from src.common.config import Settings
from src.common.models import OptionInstrument, WatchedInstrument


class SupabaseDatabaseClient:
    db_kind = "postgres"

    def __init__(self, settings: Settings) -> None:
        self._conn_str = settings.supabase_conn_str
        self._conn = None
        if not self._conn_str:
            raise RuntimeError("SUPABASE_CONN_STR is missing in .env")

    def connect(self) -> None:
        if self._conn is None:
            import psycopg2

            self._conn = psycopg2.connect(self._conn_str)
            self._conn.autocommit = False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            raise RuntimeError("DB not connected. Call connect() first.")
        return self._conn

    def create_core_tables(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_CORE_SCHEMA_SQL)
        self.conn.commit()

    def upsert_watched_instruments(self, instruments: Iterable[WatchedInstrument]) -> int:
        rows = list(instruments)
        if not rows:
            return 0
        values = [
            (
                r.tradingsymbol,
                r.exchange,
                r.name,
                r.instrument_token,
                r.segment,
                r.tick_size,
                r.lot_size,
                r.instrument_type,
                r.sector,
                r.industry,
                r.is_fo_enabled,
                r.is_active,
            )
            for r in rows
        ]
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO "WatchedInstrument"
                    (tradingsymbol, exchange, name, instrument_token, segment,
                     tick_size, lot_size, instrument_type, sector, industry,
                     is_fo_enabled, is_active)
                VALUES %s
                ON CONFLICT (tradingsymbol, exchange) DO UPDATE SET
                    name = EXCLUDED.name,
                    instrument_token = EXCLUDED.instrument_token,
                    segment = EXCLUDED.segment,
                    tick_size = EXCLUDED.tick_size,
                    lot_size = EXCLUDED.lot_size,
                    instrument_type = EXCLUDED.instrument_type,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry,
                    is_fo_enabled = EXCLUDED.is_fo_enabled,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def get_watched_instruments(
        self,
        instrument_type: str | None = None,
    ) -> list[WatchedInstrument]:
        sql = """
            SELECT watched_id, tradingsymbol, exchange, name, instrument_token,
                   segment, tick_size, lot_size, instrument_type, sector, industry,
                   is_fo_enabled, is_active
            FROM "WatchedInstrument"
            WHERE is_active = true
        """
        params: list[Any] = []
        if instrument_type:
            sql += " AND instrument_type = %s"
            params.append(instrument_type)
        sql += " ORDER BY tradingsymbol"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            WatchedInstrument(
                watched_id=r[0],
                tradingsymbol=r[1],
                exchange=r[2],
                name=r[3],
                instrument_token=int(r[4]) if r[4] is not None else None,
                segment=r[5],
                tick_size=float(r[6]) if r[6] is not None else None,
                lot_size=int(r[7]) if r[7] is not None else None,
                instrument_type=r[8],
                sector=r[9],
                industry=r[10],
                is_fo_enabled=bool(r[11]),
                is_active=bool(r[12]),
            )
            for r in rows
        ]

    def upsert_option_instruments(self, options: Iterable[OptionInstrument]) -> None:
        rows = list(options)
        if not rows:
            return
        values = [
            (
                o.fetch_date,
                o.instrument_token,
                o.underlying,
                o.exchange,
                o.tradingsymbol,
                o.name,
                o.strike,
                o.expiry,
                o.instrument_type,
                o.lot_size,
                o.tick_size,
                o.segment,
            )
            for o in rows
        ]
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO "OptionInstrument"
                    (fetch_date, instrument_token, underlying, exchange, tradingsymbol,
                     name, strike, expiry, instrument_type, lot_size, tick_size, segment)
                VALUES %s
                ON CONFLICT (instrument_token) DO UPDATE SET
                    fetch_date = EXCLUDED.fetch_date,
                    underlying = EXCLUDED.underlying,
                    exchange = EXCLUDED.exchange,
                    tradingsymbol = EXCLUDED.tradingsymbol,
                    name = EXCLUDED.name,
                    strike = EXCLUDED.strike,
                    expiry = EXCLUDED.expiry,
                    instrument_type = EXCLUDED.instrument_type,
                    lot_size = EXCLUDED.lot_size,
                    tick_size = EXCLUDED.tick_size,
                    segment = EXCLUDED.segment
                """,
                values,
            )
        self.conn.commit()

    def upsert_underlying_snapshots(
        self,
        rows: list[tuple[str, date, datetime, float | None, float | None, float | None, float | None, int | None]],
    ) -> dict[str, int]:
        if not rows:
            return {"prepared": 0, "upserted": 0}
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO "UnderlyingSnapshot"
                    (underlying, trade_date, loaded_at, open_price, high_price,
                     low_price, close_price, volume)
                VALUES %s
                ON CONFLICT (underlying, trade_date) DO UPDATE SET
                    loaded_at = EXCLUDED.loaded_at,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume
                """,
                rows,
            )
        self.conn.commit()
        return {"prepared": len(rows), "upserted": len(rows)}

    def upsert_underlying_candles_5m(
        self,
        rows: list[tuple[str, date, datetime, float, float, float, float, "int | None"]],
    ) -> dict[str, int]:
        if not rows:
            return {"prepared": 0, "updated": 0, "inserted": 0, "skipped_duplicates": 0}
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "UnderlyingCandle5m" (
                    underlying varchar(50) NOT NULL,
                    trade_date date NOT NULL,
                    candle_time timestamp NOT NULL,
                    open_price double precision NOT NULL,
                    high_price double precision NOT NULL,
                    low_price double precision NOT NULL,
                    close_price double precision NOT NULL,
                    volume bigint,
                    CONSTRAINT pk_underlying_candle_5m PRIMARY KEY (underlying, candle_time)
                )
            """)
            execute_values(
                cur,
                """
                INSERT INTO "UnderlyingCandle5m"
                    (underlying, trade_date, candle_time, open_price, high_price,
                     low_price, close_price, volume)
                VALUES %s
                ON CONFLICT (underlying, candle_time) DO UPDATE SET
                    trade_date = EXCLUDED.trade_date,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume
                """,
                rows,
            )
        self.conn.commit()
        return {"prepared": len(rows), "updated": 0, "inserted": len(rows), "skipped_duplicates": 0}

    # ---------- TRADING CALENDAR ----------

    def upsert_trading_calendar(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "TradingCalendar" (
                    calendar_date date NOT NULL,
                    exchange varchar(10) NOT NULL,
                    is_trading_day boolean NOT NULL DEFAULT false,
                    is_weekly_expiry boolean NOT NULL DEFAULT false,
                    is_monthly_expiry boolean NOT NULL DEFAULT false,
                    is_special_session boolean NOT NULL DEFAULT false,
                    notes text,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    CONSTRAINT pk_trading_calendar PRIMARY KEY (calendar_date, exchange)
                )
            """)
            execute_values(
                cur,
                """
                INSERT INTO "TradingCalendar"
                    (calendar_date, exchange, is_trading_day, is_weekly_expiry,
                     is_monthly_expiry, is_special_session, notes)
                VALUES %s
                ON CONFLICT (calendar_date, exchange) DO UPDATE SET
                    is_trading_day     = EXCLUDED.is_trading_day,
                    is_weekly_expiry   = EXCLUDED.is_weekly_expiry,
                    is_monthly_expiry  = EXCLUDED.is_monthly_expiry,
                    is_special_session = EXCLUDED.is_special_session,
                    notes              = EXCLUDED.notes,
                    updated_at         = now()
                """,
                [
                    (
                        r["calendar_date"], r["exchange"],
                        r["is_trading_day"], r["is_weekly_expiry"],
                        r["is_monthly_expiry"], r["is_special_session"],
                        r.get("notes"),
                    )
                    for r in rows
                ],
            )
        self.conn.commit()
        return len(rows)

    # ---------- KITE ACCESS TOKEN ----------

    def save_kite_access_token(self, access_token: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "KiteAccessToken" (
                    id SERIAL PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("SELECT COUNT(*) FROM \"KiteAccessToken\"")
            count = cur.fetchone()[0]
            if count == 0:
                cur.execute(
                    "INSERT INTO \"KiteAccessToken\" (access_token) VALUES (%s)",
                    (access_token,),
                )
            else:
                cur.execute(
                    "UPDATE \"KiteAccessToken\" SET access_token = %s, updated_at = now() "
                    "WHERE id = (SELECT id FROM \"KiteAccessToken\" ORDER BY updated_at DESC LIMIT 1)",
                    (access_token,),
                )
        self.conn.commit()

    def get_kite_access_token(self) -> str | None:
        with self.conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT access_token FROM \"KiteAccessToken\" ORDER BY updated_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0]).strip()
            except Exception:
                self.conn.rollback()
        return None


SUPABASE_CORE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS "WatchedInstrument" (
    watched_id bigserial PRIMARY KEY,
    tradingsymbol varchar(50) NOT NULL,
    exchange varchar(20) NOT NULL,
    name varchar(200),
    instrument_token bigint,
    segment varchar(50),
    tick_size double precision,
    lot_size integer,
    instrument_type varchar(30) NOT NULL,
    sector varchar(100),
    industry varchar(100),
    is_fo_enabled boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    CONSTRAINT uq_watched_instrument UNIQUE (tradingsymbol, exchange)
);

CREATE INDEX IF NOT EXISTS ix_watched_active
    ON "WatchedInstrument" (is_active, instrument_type, tradingsymbol);
CREATE INDEX IF NOT EXISTS ix_watched_token
    ON "WatchedInstrument" (instrument_token);

CREATE TABLE IF NOT EXISTS "UnderlyingSnapshot" (
    underlying varchar(50) NOT NULL,
    trade_date date NOT NULL,
    loaded_at timestamp NOT NULL,
    open_price double precision,
    high_price double precision,
    low_price double precision,
    close_price double precision,
    volume bigint,
    CONSTRAINT pk_underlying_snapshot PRIMARY KEY (underlying, trade_date)
);

CREATE TABLE IF NOT EXISTS "OptionInstrument" (
    id bigserial PRIMARY KEY,
    fetch_date date NOT NULL,
    instrument_token bigint NOT NULL,
    underlying varchar(50) NOT NULL,
    exchange varchar(20) NOT NULL,
    tradingsymbol varchar(100) NOT NULL,
    name varchar(100),
    strike double precision NOT NULL,
    expiry date NOT NULL,
    instrument_type varchar(10) NOT NULL,
    lot_size integer,
    tick_size double precision,
    segment varchar(50),
    CONSTRAINT uq_option_instrument_token UNIQUE (instrument_token)
);

CREATE INDEX IF NOT EXISTS ix_option_instrument_underlying
    ON "OptionInstrument" (underlying, expiry, strike, instrument_type);

CREATE TABLE IF NOT EXISTS "OptionSnapshot" (
    id bigserial PRIMARY KEY,
    option_instrument_id bigint NOT NULL REFERENCES "OptionInstrument"(id),
    snapshot_time timestamp NOT NULL,
    underlying_price double precision,
    last_price double precision,
    bid_price double precision,
    bid_qty integer,
    ask_price double precision,
    ask_qty integer,
    volume bigint,
    open_interest bigint,
    trade_date date NOT NULL,
    snapshot_label varchar(20) NOT NULL,
    exchange_timestamp timestamp,
    last_trade_time timestamp,
    last_quantity integer,
    average_price double precision,
    buy_quantity bigint,
    sell_quantity bigint,
    oi_day_high bigint,
    oi_day_low bigint,
    bid_orders integer,
    ask_orders integer,
    data_source varchar(50),
    CONSTRAINT uq_option_snapshot_instrument_date_label
        UNIQUE (option_instrument_id, trade_date, snapshot_label)
);

CREATE INDEX IF NOT EXISTS ix_option_snapshot_date_label
    ON "OptionSnapshot" (trade_date, snapshot_label);

CREATE TABLE IF NOT EXISTS "OptionSnapshotCalc" (
    option_snapshot_id bigint PRIMARY KEY REFERENCES "OptionSnapshot"(id) ON DELETE CASCADE,
    implied_volatility double precision,
    delta double precision,
    gamma double precision,
    theta double precision,
    vega double precision,
    valuation_price double precision,
    intrinsic_value double precision,
    time_value double precision,
    mid_price double precision,
    spread_width double precision,
    spread_width_pct double precision,
    days_to_expiry double precision,
    risk_free_rate double precision,
    calculation_status varchar(30),
    calculation_error varchar(500),
    created_at timestamp NOT NULL DEFAULT now()
);
"""
