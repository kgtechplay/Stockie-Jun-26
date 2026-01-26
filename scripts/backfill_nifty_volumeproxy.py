"""
backfill_market_activity_daily_udiff.py

Backfills dbo.MarketActivityDaily (1 row per trade_date per underlying)
using NSE FO UDiFF bhavcopy zip:
BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip

Targets:
- FUTIDX rows for TckrSymb in ('NIFTY', 'BANKNIFTY')
- Select near-month expiry (min XpryDt >= trade_date)
- Upsert into Azure SQL / SQL Server

Requirements:
    pip install pandas requests pyodbc

Env (recommended):
    set AZURE_SQL_CONN_STR=DRIVER={ODBC Driver 17 for SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=...;Encrypt=yes;TrustServerCertificate=no
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, Iterable

import pandas as pd
import requests
import pyodbc
from dotenv import load_dotenv

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
load_dotenv()


# -----------------------
# Config
# -----------------------
UNDERLYINGS = ["NIFTY", "BANKNIFTY"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

AZURE_SQL_CONN_STR = os.getenv("AZURE_SQL_CONN_STR", "").strip()

# Backfill window (change as needed)
BACKFILL_START = date(2025, 7, 1)
BACKFILL_END = date(2025, 11, 30)


# -----------------------
# Helpers
# -----------------------
def daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def nse_fo_bhavcopy_url(d: date) -> str:
    # UDiFF FO bhavcopy naming (as per your file)
    return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
            "Connection": "keep-alive",
        }
    )
    return s


def download_zip_bytes(sess: requests.Session, url: str) -> Optional[bytes]:
    """
    NSE often behaves better if the homepage is hit once to establish cookies.
    """
    try:
        sess.get("https://www.nseindia.com", timeout=15)
    except Exception:
        pass

    try:
        r = sess.get(url, timeout=40)
        if r.status_code != 200 or not r.content:
            return None
        return r.content
    except requests.RequestException:
        return None


def read_csv_from_zip(zip_bytes: bytes) -> pd.DataFrame:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
    if not csv_name:
        raise ValueError("No CSV found inside zip.")
    with zf.open(csv_name) as f:
        return pd.read_csv(f)


def select_near_month_futidx_udiff(df: pd.DataFrame, symbol: str, trade_date: date) -> Optional[Dict[str, Any]]:
    """
    Works with UDiFF FO bhavcopy columns exactly like you shared:
      FinInstrmTp, TckrSymb, XpryDt, FinInstrmNm, OpnPric, HghPric, LwPric, ClsPric, SttlmPric, UndrlygPric,
      OpnIntrst, ChngInOpnIntrst, TtlTradgVol, TtlTrfVal
    
    Note: FinInstrmTp values in NSE UDiFF format:
      - 'IDF' = Index Futures (NIFTY, BANKNIFTY)
      - 'IDO' = Index Options
      - 'STF' = Stock Futures
      - 'STO' = Stock Options
    """
    required = ["FinInstrmTp", "TckrSymb", "XpryDt"]
    for c in required:
        if c not in df.columns:
            raise ValueError(f"Missing required column {c}. Have={list(df.columns)}")

    dff = df[
        (df["FinInstrmTp"].astype(str).str.upper() == "IDF")
        & (df["TckrSymb"].astype(str).str.upper() == symbol.upper())
    ].copy()

    if dff.empty:
        return None

    dff["XpryDt"] = pd.to_datetime(dff["XpryDt"], errors="coerce").dt.date
    dff = dff.dropna(subset=["XpryDt"])

    # Near-month: earliest expiry >= trade_date
    dff = dff[dff["XpryDt"] >= trade_date].sort_values("XpryDt")
    if dff.empty:
        return None

    row = dff.iloc[0]

    def f(col: str) -> Optional[float]:
        if col not in row.index or pd.isna(row[col]):
            return None
        try:
            return float(row[col])
        except Exception:
            return None

    def i(col: str) -> Optional[int]:
        if col not in row.index or pd.isna(row[col]):
            return None
        try:
            return int(row[col])
        except Exception:
            try:
                return int(float(row[col]))
            except Exception:
                return None

    return {
        "fin_instrm_tp": "IDF",  # Index Futures in NSE UDiFF format
        "tckr_symb": symbol.upper(),
        "expiry_date": row["XpryDt"],
        "fin_instrm_nm": row["FinInstrmNm"] if "FinInstrmNm" in row.index and pd.notna(row["FinInstrmNm"]) else None,

        "open_price": f("OpnPric"),
        "high_price": f("HghPric"),
        "low_price": f("LwPric"),
        "close_price": f("ClsPric") if f("ClsPric") is not None else f("LastPric"),
        "settle_price": f("SttlmPric"),
        "underlying_price": f("UndrlygPric"),

        "open_interest": i("OpnIntrst"),
        "change_in_oi": i("ChngInOpnIntrst"),
        "traded_volume": i("TtlTradgVol"),
        "traded_value": f("TtlTrfVal"),
    }


def upsert_market_activity_daily(
    conn: pyodbc.Connection,
    underlying: str,
    trade_date: date,
    p: Dict[str, Any],
    source_url: str,
):
    """
    Upserts into dbo.MarketActivityDaily.
    Table schema expected (from earlier):
      underlying, trade_date, fin_instrm_tp, tckr_symb, expiry_date, fin_instrm_nm,
      open_price, high_price, low_price, close_price, settle_price, underlying_price,
      open_interest, change_in_oi, traded_volume, traded_value, source_url
    """
    sql = """
    MERGE dbo.MarketActivityDaily AS tgt
    USING (SELECT ? AS underlying, ? AS trade_date) AS src
      ON tgt.underlying = src.underlying
     AND tgt.trade_date = src.trade_date
    WHEN MATCHED THEN UPDATE SET
        fin_instrm_tp = ?, tckr_symb = ?, expiry_date = ?, fin_instrm_nm = ?,
        open_price = ?, high_price = ?, low_price = ?, close_price = ?, settle_price = ?, underlying_price = ?,
        open_interest = ?, change_in_oi = ?, traded_volume = ?, traded_value = ?,
        source_url = ?
    WHEN NOT MATCHED THEN INSERT (
        underlying, trade_date,
        fin_instrm_tp, tckr_symb, expiry_date, fin_instrm_nm,
        open_price, high_price, low_price, close_price, settle_price, underlying_price,
        open_interest, change_in_oi, traded_volume, traded_value,
        source_url
    ) VALUES (
        ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?
    );
    """

    params = (
        # source key
        underlying,
        trade_date,

        # update set
        p["fin_instrm_tp"],
        p["tckr_symb"],
        p["expiry_date"],
        p.get("fin_instrm_nm"),
        p.get("open_price"),
        p.get("high_price"),
        p.get("low_price"),
        p.get("close_price"),
        p.get("settle_price"),
        p.get("underlying_price"),
        p.get("open_interest"),
        p.get("change_in_oi"),
        p.get("traded_volume"),
        p.get("traded_value"),
        source_url,

        # insert values
        underlying,
        trade_date,
        p["fin_instrm_tp"],
        p["tckr_symb"],
        p["expiry_date"],
        p.get("fin_instrm_nm"),
        p.get("open_price"),
        p.get("high_price"),
        p.get("low_price"),
        p.get("close_price"),
        p.get("settle_price"),
        p.get("underlying_price"),
        p.get("open_interest"),
        p.get("change_in_oi"),
        p.get("traded_volume"),
        p.get("traded_value"),
        source_url,
    )

    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()


# -----------------------
# Main
# -----------------------
def main():
    if not AZURE_SQL_CONN_STR:
        raise RuntimeError(
            "AZURE_SQL_CONN_STR is empty. Set it as an environment variable or hardcode it in the script."
        )

    sess = build_session()
    conn = pyodbc.connect(AZURE_SQL_CONN_STR)

    inserted = 0
    skipped = 0

    for d in daterange(BACKFILL_START, BACKFILL_END):
        # skip weekends (holidays are handled automatically by missing files)
        if d.weekday() >= 5:
            continue

        url = nse_fo_bhavcopy_url(d)
        zip_bytes = download_zip_bytes(sess, url)
        if not zip_bytes:
            skipped += 1
            continue

        try:
            df = read_csv_from_zip(zip_bytes)
        except Exception:
            skipped += 1
            continue

        for sym in UNDERLYINGS:
            payload = select_near_month_futidx_udiff(df, sym, d)
            if payload is None:
                continue

            upsert_market_activity_daily(conn, sym, d, payload, url)
            inserted += 1

        # light progress
        if d.day in (1, 10, 20) and d.month in (1, 3, 6, 9, 12):
            print(f"[{d}] progress... inserted={inserted}, skipped_days={skipped}")

    conn.close()
    print(f"Done. Upserts={inserted}, skipped_days={skipped}")


if __name__ == "__main__":
    main()
