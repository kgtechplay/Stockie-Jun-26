"""Point-in-time global-index features for the NIFTY cascade base dataset."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_GLOBAL_INDEX_DIR = PROJECT_ROOT / "output" / "intelligence" / "global_index_ohlc"

US_INDEXES = ["SP500", "NASDAQ", "DOW", "RUSSELL2000"]
EUROPE_INDEXES = ["FTSE100", "DAX", "CAC40"]
ASIA_INDEXES = ["NIKKEI225", "HANG_SENG", "SHANGHAI", "KOSPI", "ASX200"]
INDIA_CONTEXT_INDEXES = ["NIFTY50", "SENSEX", "INDIA_VIX"]
RISK_INDEXES = US_INDEXES + EUROPE_INDEXES + ASIA_INDEXES

GLOBAL_FEATURE_COLUMNS = [
    "global_us_return_mean",
    "global_europe_return_mean",
    "global_asia_return_mean",
    "global_india_context_return_mean",
    "global_return_mean",
    "global_positive_count",
    "global_negative_count",
    "global_breadth",
    "global_risk_on",
    "global_risk_off",
]


def load_global_index_rows(start_date: Any | None = None, end_date: Any | None = None) -> pd.DataFrame:
    try:
        return load_global_index_rows_from_db(start_date, end_date)
    except Exception as exc:  # noqa: BLE001 - local CSV fallback keeps research usable offline.
        print(f"[WARN] GlobalIndexOhlc DB load failed; falling back to local CSV files: {exc}")
        return load_global_index_rows_from_local()


def load_global_index_rows_from_db(start_date: Any | None = None, end_date: Any | None = None) -> pd.DataFrame:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        sql = (
            'SELECT index_code, trade_date, close_price '
            'FROM "GlobalIndexOhlc" WHERE close_price IS NOT NULL'
        )
        params: list[Any] = []
        if start_date is not None:
            sql += " AND trade_date >= %s"
            params.append(start_date)
        if end_date is not None:
            sql += " AND trade_date <= %s"
            params.append(end_date)
        sql += " ORDER BY trade_date, index_code"
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        db.close()
    return pd.DataFrame(rows, columns=["index_code", "trade_date", "close_price"])


def load_global_index_rows_from_local(input_dir: Path = LOCAL_GLOBAL_INDEX_DIR) -> pd.DataFrame:
    files = sorted(input_dir.glob("**/global_index_ohlc.csv"))
    if not files:
        raise FileNotFoundError(f"No local global index CSV files found under {input_dir}")
    frames = [pd.read_csv(path, usecols=["index_code", "trade_date", "close_price"]) for path in files]
    return pd.concat(frames, ignore_index=True).drop_duplicates(["index_code", "trade_date"])


def build_global_index_features(global_rows: pd.DataFrame) -> pd.DataFrame:
    """Build daily global risk features from raw index OHLC rows.

    The features are indexed by calendar date and can be merged into NIFTY
    `trade_date` rows with `merge_asof`. US and Europe features are lagged one
    available row before aggregation so the base dataset does not accidentally use
    market closes that happen after the Indian trading session.
    """
    if global_rows.empty:
        return pd.DataFrame(columns=["trade_date", *GLOBAL_FEATURE_COLUMNS])

    rows = global_rows.copy()
    rows["trade_date"] = pd.to_datetime(rows["trade_date"])
    rows["close_price"] = pd.to_numeric(rows["close_price"], errors="coerce")
    rows = rows.sort_values(["index_code", "trade_date"])
    rows["index_return_1d"] = rows.groupby("index_code")["close_price"].pct_change()

    pivot = rows.pivot_table(index="trade_date", columns="index_code", values="index_return_1d", aggfunc="last")
    effective = pivot.copy()
    for index_code in US_INDEXES + EUROPE_INDEXES:
        if index_code in effective.columns:
            effective[index_code] = effective[index_code].shift(1)

    features = pd.DataFrame(index=effective.index).sort_index()
    for index_code in sorted(rows["index_code"].dropna().unique()):
        if index_code in effective.columns:
            features[f"global_ret_{index_code}"] = effective[index_code]

    features["global_us_return_mean"] = _mean_existing(effective, US_INDEXES)
    features["global_europe_return_mean"] = _mean_existing(effective, EUROPE_INDEXES)
    features["global_asia_return_mean"] = _mean_existing(effective, ASIA_INDEXES)
    features["global_india_context_return_mean"] = _mean_existing(effective, INDIA_CONTEXT_INDEXES)
    features["global_return_mean"] = _mean_existing(effective, RISK_INDEXES)

    risk_frame = effective[[c for c in RISK_INDEXES if c in effective.columns]]
    features["global_positive_count"] = (risk_frame > 0).sum(axis=1)
    features["global_negative_count"] = (risk_frame < 0).sum(axis=1)
    denominator = features["global_positive_count"] + features["global_negative_count"]
    features["global_breadth"] = (
        (features["global_positive_count"] - features["global_negative_count"])
        / denominator.replace(0, pd.NA)
    )
    features["global_risk_on"] = (
        (features["global_return_mean"] >= 0.002) & (features["global_breadth"] >= 0.20)
    ).astype(int)
    features["global_risk_off"] = (
        (features["global_return_mean"] <= -0.002) & (features["global_breadth"] <= -0.20)
    ).astype(int)
    return features.reset_index()


def add_global_index_features(base: pd.DataFrame) -> pd.DataFrame:
    if base.empty or "trade_date" not in base.columns:
        return base

    start_date = pd.to_datetime(base["trade_date"]).min().date()
    end_date = pd.to_datetime(base["trade_date"]).max().date()
    global_rows = load_global_index_rows(start_date, end_date)
    features = build_global_index_features(global_rows)
    if features.empty:
        return _ensure_global_columns(base.copy())

    out = base.copy()
    global_cols = [c for c in out.columns if c.startswith("global_")]
    out = out.drop(columns=global_cols, errors="ignore")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).astype("datetime64[ns]")
    features["trade_date"] = pd.to_datetime(features["trade_date"]).astype("datetime64[ns]")
    out = pd.merge_asof(
        out.sort_values("trade_date"),
        features.sort_values("trade_date"),
        on="trade_date",
        direction="backward",
    )
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return _ensure_global_columns(out)


def _mean_existing(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    present = [c for c in columns if c in frame.columns]
    if not present:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return frame[present].mean(axis=1)


def _ensure_global_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in GLOBAL_FEATURE_COLUMNS:
        if col not in df.columns:
            df[col] = float("nan")
    return df