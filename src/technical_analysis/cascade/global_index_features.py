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


_RISK_OFF_RETURN = -0.002   # mean cumulative return threshold
_RISK_OFF_BREADTH = -0.20   # breadth threshold (fraction: (pos-neg)/n)
_RISK_ON_RETURN = 0.002
_RISK_ON_BREADTH = 0.20


def build_gap_gate_signal(rows: pd.DataFrame) -> dict:
    """Compute a cumulative gate signal from GlobalIndexOhlc rows spanning a date range.

    For each RISK_INDEX present in `rows`, cumulative return = (last - first) / first
    across all dates in the range. Returns both the 12-index compound risk_off/risk_on
    gate AND the 3-regional GlobalNoDisagree gate (put_agree / call_agree), so callers
    can apply either or both.

    Intended for holiday-gap scenarios where multiple sessions of global data must be
    evaluated cumulatively before an Indian market open.

    Keys returned:
        us_mean, europe_mean, asia_mean  — regional cumulative means
        all_mean                         — 12-index mean cumulative return
        breadth                          — (pos - neg) / n
        risk_off                         — all_mean <= -0.2% AND breadth <= -0.20
        risk_on                          — all_mean >= +0.2% AND breadth >= +0.20
        put_agree                        — 2 of 3 regions negative (GlobalNoDisagree for CALL)
        call_agree                       — 2 of 3 regions positive (GlobalNoDisagree for PUT)
        indices                          — per-index cumulative return
        dates_covered                    — distinct trade_dates in rows
    """
    _empty: dict = {
        "us_mean": 0.0, "europe_mean": 0.0, "asia_mean": 0.0,
        "all_mean": 0.0, "breadth": 0.0,
        "risk_off": False, "risk_on": False,
        "put_agree": False, "call_agree": False,
        "indices": {}, "dates_covered": 0,
    }
    if rows is None or rows.empty:
        return _empty

    rows = rows.copy()
    rows["close_price"] = pd.to_numeric(rows["close_price"], errors="coerce")
    rows["trade_date"] = pd.to_datetime(rows["trade_date"])

    index_returns: dict[str, float] = {}
    for idx_code in RISK_INDEXES:
        grp = rows[rows["index_code"] == idx_code].sort_values("trade_date").dropna(subset=["close_price"])
        if len(grp) >= 2:
            start = float(grp["close_price"].iloc[0])
            end = float(grp["close_price"].iloc[-1])
            if start > 0:
                index_returns[idx_code] = (end - start) / start

    if not index_returns:
        return {**_empty, "dates_covered": int(rows["trade_date"].nunique())}

    n = len(index_returns)
    pos = sum(1 for r in index_returns.values() if r > 0)
    neg = sum(1 for r in index_returns.values() if r < 0)
    breadth = (pos - neg) / n if n else 0.0

    def _rmean(codes: list[str]) -> float:
        vals = [index_returns[c] for c in codes if c in index_returns]
        return sum(vals) / len(vals) if vals else 0.0

    us_mean = _rmean(US_INDEXES)
    europe_mean = _rmean(EUROPE_INDEXES)
    asia_mean = _rmean(ASIA_INDEXES)
    all_mean = sum(index_returns.values()) / n
    regional = [us_mean, europe_mean, asia_mean]

    return {
        "us_mean": round(us_mean, 6),
        "europe_mean": round(europe_mean, 6),
        "asia_mean": round(asia_mean, 6),
        "all_mean": round(all_mean, 6),
        "breadth": round(breadth, 4),
        "risk_off": bool(all_mean <= _RISK_OFF_RETURN and breadth <= _RISK_OFF_BREADTH),
        "risk_on": bool(all_mean >= _RISK_ON_RETURN and breadth >= _RISK_ON_BREADTH),
        "put_agree": bool(sum(1 for v in regional if v < 0) >= 2),
        "call_agree": bool(sum(1 for v in regional if v > 0) >= 2),
        "indices": {k: round(v, 6) for k, v in sorted(index_returns.items())},
        "dates_covered": int(rows["trade_date"].nunique()),
    }


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