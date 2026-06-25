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

    def bulk_insert_option_snapshots(
        self,
        rows: list[dict],
        batch_size: int = 500,
    ) -> int:
        """
        Upsert option snapshot rows into Supabase "OptionSnapshot".

        Each dict must have: option_instrument_id, snapshot_time, trade_date,
        snapshot_label. All other fields are optional.
        Upsert key: (option_instrument_id, trade_date, snapshot_label).
        """
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        with self.conn.cursor() as cur:
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start : batch_start + batch_size]
                values = [
                    (
                        r["option_instrument_id"],
                        r["snapshot_time"],
                        r.get("underlying_price"),
                        r.get("last_price"),
                        r.get("bid_price"),
                        r.get("bid_qty"),
                        r.get("ask_price"),
                        r.get("ask_qty"),
                        r.get("volume"),
                        r.get("open_interest"),
                        r["trade_date"],
                        r["snapshot_label"],
                        r.get("exchange_timestamp"),
                        r.get("last_trade_time"),
                        r.get("last_quantity"),
                        r.get("average_price"),
                        r.get("buy_quantity"),
                        r.get("sell_quantity"),
                        r.get("oi_day_high"),
                        r.get("oi_day_low"),
                        r.get("bid_orders"),
                        r.get("ask_orders"),
                        r.get("data_source"),
                    )
                    for r in batch
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO "OptionSnapshot" (
                        option_instrument_id, snapshot_time,
                        underlying_price, last_price,
                        bid_price, bid_qty, ask_price, ask_qty,
                        volume, open_interest,
                        trade_date, snapshot_label,
                        exchange_timestamp, last_trade_time, last_quantity,
                        average_price, buy_quantity, sell_quantity,
                        oi_day_high, oi_day_low, bid_orders, ask_orders,
                        data_source
                    )
                    VALUES %s
                    ON CONFLICT (option_instrument_id, trade_date, snapshot_label)
                    DO UPDATE SET
                        snapshot_time    = EXCLUDED.snapshot_time,
                        underlying_price = EXCLUDED.underlying_price,
                        last_price       = EXCLUDED.last_price,
                        bid_price        = EXCLUDED.bid_price,
                        bid_qty          = EXCLUDED.bid_qty,
                        ask_price        = EXCLUDED.ask_price,
                        ask_qty          = EXCLUDED.ask_qty,
                        volume           = EXCLUDED.volume,
                        open_interest    = EXCLUDED.open_interest,
                        exchange_timestamp = EXCLUDED.exchange_timestamp,
                        last_trade_time  = EXCLUDED.last_trade_time,
                        last_quantity    = EXCLUDED.last_quantity,
                        average_price    = EXCLUDED.average_price,
                        buy_quantity     = EXCLUDED.buy_quantity,
                        sell_quantity    = EXCLUDED.sell_quantity,
                        oi_day_high      = EXCLUDED.oi_day_high,
                        oi_day_low       = EXCLUDED.oi_day_low,
                        bid_orders       = EXCLUDED.bid_orders,
                        ask_orders       = EXCLUDED.ask_orders,
                        data_source      = EXCLUDED.data_source
                    """,
                    values,
                )
        self.conn.commit()
        return len(rows)

    def upsert_signal_features(self, rows: list[dict]) -> int:
        """
        Persist feature rows to "SignalFeatureDaily".

        Each dict must have: signal_date, symbol. All feature columns are optional.
        Upsert key: (signal_date, symbol, feature_version).
        """
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "signal_date", "symbol", "feature_version",
            "close_1515", "open_915", "high_day", "low_day", "volume_day",
            "ma10", "ma20", "ma50", "ma90",
            "rsi14", "rsi5", "atr14",
            "bb_upper", "bb_middle", "bb_lower", "bb_width",
            "ret_2d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d",
            "volatility_10d", "volatility_20d", "volume_10d", "volume_20d",
            "trend_efficiency_5d", "trend_efficiency_10d",
            "trend_efficiency_20d", "trend_efficiency_60d",
            "relative_strength_vs_sector",
            "ma5d_slope", "ma10d_slope", "ma20_slope", "ma50_slope",
            "recent_high_5d", "recent_low_5d",
            "recent_high_10d", "recent_low_10d",
            "recent_high_20d", "recent_low_20d",
            "range_position_5d", "range_position_10d", "range_position_20d",
            "regime",
        ]
        update_cols = [c for c in cols if c not in ("signal_date", "symbol", "feature_version")]
        set_clause = ",\n                        ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

        with self.conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE "SignalFeatureDaily"
                    ADD COLUMN IF NOT EXISTS ret_10d double precision,
                    ADD COLUMN IF NOT EXISTS rsi5 double precision,
                    ADD COLUMN IF NOT EXISTS ret_2d double precision,
                    ADD COLUMN IF NOT EXISTS ret_3d double precision,
                    ADD COLUMN IF NOT EXISTS volatility_10d double precision,
                    ADD COLUMN IF NOT EXISTS volume_10d double precision,
                    ADD COLUMN IF NOT EXISTS volume_20d double precision,
                    ADD COLUMN IF NOT EXISTS trend_efficiency_5d double precision,
                    ADD COLUMN IF NOT EXISTS trend_efficiency_10d double precision,
                    ADD COLUMN IF NOT EXISTS trend_efficiency_20d double precision,
                    ADD COLUMN IF NOT EXISTS ma5d_slope double precision,
                    ADD COLUMN IF NOT EXISTS ma10d_slope double precision,
                    ADD COLUMN IF NOT EXISTS recent_high_5d double precision,
                    ADD COLUMN IF NOT EXISTS recent_low_5d double precision,
                    ADD COLUMN IF NOT EXISTS recent_high_10d double precision,
                    ADD COLUMN IF NOT EXISTS recent_low_10d double precision,
                    ADD COLUMN IF NOT EXISTS range_position_5d double precision,
                    ADD COLUMN IF NOT EXISTS range_position_10d double precision,
                    DROP COLUMN IF EXISTS volume_ratio,
                    DROP COLUMN IF EXISTS ma20_50_crossovers_20d
            """)
            values = [tuple(r.get(c, "v1" if c == "feature_version" else None) for c in cols) for r in rows]
            execute_values(
                cur,
                f"""
                INSERT INTO "SignalFeatureDaily" ({", ".join(cols)}, updated_at)
                VALUES %s
                ON CONFLICT ON CONSTRAINT uq_signal_feature_daily DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                [v + (None,) for v in values],
            )
        self.conn.commit()
        return len(rows)

    def upsert_nifty_predictions(self, rows: list[dict]) -> int:
        """
        Persist daily final-prediction rows to "NiftyPrediction".

        Each dict mirrors the production prediction CSV. Required: trade_date.
        Upsert key: (symbol, trade_date, model_version). The table is created on
        first use, so a missing migration does not break the daily job.
        """
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "symbol", "trade_date", "model_version", "next_trade_date",
            "open_915", "high_day", "low_day", "close_1515", "volume_day",
            "vix_close", "vix_chg_1d", "vix_chg_pct", "regime",
            "next_open", "next_high", "next_low", "next_close", "next_return_pct",
            "final_prediction", "direction", "volatility_regime", "primary_strategy",
            "strategy_precision", "signal_style", "strength_score", "strength_label",
            "confidence_level", "actual_trade_label",
        ]
        key_cols = ("symbol", "trade_date", "model_version")
        update_cols = [c for c in cols if c not in key_cols]
        set_clause = ",\n                        ".join(
            f"{c} = EXCLUDED.{c}" for c in update_cols
        )

        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "NiftyPrediction" (
                    symbol             varchar(50)  NOT NULL DEFAULT 'NIFTY',
                    trade_date         date         NOT NULL,
                    model_version      varchar(50)  NOT NULL DEFAULT 'cascade_v1',
                    next_trade_date    date,
                    open_915           double precision,
                    high_day           double precision,
                    low_day            double precision,
                    close_1515         double precision,
                    volume_day         double precision,
                    vix_close          double precision,
                    vix_chg_1d         double precision,
                    vix_chg_pct        double precision,
                    regime             varchar(20),
                    next_open          double precision,
                    next_high          double precision,
                    next_low           double precision,
                    next_close         double precision,
                    next_return_pct    double precision,
                    final_prediction   varchar(20),
                    direction          varchar(20),
                    volatility_regime  varchar(20),
                    primary_strategy   varchar(120),
                    strategy_precision double precision,
                    signal_style       varchar(50),
                    strength_score     double precision,
                    strength_label     varchar(20),
                    confidence_level   double precision,
                    actual_trade_label varchar(20),
                    created_at         timestamptz NOT NULL DEFAULT now(),
                    updated_at         timestamptz NOT NULL DEFAULT now(),
                    CONSTRAINT pk_nifty_prediction PRIMARY KEY (symbol, trade_date, model_version)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_nifty_prediction_date
                    ON "NiftyPrediction" (trade_date);
            """)
            for ddl in (
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS direction varchar(20)',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS volatility_regime varchar(20)',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS primary_strategy varchar(120)',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS strategy_precision double precision',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS signal_style varchar(50)',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS strength_score double precision',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS strength_label varchar(20)',
                'ALTER TABLE "NiftyPrediction" ADD COLUMN IF NOT EXISTS confidence_level double precision',
            ):
                cur.execute(ddl)
            values = [
                tuple(
                    r.get(c, "NIFTY" if c == "symbol"
                          else "cascade_v1" if c == "model_version" else None)
                    for c in cols
                )
                for r in rows
            ]
            execute_values(
                cur,
                f"""
                INSERT INTO "NiftyPrediction" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT ON CONSTRAINT pk_nifty_prediction DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def upsert_nifty_option_selections(self, rows: list[dict]) -> int:
        """Persist daily option-selection rows to "NiftyOptionSelection"."""
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "symbol", "trade_date", "model_version", "next_trade_date",
            "final_prediction", "prediction_direction", "volatility_regime",
            "primary_strategy", "strategy_precision", "signal_style",
            "strength_score", "strength_label", "confidence_level",
            "spot_price", "as_of_time", "selected_strategy", "option_bias_selected",
            "no_trade_reason", "evaluated_candidate_count", "strategy_direction",
            "entry_debit_or_credit", "max_profit", "max_loss", "breakeven",
            "reward_risk", "selection_score", "selection_confidence",
            "total_delta", "total_gamma", "total_theta", "total_vega",
            "legs_summary", "primary_buy_token", "primary_buy_symbol",
            "primary_buy_strike", "primary_buy_expiry", "primary_buy_option_type",
            "primary_buy_entry_price", "primary_buy_iv", "primary_buy_delta",
            "target_1_pct", "target_1_price", "target_2_pct", "target_2_price",
            "stop_loss_enabled", "stop_loss_pct", "stop_loss_price",
        ]
        key_cols = ("symbol", "trade_date", "model_version")
        update_cols = [c for c in cols if c not in key_cols]
        set_clause = ",\n                        ".join(
            f"{c} = EXCLUDED.{c}" for c in update_cols
        )

        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "NiftyOptionSelection" (
                    symbol                    varchar(50) NOT NULL DEFAULT 'NIFTY',
                    trade_date                date        NOT NULL,
                    model_version             varchar(50) NOT NULL DEFAULT 'cascade_v1',
                    next_trade_date           date,
                    final_prediction          varchar(20),
                    prediction_direction      varchar(20),
                    volatility_regime         varchar(20),
                    primary_strategy          varchar(120),
                    strategy_precision        double precision,
                    signal_style              varchar(50),
                    strength_score            double precision,
                    strength_label            varchar(20),
                    confidence_level          double precision,
                    spot_price                double precision,
                    as_of_time                timestamp,
                    selected_strategy         varchar(50),
                    option_bias_selected      varchar(50),
                    no_trade_reason           text,
                    evaluated_candidate_count integer,
                    strategy_direction        varchar(20),
                    entry_debit_or_credit     double precision,
                    max_profit                double precision,
                    max_loss                  double precision,
                    breakeven                 double precision,
                    reward_risk               double precision,
                    selection_score           double precision,
                    selection_confidence      varchar(20),
                    total_delta               double precision,
                    total_gamma               double precision,
                    total_theta               double precision,
                    total_vega                double precision,
                    legs_summary              text,
                    primary_buy_token         bigint,
                    primary_buy_symbol        varchar(120),
                    primary_buy_strike        double precision,
                    primary_buy_expiry        date,
                    primary_buy_option_type   varchar(10),
                    primary_buy_entry_price   double precision,
                    primary_buy_iv            double precision,
                    primary_buy_delta         double precision,
                    target_1_pct              double precision,
                    target_1_price            double precision,
                    target_2_pct              double precision,
                    target_2_price            double precision,
                    stop_loss_enabled         boolean NOT NULL DEFAULT false,
                    stop_loss_pct             double precision,
                    stop_loss_price           double precision,
                    created_at                timestamptz NOT NULL DEFAULT now(),
                    updated_at                timestamptz NOT NULL DEFAULT now(),
                    CONSTRAINT pk_nifty_option_selection PRIMARY KEY (symbol, trade_date, model_version)
                );
            """)
            for ddl in (
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS target_1_pct double precision',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS target_1_price double precision',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS target_2_pct double precision',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS target_2_price double precision',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS stop_loss_enabled boolean NOT NULL DEFAULT false',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS stop_loss_pct double precision',
                'ALTER TABLE "NiftyOptionSelection" ADD COLUMN IF NOT EXISTS stop_loss_price double precision',
            ):
                cur.execute(ddl)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_nifty_option_selection_date
                    ON "NiftyOptionSelection" (trade_date);
            """)
            values = [
                tuple(
                    r.get(c, "NIFTY" if c == "symbol"
                          else "cascade_v1" if c == "model_version" else None)
                    for c in cols
                )
                for r in rows
            ]
            execute_values(
                cur,
                f"""
                INSERT INTO "NiftyOptionSelection" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT ON CONSTRAINT pk_nifty_option_selection DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def upsert_news_articles(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "article_id", "source", "url", "title", "summary", "published_at",
            "fetched_at", "region", "provider",
        ]
        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_NEWS_SENTIMENT_SCHEMA_SQL)
            values = [tuple(r.get(c) for c in cols) for r in rows]
            execute_values(
                cur,
                f"""
                INSERT INTO "NewsArticle" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT (article_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    url = EXCLUDED.url,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    published_at = EXCLUDED.published_at,
                    fetched_at = EXCLUDED.fetched_at,
                    region = EXCLUDED.region,
                    provider = EXCLUDED.provider,
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def upsert_news_article_sentiments(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "target_date", "article_id", "window_start", "window_end",
            "sentiment_label", "sentiment_score", "sentiment_confidence",
            "sentiment_model", "sectors", "sector_confidences", "sector_weight",
            "weighted_sentiment",
        ]
        update_cols = [c for c in cols if c not in ("target_date", "article_id")]
        set_clause = ",\n                    ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_NEWS_SENTIMENT_SCHEMA_SQL)
            values = [tuple(r.get(c) for c in cols) for r in rows]
            execute_values(
                cur,
                f"""
                INSERT INTO "NewsArticleSentiment" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT (target_date, article_id) DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def upsert_nifty_market_sentiments(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "target_date", "window_start", "window_end", "article_count",
            "usable_article_count", "composite_score", "composite_label",
            "mean_confidence", "positive_count", "neutral_count", "negative_count",
            "weighted_signal_sum", "normalization_denominator", "source_mix",
            "generated_at",
        ]
        update_cols = [c for c in cols if c != "target_date"]
        set_clause = ",\n                    ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_NEWS_SENTIMENT_SCHEMA_SQL)
            values = [tuple(r.get(c) for c in cols) for r in rows]
            execute_values(
                cur,
                f"""
                INSERT INTO "NiftyMarketSentiment" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT (target_date) DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def upsert_global_index_ohlc(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = [
            "index_code", "index_name", "yahoo_symbol", "region", "currency",
            "trade_date", "open_price", "high_price", "low_price", "close_price",
            "adj_close", "volume", "source", "fetched_at",
        ]
        key_cols = ("index_code", "trade_date", "source")
        update_cols = [c for c in cols if c not in key_cols]
        set_clause = ",\n                    ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_GLOBAL_INDEX_SCHEMA_SQL)
            values = [tuple(r.get(c) for c in cols) for r in rows]
            execute_values(
                cur,
                f"""
                INSERT INTO "GlobalIndexOhlc" ({", ".join(cols)})
                VALUES %s
                ON CONFLICT ON CONSTRAINT pk_global_index_ohlc DO UPDATE SET
                    {set_clause},
                    updated_at = now()
                """,
                values,
            )
        self.conn.commit()
        return len(rows)

    def get_latest_global_index_trade_date(self) -> date | None:
        with self.conn.cursor() as cur:
            cur.execute(SUPABASE_GLOBAL_INDEX_SCHEMA_SQL)
            cur.execute('SELECT max(trade_date) FROM "GlobalIndexOhlc"')
            row = cur.fetchone()
        self.conn.commit()
        return row[0] if row and row[0] else None

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

CREATE TABLE IF NOT EXISTS "SignalFeatureDaily" (
    feature_id bigserial PRIMARY KEY,
    signal_date date NOT NULL,
    symbol varchar(50) NOT NULL,
    feature_version varchar(50) NOT NULL DEFAULT 'v1',
    close_1515 double precision,
    open_915 double precision,
    high_day double precision,
    low_day double precision,
    volume_day bigint,
    ma10 double precision,
    ma20 double precision,
    ma50 double precision,
    ma90 double precision,
    rsi14 double precision,
    atr14 double precision,
    bb_upper double precision,
    bb_middle double precision,
    bb_lower double precision,
    bb_width double precision,
    ret_5d double precision,
    ret_10d double precision,
    ret_20d double precision,
    ret_60d double precision,
    volatility_10d double precision,
    volatility_20d double precision,
    volume_10d double precision,
    volume_20d double precision,
    trend_efficiency_5d double precision,
    trend_efficiency_10d double precision,
    trend_efficiency_20d double precision,
    trend_efficiency_60d double precision,
    relative_strength_vs_sector double precision,
    ma5d_slope double precision,
    ma10d_slope double precision,
    ma20_slope double precision,
    ma50_slope double precision,
    recent_high_5d double precision,
    recent_low_5d double precision,
    recent_high_10d double precision,
    recent_low_10d double precision,
    recent_high_20d double precision,
    recent_low_20d double precision,
    range_position_5d double precision,
    range_position_10d double precision,
    range_position_20d double precision,
    regime varchar(30),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    CONSTRAINT uq_signal_feature_daily UNIQUE (signal_date, symbol, feature_version)
);

CREATE INDEX IF NOT EXISTS ix_signal_feature_daily_symbol_date
    ON "SignalFeatureDaily" (symbol, signal_date);
CREATE INDEX IF NOT EXISTS ix_signal_feature_daily_regime
    ON "SignalFeatureDaily" (signal_date, regime);
"""


SUPABASE_NEWS_SENTIMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS "NewsArticle" (
    article_id   varchar(64) PRIMARY KEY,
    source       varchar(200),
    url          text,
    title        text,
    summary      text,
    published_at timestamptz,
    fetched_at   timestamptz,
    region       varchar(50),
    provider     varchar(50),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_news_article_published_at
    ON "NewsArticle" (published_at);
CREATE INDEX IF NOT EXISTS ix_news_article_provider_region
    ON "NewsArticle" (provider, region);

CREATE TABLE IF NOT EXISTS "NewsArticleSentiment" (
    target_date               date NOT NULL,
    article_id                varchar(64) NOT NULL,
    window_start              timestamptz,
    window_end                timestamptz,
    sentiment_label           varchar(20),
    sentiment_score           double precision,
    sentiment_confidence      double precision,
    sentiment_model           varchar(100),
    sectors                   text,
    sector_confidences        text,
    sector_weight             double precision,
    weighted_sentiment        double precision,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_news_article_sentiment PRIMARY KEY (target_date, article_id),
    CONSTRAINT fk_news_article_sentiment_article
        FOREIGN KEY (article_id) REFERENCES "NewsArticle"(article_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_news_article_sentiment_target_date
    ON "NewsArticleSentiment" (target_date);
CREATE INDEX IF NOT EXISTS ix_news_article_sentiment_label
    ON "NewsArticleSentiment" (target_date, sentiment_label);

CREATE TABLE IF NOT EXISTS "NiftyMarketSentiment" (
    target_date                date PRIMARY KEY,
    window_start               timestamptz,
    window_end                 timestamptz,
    article_count              integer,
    usable_article_count       integer,
    composite_score            double precision,
    composite_label            varchar(20),
    mean_confidence            double precision,
    positive_count             integer,
    neutral_count              integer,
    negative_count             integer,
    weighted_signal_sum        double precision,
    normalization_denominator  double precision,
    source_mix                 text,
    generated_at               timestamptz,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_nifty_market_sentiment_label
    ON "NiftyMarketSentiment" (target_date, composite_label);
"""


SUPABASE_GLOBAL_INDEX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS "GlobalIndexOhlc" (
    index_code    varchar(50) NOT NULL,
    index_name    varchar(120) NOT NULL,
    yahoo_symbol  varchar(50) NOT NULL,
    region        varchar(50),
    currency      varchar(10),
    trade_date    date NOT NULL,
    open_price    double precision,
    high_price    double precision,
    low_price     double precision,
    close_price   double precision,
    adj_close     double precision,
    volume        bigint,
    source        varchar(50) NOT NULL DEFAULT 'yfinance',
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_global_index_ohlc PRIMARY KEY (index_code, trade_date, source)
);

CREATE INDEX IF NOT EXISTS ix_global_index_ohlc_date
    ON "GlobalIndexOhlc" (trade_date);
CREATE INDEX IF NOT EXISTS ix_global_index_ohlc_symbol_date
    ON "GlobalIndexOhlc" (index_code, trade_date);
"""
