from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GLOBAL_INDEX_OUTPUT_DIR = PROJECT_ROOT / "output" / "intelligence" / "global_index_ohlc"


GLOBAL_INDEX_UNIVERSE: tuple[dict[str, str], ...] = (
    {"index_code": "NIFTY50", "index_name": "Nifty 50", "yahoo_symbol": "^NSEI", "region": "India", "currency": "INR"},
    {"index_code": "SENSEX", "index_name": "BSE Sensex", "yahoo_symbol": "^BSESN", "region": "India", "currency": "INR"},
    {"index_code": "INDIA_VIX", "index_name": "India VIX", "yahoo_symbol": "^INDIAVIX", "region": "India", "currency": "INR"},
    {"index_code": "SP500", "index_name": "S&P 500", "yahoo_symbol": "^GSPC", "region": "United States", "currency": "USD"},
    {"index_code": "NASDAQ", "index_name": "NASDAQ Composite", "yahoo_symbol": "^IXIC", "region": "United States", "currency": "USD"},
    {"index_code": "DOW", "index_name": "Dow Jones Industrial Average", "yahoo_symbol": "^DJI", "region": "United States", "currency": "USD"},
    {"index_code": "RUSSELL2000", "index_name": "Russell 2000", "yahoo_symbol": "^RUT", "region": "United States", "currency": "USD"},
    {"index_code": "FTSE100", "index_name": "FTSE 100", "yahoo_symbol": "^FTSE", "region": "United Kingdom", "currency": "GBP"},
    {"index_code": "DAX", "index_name": "DAX", "yahoo_symbol": "^GDAXI", "region": "Germany", "currency": "EUR"},
    {"index_code": "CAC40", "index_name": "CAC 40", "yahoo_symbol": "^FCHI", "region": "France", "currency": "EUR"},
    {"index_code": "HANG_SENG", "index_name": "Hang Seng", "yahoo_symbol": "^HSI", "region": "Hong Kong", "currency": "HKD"},
    {"index_code": "NIKKEI225", "index_name": "Nikkei 225", "yahoo_symbol": "^N225", "region": "Japan", "currency": "JPY"},
    {"index_code": "SHANGHAI", "index_name": "Shanghai Composite", "yahoo_symbol": "000001.SS", "region": "China", "currency": "CNY"},
    {"index_code": "KOSPI", "index_name": "KOSPI", "yahoo_symbol": "^KS11", "region": "South Korea", "currency": "KRW"},
    {"index_code": "ASX200", "index_name": "ASX 200", "yahoo_symbol": "^AXJO", "region": "Australia", "currency": "AUD"},
)


def fetch_global_index_ohlc(
    start_date: date,
    end_date: date,
    index_universe: tuple[dict[str, str], ...] = GLOBAL_INDEX_UNIVERSE,
) -> list[dict[str, Any]]:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for global index loading. Install requirements.txt first.") from exc

    rows: list[dict[str, Any]] = []
    fetch_end = end_date + timedelta(days=1)
    fetched_at = datetime.now(timezone.utc)

    for index_meta in index_universe:
        frame = yf.download(
            index_meta["yahoo_symbol"],
            start=start_date.isoformat(),
            end=fetch_end.isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        rows.extend(normalize_yfinance_frame(frame, index_meta, fetched_at=fetched_at))
    return rows


def normalize_yfinance_frame(
    frame: pd.DataFrame,
    index_meta: dict[str, str],
    fetched_at: datetime | None = None,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []

    normalized = frame.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = normalized.columns.get_level_values(0)

    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw_trade_date, row in normalized.iterrows():
        trade_date = pd.Timestamp(raw_trade_date).date()
        rows.append({
            "index_code": index_meta["index_code"],
            "index_name": index_meta["index_name"],
            "yahoo_symbol": index_meta["yahoo_symbol"],
            "region": index_meta.get("region"),
            "currency": index_meta.get("currency"),
            "trade_date": trade_date,
            "open_price": _float_or_none(row.get("Open")),
            "high_price": _float_or_none(row.get("High")),
            "low_price": _float_or_none(row.get("Low")),
            "close_price": _float_or_none(row.get("Close")),
            "adj_close": _float_or_none(row.get("Adj Close")),
            "volume": _int_or_none(row.get("Volume")),
            "source": "yfinance",
            "fetched_at": fetched_at,
        })
    return rows


def write_global_index_ohlc_csv(
    rows: list[dict[str, Any]],
    end_date: date,
    output_dir: Path = DEFAULT_GLOBAL_INDEX_OUTPUT_DIR,
) -> Path | None:
    if not rows:
        return None
    partition_dir = output_dir / end_date.strftime("%d-%m-%Y")
    partition_dir.mkdir(parents=True, exist_ok=True)
    output_path = partition_dir / "global_index_ohlc.csv"
    pd.DataFrame(rows).sort_values(["trade_date", "index_code"]).to_csv(output_path, index=False)
    return output_path


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)