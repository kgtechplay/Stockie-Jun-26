from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.technical_analysis.prediction.aggregator import build_strategy_signals
from src.technical_analysis.prediction.regime import detect_regime
from src.technical_analysis.prediction.schema import UnderlyingView
from src.technical_analysis.prediction.snapshot import build_feature_snapshot, build_regime_snapshot
from src.technical_analysis.prediction.strategies import BUILTIN_UNDERLYING_STRATEGIES
from src.technical_analysis.prediction.view import build_underlying_view


def connect_db():
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    return db


def fetch_underlying_history(
    db,
    underlying: str,
    start_date: date,
    end_date: date,
    warmup_days: int,
) -> pd.DataFrame:
    fetch_start = start_date - timedelta(days=warmup_days)
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = '"UnderlyingSnapshot"' if is_postgres else "dbo.UnderlyingSnapshot"
    placeholder = "%s" if is_postgres else "?"
    sql = f"""
        SELECT trade_date, open_price, high_price, low_price, close_price, volume
        FROM {table}
        WHERE underlying = {placeholder}
          AND trade_date >= {placeholder}
          AND trade_date <= {placeholder}
        ORDER BY trade_date
    """
    df = pd.read_sql(sql, db.conn, params=[underlying.upper(), fetch_start, end_date])
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df.sort_values("trade_date").reset_index(drop=True)


def build_view_for_window(
    underlying: str,
    trade_date: date,
    window: pd.DataFrame,
) -> UnderlyingView:
    regime = detect_regime(window)
    if regime == "UNKNOWN":
        regime = detect_short_window_regime(window)
    features = build_feature_snapshot(underlying, trade_date, window)
    regime_snapshot = build_regime_snapshot(regime)
    predictions = {
        name: definition.predict(window)
        for name, definition in BUILTIN_UNDERLYING_STRATEGIES.items()
    }
    strategy_signals = build_strategy_signals(predictions, features, regime_snapshot)
    return build_underlying_view(
        symbol=underlying,
        trade_date=trade_date.isoformat(),
        stock_features=features,
        regime_snapshot=regime_snapshot,
        strategy_signals=strategy_signals,
    )


def detect_short_window_regime(window: pd.DataFrame) -> str:
    if len(window) < 20 or "close_price" not in window.columns:
        return "UNKNOWN"
    closes = window["close_price"].astype(float)
    ret = closes.iloc[-1] / closes.iloc[0] - 1.0 if closes.iloc[0] else 0.0
    ma10 = closes.tail(10).mean()
    ma20 = closes.tail(20).mean()
    volatility = closes.pct_change().dropna().tail(20).std()
    path_move = closes.diff().abs().sum()
    trend_efficiency = abs(closes.iloc[-1] - closes.iloc[0]) / path_move if path_move else 0.0

    if ret > 0.02 and closes.iloc[-1] > ma10 > ma20 and trend_efficiency >= 0.30:
        return "TREND_UP"
    if ret < -0.02 and closes.iloc[-1] < ma10 < ma20 and trend_efficiency >= 0.30:
        return "TREND_DOWN"
    if volatility >= 0.02 and trend_efficiency < 0.25:
        return "CHOPPY"
    return "RANGE"


def build_historical_underlying_views(
    db,
    underlying: str,
    start_date: date,
    end_date: date,
    lookback_days: int,
    warmup_days: int,
    min_history_days: int = 20,
) -> list[UnderlyingView]:
    df = fetch_underlying_history(db, underlying, start_date, end_date, warmup_days)
    if df.empty:
        return []
    views: list[UnderlyingView] = []
    for _, row in df[df["trade_date"].between(start_date, end_date)].iterrows():
        trade_dt = row["trade_date"]
        history = df[df["trade_date"] <= trade_dt].tail(lookback_days)
        if len(history) < min_history_days:
            continue
        views.append(build_view_for_window(underlying, trade_dt, history))
    return views


def dataclass_to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [dataclass_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_plain(item) for key, item in value.items()}
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclass_to_plain(payload), indent=2, default=str), encoding="utf-8")


def views_to_summary_rows(views: list[UnderlyingView]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for view in views:
        rows.append({
            "trade_date": view.trade_date,
            "symbol": view.symbol,
            "raw_signal": view.raw_signal,
            "direction": view.direction,
            "option_bias": view.option_bias,
            "is_option_eligible": view.is_option_eligible,
            "stock_regime": view.stock_regime,
            "primary_strategy": view.primary_strategy,
            "setup_type": view.setup_type,
            "strength_score": view.strength_score,
            "confidence": view.confidence,
            "expected_move_abs": view.expected_move_abs,
            "expected_move_pct": view.expected_move_pct,
            "expected_holding_days": view.expected_holding_days,
        })
    return rows


def fetch_spot_close_by_date(db, underlying: str, start_date: date, end_date: date) -> dict[str, float]:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = '"UnderlyingSnapshot"' if is_postgres else "dbo.UnderlyingSnapshot"
    placeholder = "%s" if is_postgres else "?"
    sql = f"""
        SELECT trade_date, close_price
        FROM {table}
        WHERE underlying = {placeholder}
          AND trade_date >= {placeholder}
          AND trade_date <= {placeholder}
          AND close_price IS NOT NULL
    """
    df = pd.read_sql(sql, db.conn, params=[underlying.upper(), start_date, end_date])
    if df.empty:
        return {}
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
    return {row["trade_date"]: float(row["close_price"]) for _, row in df.iterrows()}
