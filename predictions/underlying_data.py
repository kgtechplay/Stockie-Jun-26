
# underlying_data.py (EXTENDED: joins UnderlyingSnapshot OHLC + MarketActivityDaily volume/OI proxies)
#
# Goal:
# - Keep existing behavior for predictions (still returns daily rows).
# - Extend fetch_index_daily() to also bring MarketActivityDaily proxy columns
#   so you can later use them in prediction/backtest pipelines.
#
# IMPORTANT:
# - This does NOT change your prediction_logic yet.
# - It only enriches the returned dataframe with extra columns.

import sys
from pathlib import Path
import pyodbc
import pandas as pd
from dotenv import load_dotenv
import os

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
load_dotenv()

# Import config after path is set
from src.config import get_settings


def get_db_connection() -> pyodbc.Connection:
    print("KASHYAP AZURE_SQL_CONN_STR exists:", bool(os.getenv("AZURE_SQL_CONN_STR")))
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
    start_date: str | None = None,   # optional: "YYYY-MM-DD"
    end_date: str | None = None,     # optional: "YYYY-MM-DD"
    join_activity: bool = True,
) -> pd.DataFrame:
    """
    Fetch DAILY index data from dbo.UnderlyingSnapshot (1 row per trade_date),
    optionally enriched with MarketActivityDaily proxy data for the SAME trade_date.

    Returns columns (at minimum):
      - trade_date
      - open_price, high_price, low_price, close_price
      - volume (if present in UnderlyingSnapshot)

    If join_activity=True, also returns (nullable if missing):
      - fut_expiry_date
      - fut_close_price
      - fut_settle_price
      - fut_underlying_price
      - fut_open_interest
      - fut_change_in_oi
      - fut_traded_volume
      - fut_traded_value

    Notes:
    - For prediction at open of day T, you should use activity columns from T-1 to avoid lookahead.
      We are NOT shifting here; we’re just returning the raw joined dataset.
    """

    where = ["s.underlying = ?"]
    params: list = [underlying]

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

            -- Market activity proxies (same trade_date)
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

    # Hard requirement for your current prediction code
    if "close_price" not in df.columns:
        raise ValueError(f"{snapshot_table} must contain close_price for predictions.")

    return df


def fetch_5m_candles_for_dates(
    conn: pyodbc.Connection,
    underlying: str,
    dates: list,
) -> pd.DataFrame:
    """
    Fetch 5-minute candle data for given dates from UnderlyingCandle5m table.
    
    Returns aggregated min/max prices per date:
    - min_low_price: Minimum low_price across all 5-minute candles for that date
    - max_high_price: Maximum high_price across all 5-minute candles for that date
    
    Args:
        conn: Database connection
        underlying: NIFTY or BANKNIFTY
        dates: List of dates (date objects or datetime objects)
        
    Returns:
        DataFrame with columns: trade_date, min_low_price, max_high_price
    """
    if not dates:
        return pd.DataFrame()
    
    # Convert dates to date objects if needed
    date_list = [pd.to_datetime(d).date() if not isinstance(d, type(pd.Timestamp.now().date())) else d for d in dates]
    unique_dates = sorted(set(date_list))
    
    if not unique_dates:
        return pd.DataFrame()
    
    # Build SQL query with date parameters
    placeholders = ','.join(['?' for _ in unique_dates])
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
    df = fetch_index_daily(conn, underlying="NIFTY", start_date="2025-01-01", end_date="2025-01-31", join_activity=True)
    print(df.head(10))
    print(df.tail(5))
    conn.close()
