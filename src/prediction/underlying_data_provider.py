from __future__ import annotations

import pyodbc
import pandas as pd
from dotenv import load_dotenv

from src.core.config import get_settings

load_dotenv()


def get_db_connection() -> pyodbc.Connection:
    settings = get_settings()
    conn_str = settings.azure_sql_conn_str
    if not conn_str:
        raise ValueError(
            "AZURE_SQL_CONN_STR is not set in environment or .env file. "
            "Please set it in your .env file."
        )
    return pyodbc.connect(conn_str)


def fetch_index_daily(
    conn: pyodbc.Connection,
    snapshot_table: str = "dbo.UnderlyingSnapshot",
    activity_table: str = "dbo.MarketActivityDaily",
    underlying: str = "NIFTY",
    start_date: str | None = None,
    end_date: str | None = None,
    join_activity: bool = True,
) -> pd.DataFrame:
    """Fetch daily index rows, optionally enriched with futures activity proxies."""
    where = ["s.underlying = ?"]
    params: list[object] = [underlying]

    if start_date:
        where.append("s.trade_date >= ?")
        params.append(pd.to_datetime(start_date).date())

    if end_date:
        where.append("s.trade_date <= ?")
        params.append(pd.to_datetime(end_date).date())

    where_clause = " AND ".join(where)

    if join_activity:
        sql = f"""
        SELECT
            s.trade_date,
            s.open_price,
            s.high_price,
            s.low_price,
            s.close_price,
            s.volume,
            m.expiry_date      AS fut_expiry_date,
            m.close_price      AS fut_close_price,
            m.settle_price     AS fut_settle_price,
            m.underlying_price AS fut_underlying_price,
            m.open_interest    AS fut_open_interest,
            m.change_in_oi     AS fut_change_in_oi,
            m.traded_volume    AS fut_traded_volume,
            m.traded_value     AS fut_traded_value
        FROM {snapshot_table} s
        LEFT JOIN {activity_table} m
          ON m.underlying = s.underlying
         AND m.trade_date = s.trade_date
         AND m.fin_instrm_tp = 'IDF'
         AND m.tckr_symb = s.underlying
        WHERE {where_clause}
        ORDER BY s.trade_date;
        """
    else:
        sql = f"""
        SELECT
            s.trade_date,
            s.open_price,
            s.high_price,
            s.low_price,
            s.close_price,
            s.volume
        FROM {snapshot_table} s
        WHERE {where_clause}
        ORDER BY s.trade_date;
        """

    df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return df

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    if "close_price" not in df.columns:
        raise ValueError(f"{snapshot_table} must contain close_price for predictions.")

    return df


def fetch_5m_candles_for_dates(
    conn: pyodbc.Connection,
    underlying: str,
    dates: list,
) -> pd.DataFrame:
    """Fetch aggregated min low / max high from UnderlyingCandle5m for given dates."""
    if not dates:
        return pd.DataFrame()

    date_list = [
        pd.to_datetime(d).date() if not isinstance(d, type(pd.Timestamp.now().date())) else d
        for d in dates
    ]
    unique_dates = sorted(set(date_list))

    if not unique_dates:
        return pd.DataFrame()

    placeholders = ",".join(["?" for _ in unique_dates])
    sql = f"""
        SELECT
            trade_date,
            MIN(low_price) AS min_low_price,
            MAX(high_price) AS max_high_price
        FROM dbo.UnderlyingCandle5m
        WHERE underlying = ?
          AND trade_date IN ({placeholders})
        GROUP BY trade_date
        ORDER BY trade_date
    """

    params = [underlying] + unique_dates
    df = pd.read_sql(sql, conn, params=params)

    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()

    return df


if __name__ == "__main__":
    conn = get_db_connection()
    try:
        df = fetch_index_daily(
            conn,
            underlying="NIFTY",
            start_date="2025-01-01",
            end_date="2025-01-31",
            join_activity=True,
        )
        print(df.head(10))
        print(df.tail(5))
    finally:
        conn.close()

