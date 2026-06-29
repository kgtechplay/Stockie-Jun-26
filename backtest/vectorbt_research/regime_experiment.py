"""
NIFTY regime detector experiments.

Script mode:
    python backtest/vectorbt_research/regime_experiment.py
    python backtest/vectorbt_research/regime_experiment.py --underlying NIFTY --start 2025-01-01 --end 2026-06-17

Outputs:
    output/backtest/NIFTY/regime/NIFTY_regime_experiment_comparison.csv
    output/backtest/NIFTY/regime/NIFTY_regime_experiment_summary.csv
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from dotenv import load_dotenv

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.technical_analysis.prediction.features import (
    FEATURE_COLUMNS,
    compute_underlying_features,
)
from src.technical_analysis.prediction.highsight_regime import compute_hindsight_regime
from src.technical_analysis.prediction.regime import detect_regime

DEFAULT_OUTPUT_DIR = Path("output") / "backtest" / "NIFTY" / "regime"
DEFAULT_LOOKBACK_DAYS = 140
DEFAULT_BUFFER_DAYS = 2

FAST_RET_5D_THRESHOLD = 0.0075
FAST_RET_10D_THRESHOLD = 0.01
FAST_RET_20D_THRESHOLD = 0.015
FAST_TREND_EFFICIENCY_5D_THRESHOLD = 0.45
FAST_TREND_EFFICIENCY_10D_THRESHOLD = 0.35
FAST_TREND_EFFICIENCY_20D_THRESHOLD = 0.30
FAST_RANGE_RETURN_10D_THRESHOLD = 0.0075
FAST_CHOPPY_EFFICIENCY_10D_MAX = 0.25
FAST_CHOPPY_RANGE_10D_THRESHOLD = 0.025
FAST_VOLATILITY_10D_HIGH = 0.022
FAST_VOLATILITY_10D_MODERATE = 0.028
BREAKOUT_LOOKBACK_DAYS = 20
BREAKOUT_BUFFER_PCT = 0.002
TREND_REGIMES = {"TREND_UP", "TREND_DOWN"}

RegimeDetector = Callable[[pd.DataFrame], str]


@dataclass(frozen=True)
class RegimeExperiment:
    name: str
    detect: RegimeDetector


def detect_fast_return_10_20(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    ret_10d = metrics["ret_10d"]
    ret_20d = metrics["ret_20d"]
    eff_10d = metrics["eff_10d"]
    if _none(ret_10d, ret_20d, eff_10d):
        return detect_regime(window)
    if ret_10d >= FAST_RET_10D_THRESHOLD and ret_20d >= FAST_RET_20D_THRESHOLD and eff_10d >= FAST_TREND_EFFICIENCY_10D_THRESHOLD:
        return "TREND_UP"
    if ret_10d <= -FAST_RET_10D_THRESHOLD and ret_20d <= -FAST_RET_20D_THRESHOLD and eff_10d >= FAST_TREND_EFFICIENCY_10D_THRESHOLD:
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_current_trend_ret60_015(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.015,
        eff_key="eff_60d",
        eff_threshold=0.25,
        stack="ma20_ma50",
        slopes="ma20_ma50",
    )


def detect_current_trend_ret60_010(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.010,
        eff_key="eff_60d",
        eff_threshold=0.25,
        stack="ma20_ma50",
        slopes="ma20_ma50",
    )


def detect_current_trend_eff60_020(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.020,
        eff_key="eff_60d",
        eff_threshold=0.20,
        stack="ma20_ma50",
        slopes="ma20_ma50",
    )


def detect_current_trend_ma50_flat_ok(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.020,
        eff_key="eff_60d",
        eff_threshold=0.25,
        stack="ma20_ma50",
        slopes="ma20_ma50_flat_ok",
    )


def detect_current_trend_ma20_slope_only(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.020,
        eff_key="eff_60d",
        eff_threshold=0.25,
        stack="ma20_ma50",
        slopes="ma20_only",
    )


def detect_current_trend_stack_ma10_ma20(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_60d",
        ret_threshold=0.020,
        eff_key="eff_60d",
        eff_threshold=0.25,
        stack="ma10_ma20",
        slopes="ma10_ma20",
    )


def detect_current_trend_ret20_eff20(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_20d",
        ret_threshold=0.015,
        eff_key="eff_20d",
        eff_threshold=0.30,
        stack="ma20_ma50",
        slopes="ma20_ma50",
    )


def detect_current_trend_ret10_eff10(window: pd.DataFrame) -> str:
    return _detect_current_trend_variant(
        window,
        ret_key="ret_10d",
        ret_threshold=0.010,
        eff_key="eff_10d",
        eff_threshold=0.35,
        stack="ma10_ma20",
        slopes="ma10_ma20",
    )


def detect_current_trend_range10_ret5_eff5(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if (
        _all(metrics, "range_position_10d", "ret_5d", "eff_5d")
        and metrics["range_position_10d"] >= 0.65
        and metrics["ret_5d"] >= 0
        and metrics["eff_5d"] >= 0.35
    ):
        return "TREND_UP"
    if (
        _all(metrics, "range_position_10d", "ret_5d", "eff_5d")
        and metrics["range_position_10d"] <= 0.30
        and metrics["ret_5d"] <= 0
        and metrics["eff_5d"] >= 0.35
    ):
        return "TREND_DOWN"
    return _current_non_trend_fallback(metrics)


def detect_current_trend_range20_ret5_ma5(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if (
        _all(metrics, "range_position_20d", "ret_5d", "ma5d_slope")
        and metrics["range_position_20d"] >= 0.65
        and metrics["ret_5d"] >= 0
        and metrics["ma5d_slope"] >= 0
    ):
        return "TREND_UP"
    if (
        _all(metrics, "range_position_20d", "ret_5d", "ma5d_slope")
        and metrics["range_position_20d"] <= 0.30
        and metrics["ret_5d"] <= 0
        and metrics["ma5d_slope"] <= 0
    ):
        return "TREND_DOWN"
    return _current_non_trend_fallback(metrics)


def detect_current_trend_ret5_eff5_ma5(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if (
        _all(metrics, "ret_5d", "eff_5d", "ma5d_slope")
        and metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD
        and metrics["eff_5d"] >= FAST_TREND_EFFICIENCY_5D_THRESHOLD
        and metrics["ma5d_slope"] > 0
    ):
        return "TREND_UP"
    if (
        _all(metrics, "ret_5d", "eff_5d", "ma5d_slope")
        and metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD
        and metrics["eff_5d"] >= FAST_TREND_EFFICIENCY_5D_THRESHOLD
        and metrics["ma5d_slope"] < 0
    ):
        return "TREND_DOWN"
    return _current_non_trend_fallback(metrics)


def detect_current_trend_ret10_range_eff10(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if (
        _all(metrics, "ret_10d", "range_position_10d", "eff_10d")
        and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD
        and metrics["range_position_10d"] >= 0.70
        and metrics["eff_10d"] >= 0.30
    ):
        return "TREND_UP"
    if (
        _all(metrics, "ret_10d", "range_position_10d", "eff_10d")
        and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD
        and metrics["range_position_10d"] <= 0.30
        and metrics["eff_10d"] >= 0.30
    ):
        return "TREND_DOWN"
    return _current_non_trend_fallback(metrics)


def detect_fast_ma10_ma20(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["close"], metrics["ma10"], metrics["ma20"], metrics["ma10_slope"], metrics["ret_10d"]):
        return detect_regime(window)
    if metrics["close"] > metrics["ma10"] > metrics["ma20"] and metrics["ma10_slope"] > 0 and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD:
        return "TREND_UP"
    if metrics["close"] < metrics["ma10"] < metrics["ma20"] and metrics["ma10_slope"] < 0 and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD:
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_fast_return_5_10(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["ret_5d"], metrics["ret_10d"], metrics["eff_5d"], metrics["eff_10d"]):
        return detect_regime(window)
    if (
        metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD
        and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD
        and metrics["eff_5d"] >= FAST_TREND_EFFICIENCY_5D_THRESHOLD
        and metrics["eff_10d"] >= FAST_TREND_EFFICIENCY_10D_THRESHOLD
    ):
        return "TREND_UP"
    if (
        metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD
        and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD
        and metrics["eff_5d"] >= FAST_TREND_EFFICIENCY_5D_THRESHOLD
        and metrics["eff_10d"] >= FAST_TREND_EFFICIENCY_10D_THRESHOLD
    ):
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_fast_slope_5_10(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["close"], metrics["ma10"], metrics["ma5d_slope"], metrics["ma10d_slope"], metrics["ret_5d"]):
        return detect_regime(window)
    if (
        metrics["close"] > metrics["ma10"]
        and metrics["ma5d_slope"] > 0
        and metrics["ma10d_slope"] > 0
        and metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD
    ):
        return "TREND_UP"
    if (
        metrics["close"] < metrics["ma10"]
        and metrics["ma5d_slope"] < 0
        and metrics["ma10d_slope"] < 0
        and metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD
    ):
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_hybrid_10_20(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["close"], metrics["ma10"], metrics["ma20"], metrics["ma10d_slope"], metrics["ma20_slope"], metrics["ret_10d"], metrics["ret_20d"], metrics["eff_20d"]):
        return detect_regime(window)
    if (
        metrics["close"] > metrics["ma10"] > metrics["ma20"]
        and metrics["ma10d_slope"] > 0
        and metrics["ma20_slope"] > 0
        and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD
        and metrics["ret_20d"] >= FAST_RET_20D_THRESHOLD
        and metrics["eff_20d"] >= FAST_TREND_EFFICIENCY_20D_THRESHOLD
    ):
        return "TREND_UP"
    if (
        metrics["close"] < metrics["ma10"] < metrics["ma20"]
        and metrics["ma10d_slope"] < 0
        and metrics["ma20_slope"] < 0
        and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD
        and metrics["ret_20d"] <= -FAST_RET_20D_THRESHOLD
        and metrics["eff_20d"] >= FAST_TREND_EFFICIENCY_20D_THRESHOLD
    ):
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_fast_range_chop_10d(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["ret_10d"], metrics["eff_10d"], metrics["volatility_10d"]):
        return detect_regime(window)
    if _fast_up(metrics):
        return "TREND_UP"
    if _fast_down(metrics):
        return "TREND_DOWN"
    if (
        abs(metrics["ret_10d"]) <= FAST_RANGE_RETURN_10D_THRESHOLD
        and metrics["eff_10d"] <= FAST_CHOPPY_EFFICIENCY_10D_MAX
        and metrics["volatility_10d"] >= FAST_VOLATILITY_10D_HIGH
    ):
        return "CHOPPY"
    if (
        abs(metrics["ret_10d"]) <= FAST_RANGE_RETURN_10D_THRESHOLD
        and metrics["volatility_10d"] <= FAST_VOLATILITY_10D_MODERATE
    ):
        return "RANGE"
    return "RANGE"


def detect_breakout_20d(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _none(metrics["close"], metrics["prior_high_20d"], metrics["prior_low_20d"], metrics["ret_5d"]):
        return detect_regime(window)
    if metrics["close"] > metrics["prior_high_20d"] * (1.0 + BREAKOUT_BUFFER_PCT) and metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD:
        return "TREND_UP"
    if metrics["close"] < metrics["prior_low_20d"] * (1.0 - BREAKOUT_BUFFER_PCT) and metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD:
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_fast_combo_v1(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    if _fast_up(metrics):
        return "TREND_UP"
    if _fast_down(metrics):
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


def detect_fast_combo_v2(window: pd.DataFrame) -> str:
    metrics = _metrics(window)
    current_regime = detect_regime(window)
    if current_regime in {"TREND_UP", "TREND_DOWN"}:
        return current_regime
    if _fast_up(metrics) or _recovery_up(metrics):
        return "TREND_UP"
    if _fast_down(metrics) or _selloff_down(metrics):
        return "TREND_DOWN"
    if _is_fast_choppy(metrics):
        return "CHOPPY"
    return "RANGE"


EXPERIMENTS: list[RegimeExperiment] = [
    RegimeExperiment("current", detect_regime),
    RegimeExperiment("current_trend_ret60_015", detect_current_trend_ret60_015),
    RegimeExperiment("current_trend_ret60_010", detect_current_trend_ret60_010),
    RegimeExperiment("current_trend_eff60_020", detect_current_trend_eff60_020),
    RegimeExperiment("current_trend_ma50_flat_ok", detect_current_trend_ma50_flat_ok),
    RegimeExperiment("current_trend_ma20_slope_only", detect_current_trend_ma20_slope_only),
    RegimeExperiment("current_trend_stack_ma10_ma20", detect_current_trend_stack_ma10_ma20),
    RegimeExperiment("current_trend_ret20_eff20", detect_current_trend_ret20_eff20),
    RegimeExperiment("current_trend_ret10_eff10", detect_current_trend_ret10_eff10),
    RegimeExperiment("current_trend_range10_ret5_eff5", detect_current_trend_range10_ret5_eff5),
    RegimeExperiment("current_trend_range20_ret5_ma5", detect_current_trend_range20_ret5_ma5),
    RegimeExperiment("current_trend_ret5_eff5_ma5", detect_current_trend_ret5_eff5_ma5),
    RegimeExperiment("current_trend_ret10_range_eff10", detect_current_trend_ret10_range_eff10),
]


def run_regime_experiments(
    underlying: str,
    start_date: date,
    end_date: date,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    buffer_days: int = DEFAULT_BUFFER_DAYS,
) -> dict[str, Any]:
    fetch_start = start_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    ohlcv_df = _fetch_ohlcv(underlying, fetch_start, end_date)
    if ohlcv_df.empty:
        print(f"No OHLCV rows found for {underlying} from {fetch_start} to {end_date}")
        return {"rows": 0}

    rows: list[dict[str, Any]] = []
    target = ohlcv_df[ohlcv_df["trade_date"].dt.date.between(start_date, end_date)]
    for idx, ohlcv_row in target.iterrows():
        window = ohlcv_df.iloc[max(0, idx - DEFAULT_LOOKBACK_DAYS + 1): idx + 1].copy()
        hindsight = compute_hindsight_regime(ohlcv_df, int(idx))
        row: dict[str, Any] = {
            "date": ohlcv_row["trade_date"].date().isoformat(),
            "underlying": underlying.upper(),
            "open": float(ohlcv_row["open_price"]) if pd.notna(ohlcv_row["open_price"]) else None,
            "high": float(ohlcv_row["high_price"]) if pd.notna(ohlcv_row["high_price"]) else None,
            "low": float(ohlcv_row["low_price"]) if pd.notna(ohlcv_row["low_price"]) else None,
            "close": float(ohlcv_row["close_price"]),
            "volume": float(ohlcv_row["volume"]) if pd.notna(ohlcv_row["volume"]) else None,
        }
        features = compute_underlying_features(window)
        for column in FEATURE_COLUMNS:
            row[column] = features.get(column)
        row.update(hindsight)
        for experiment in EXPERIMENTS:
            row[f"{experiment.name}_regime"] = experiment.detect(window)
        rows.append(row)

    out_df = pd.DataFrame(rows)
    out_df["hindsight_regime_minus_1d"] = out_df["hindsight_regime"].shift(1)
    out_df["hindsight_regime_minus_2d"] = out_df["hindsight_regime"].shift(2)
    out_df["lagged_hindsight_trend_2d"] = [
        _lagged_hindsight_trend_label(current, prev_1, prev_2)
        for current, prev_1, prev_2 in zip(
            out_df["hindsight_regime"],
            out_df["hindsight_regime_minus_1d"],
            out_df["hindsight_regime_minus_2d"],
        )
    ]
    for experiment in EXPERIMENTS:
        regime_col = f"{experiment.name}_regime"
        out_df[f"{experiment.name}_exact_match"] = _exact_matches(out_df[regime_col], out_df["hindsight_regime"])
        out_df[f"{experiment.name}_buffer_match"] = _buffered_matches(
            out_df[regime_col],
            out_df["hindsight_regime"],
            buffer_days,
        )
        out_df[f"{experiment.name}_lag2_trend_match"] = _lag2_trend_matches(
            out_df[regime_col],
            out_df["lagged_hindsight_trend_2d"],
        )

    summary_df = _build_summary(out_df, buffer_days)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = output_dir / f"{underlying.upper()}_regime_experiment_comparison.csv"
    summary_path = output_dir / f"{underlying.upper()}_regime_experiment_summary.csv"
    out_df.to_csv(comparison_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    print(f"Wrote {len(out_df)} comparison rows to {comparison_path}")
    print(f"Wrote {len(summary_df)} summary rows to {summary_path}")
    if not summary_df.empty:
        winner = summary_df.iloc[0]
        print(
            "Best lag2 trend recall: "
            f"{winner['experiment']} {winner['lag2_trend_recall_pct']:.1f}% "
            f"({int(winner['lag2_trend_match_count'])}/{int(winner['lag2_trend_rows'])}); "
            f"overall buffered accuracy {winner['overall_buffered_accuracy_pct']:.1f}%"
        )
    return {
        "rows": len(out_df),
        "comparison_path": str(comparison_path),
        "summary_path": str(summary_path),
    }


def _fetch_ohlcv(underlying: str, start_date: date, end_date: date) -> pd.DataFrame:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        is_postgres = getattr(db, "db_kind", "") == "postgres"
        table = '"UnderlyingSnapshot"' if is_postgres else "dbo.UnderlyingSnapshot"
        ph = "%s" if is_postgres else "?"
        sql = f"""
            SELECT trade_date, open_price, high_price, low_price, close_price, volume
            FROM {table}
            WHERE underlying = {ph} AND trade_date >= {ph} AND trade_date <= {ph}
            ORDER BY trade_date
        """
        df = pd.read_sql_query(sql, db.conn, params=(underlying.upper(), start_date, end_date))
    finally:
        db.close()
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values("trade_date").reset_index(drop=True)


def _metrics(window: pd.DataFrame) -> dict[str, float | None]:
    closes = window["close_price"].astype(float)
    features = compute_underlying_features(window)
    metrics: dict[str, float | None] = {
        "close": float(closes.iloc[-1]) if len(closes) else None,
        "ma10": _f(features.get("ma10")),
        "ma20": _f(features.get("ma20")),
        "ma50": _f(features.get("ma50")),
        "ma5d_slope": _f(features.get("ma5d_slope")),
        "ma10_slope": _f(features.get("ma10d_slope")),
        "ma10d_slope": _f(features.get("ma10d_slope")),
        "ma20_slope": _f(features.get("ma20_slope")),
        "ma50_slope": _f(features.get("ma50_slope")),
        "ret_5d": _f(features.get("ret_5d")),
        "ret_10d": _f(features.get("ret_10d")),
        "ret_20d": _f(features.get("ret_20d")),
        "ret_60d": _f(features.get("ret_60d")),
        "eff_5d": _f(features.get("trend_efficiency_5d")),
        "eff_10d": _f(features.get("trend_efficiency_10d")),
        "eff_20d": _f(features.get("trend_efficiency_20d")),
        "eff_60d": _f(features.get("trend_efficiency_60d")),
        "volatility_10d": _f(features.get("volatility_10d")),
        "volatility_20d": _f(features.get("volatility_20d")),
        "range_10d": _realized_range(window, 10),
        "range_position_5d": _f(features.get("range_position_5d")),
        "range_position_10d": _f(features.get("range_position_10d")),
        "range_position_20d": _f(features.get("range_position_20d")),
        "prior_high_5d": _f(features.get("recent_high_5d")),
        "prior_low_5d": _f(features.get("recent_low_5d")),
        "prior_high_10d": _f(features.get("recent_high_10d")),
        "prior_low_10d": _f(features.get("recent_low_10d")),
        "prior_high_20d": _prior_high(window, BREAKOUT_LOOKBACK_DAYS),
        "prior_low_20d": _prior_low(window, BREAKOUT_LOOKBACK_DAYS),
    }
    return metrics


def _detect_current_trend_variant(
    window: pd.DataFrame,
    *,
    ret_key: str,
    ret_threshold: float,
    eff_key: str,
    eff_threshold: float,
    stack: str,
    slopes: str,
) -> str:
    if len(window) < 10:
        return "UNKNOWN"

    metrics = _metrics(window)
    if _trend_up_variant(metrics, ret_key, ret_threshold, eff_key, eff_threshold, stack, slopes):
        return "TREND_UP"
    if _trend_down_variant(metrics, ret_key, ret_threshold, eff_key, eff_threshold, stack, slopes):
        return "TREND_DOWN"
    return _current_non_trend_fallback(metrics)


def _trend_up_variant(
    metrics: dict[str, float | None],
    ret_key: str,
    ret_threshold: float,
    eff_key: str,
    eff_threshold: float,
    stack: str,
    slopes: str,
) -> bool:
    if _none(metrics.get(ret_key), metrics.get(eff_key)):
        return False
    return (
        _stack_up(metrics, stack)
        and _slopes_up(metrics, slopes)
        and metrics[ret_key] > ret_threshold
        and metrics[eff_key] > eff_threshold
    )


def _trend_down_variant(
    metrics: dict[str, float | None],
    ret_key: str,
    ret_threshold: float,
    eff_key: str,
    eff_threshold: float,
    stack: str,
    slopes: str,
) -> bool:
    if _none(metrics.get(ret_key), metrics.get(eff_key)):
        return False
    return (
        _stack_down(metrics, stack)
        and _slopes_down(metrics, slopes)
        and metrics[ret_key] < -ret_threshold
        and metrics[eff_key] > eff_threshold
    )


def _current_non_trend_fallback(metrics: dict[str, float | None]) -> str:
    ret_60d = metrics.get("ret_60d")
    ma20_slope = metrics.get("ma20_slope")
    ma50_slope = metrics.get("ma50_slope")
    volatility_20d = metrics.get("volatility_20d")
    trend_efficiency = metrics.get("eff_60d")
    range_position = metrics.get("range_position_20d")

    ret_is_small = ret_60d is not None and abs(ret_60d) <= 0.012
    slopes_flat = (
        ma20_slope is not None
        and ma50_slope is not None
        and abs(ma20_slope) <= 0.01
        and abs(ma50_slope) <= 0.01
    )
    oscillating_in_range = range_position is not None and 0.15 <= range_position <= 0.85
    volatility_moderate = volatility_20d is not None and volatility_20d <= 0.03
    trend_efficiency_low = trend_efficiency is not None and trend_efficiency <= 0.25
    volatility_high = volatility_20d is not None and volatility_20d >= 0.025

    if ret_is_small and trend_efficiency_low and volatility_high:
        return "CHOPPY"
    if ret_is_small and slopes_flat and oscillating_in_range and volatility_moderate:
        return "RANGE"
    if ret_is_small:
        return "RANGE"
    if trend_efficiency_low and volatility_high:
        return "CHOPPY"
    return "RANGE"


def _stack_up(metrics: dict[str, float | None], stack: str) -> bool:
    if stack == "ma20_ma50":
        return _all(metrics, "close", "ma20", "ma50") and metrics["close"] > metrics["ma20"] > metrics["ma50"]
    if stack == "ma10_ma20":
        return _all(metrics, "close", "ma10", "ma20") and metrics["close"] > metrics["ma10"] > metrics["ma20"]
    return False


def _stack_down(metrics: dict[str, float | None], stack: str) -> bool:
    if stack == "ma20_ma50":
        return _all(metrics, "close", "ma20", "ma50") and metrics["close"] < metrics["ma20"] < metrics["ma50"]
    if stack == "ma10_ma20":
        return _all(metrics, "close", "ma10", "ma20") and metrics["close"] < metrics["ma10"] < metrics["ma20"]
    return False


def _slopes_up(metrics: dict[str, float | None], slopes: str) -> bool:
    if slopes == "ma20_ma50":
        return _all(metrics, "ma20_slope", "ma50_slope") and metrics["ma20_slope"] > 0 and metrics["ma50_slope"] > 0
    if slopes == "ma20_ma50_flat_ok":
        return _all(metrics, "ma20_slope", "ma50_slope") and metrics["ma20_slope"] > 0 and metrics["ma50_slope"] >= 0
    if slopes == "ma20_only":
        return _all(metrics, "ma20_slope") and metrics["ma20_slope"] > 0
    if slopes == "ma10_ma20":
        return _all(metrics, "ma10d_slope", "ma20_slope") and metrics["ma10d_slope"] > 0 and metrics["ma20_slope"] > 0
    return False


def _slopes_down(metrics: dict[str, float | None], slopes: str) -> bool:
    if slopes == "ma20_ma50":
        return _all(metrics, "ma20_slope", "ma50_slope") and metrics["ma20_slope"] < 0 and metrics["ma50_slope"] < 0
    if slopes == "ma20_ma50_flat_ok":
        return _all(metrics, "ma20_slope", "ma50_slope") and metrics["ma20_slope"] < 0 and metrics["ma50_slope"] <= 0
    if slopes == "ma20_only":
        return _all(metrics, "ma20_slope") and metrics["ma20_slope"] < 0
    if slopes == "ma10_ma20":
        return _all(metrics, "ma10d_slope", "ma20_slope") and metrics["ma10d_slope"] < 0 and metrics["ma20_slope"] < 0
    return False


def _fast_up(metrics: dict[str, float | None]) -> bool:
    return any(
        [
            _all(metrics, "close", "ma10", "ma20", "ma10_slope", "ret_10d")
            and metrics["close"] > metrics["ma10"] > metrics["ma20"]
            and metrics["ma10_slope"] > 0
            and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD,
            _all(metrics, "ret_10d", "eff_10d")
            and metrics["ret_10d"] >= FAST_RET_10D_THRESHOLD * 1.5
            and metrics["eff_10d"] >= FAST_TREND_EFFICIENCY_10D_THRESHOLD,
            _all(metrics, "close", "prior_high_20d", "ret_5d")
            and metrics["close"] > metrics["prior_high_20d"] * (1.0 + BREAKOUT_BUFFER_PCT)
            and metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD,
        ]
    )


def _fast_down(metrics: dict[str, float | None]) -> bool:
    return any(
        [
            _all(metrics, "close", "ma10", "ma20", "ma10_slope", "ret_10d")
            and metrics["close"] < metrics["ma10"] < metrics["ma20"]
            and metrics["ma10_slope"] < 0
            and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD,
            _all(metrics, "ret_10d", "eff_10d")
            and metrics["ret_10d"] <= -FAST_RET_10D_THRESHOLD * 1.5
            and metrics["eff_10d"] >= FAST_TREND_EFFICIENCY_10D_THRESHOLD,
            _all(metrics, "close", "prior_low_20d", "ret_5d")
            and metrics["close"] < metrics["prior_low_20d"] * (1.0 - BREAKOUT_BUFFER_PCT)
            and metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD,
        ]
    )


def _recovery_up(metrics: dict[str, float | None]) -> bool:
    return (
        _all(metrics, "ret_5d", "close", "ma10", "range_position_20d")
        and metrics["ret_5d"] >= FAST_RET_5D_THRESHOLD * 2.0
        and metrics["close"] > metrics["ma10"]
        and metrics["range_position_20d"] >= 0.70
    )


def _selloff_down(metrics: dict[str, float | None]) -> bool:
    return (
        _all(metrics, "ret_5d", "close", "ma10", "range_position_20d")
        and metrics["ret_5d"] <= -FAST_RET_5D_THRESHOLD * 2.0
        and metrics["close"] < metrics["ma10"]
        and metrics["range_position_20d"] <= 0.30
    )


def _is_fast_choppy(metrics: dict[str, float | None]) -> bool:
    return (
        _all(metrics, "ret_10d", "eff_10d", "range_10d")
        and abs(metrics["ret_10d"]) <= FAST_RANGE_RETURN_10D_THRESHOLD
        and metrics["eff_10d"] <= FAST_CHOPPY_EFFICIENCY_10D_MAX
        and metrics["range_10d"] >= FAST_CHOPPY_RANGE_10D_THRESHOLD
    )


def _build_summary(df: pd.DataFrame, buffer_days: int) -> pd.DataFrame:
    comparable = df[df["hindsight_regime"].notna() & (df["hindsight_regime"] != "UNKNOWN")]
    lag2_trend = df[df["lagged_hindsight_trend_2d"].isin(TREND_REGIMES)]
    rows: list[dict[str, Any]] = []
    for experiment in EXPERIMENTS:
        buffer_col = f"{experiment.name}_buffer_match"
        lag2_col = f"{experiment.name}_lag2_trend_match"
        regime_col = f"{experiment.name}_regime"
        total = len(comparable)
        buffered_correct = int((comparable[buffer_col] == True).sum())
        lag2_total = len(lag2_trend)
        lag2_correct = int((lag2_trend[lag2_col] == True).sum()) if lag2_total else 0
        trend_signals = df[df[regime_col].isin(TREND_REGIMES)]
        trend_signal_count = len(trend_signals)
        row: dict[str, Any] = {
            "experiment": experiment.name,
            "buffer_days": buffer_days,
            "comparable_rows": total,
            "overall_buffered_correct": buffered_correct,
            "overall_buffered_accuracy_pct": round(100.0 * buffered_correct / total, 2) if total else 0.0,
            "lag2_trend_rows": lag2_total,
            "trend_signal_rows": trend_signal_count,
            "lag2_trend_match_count": lag2_correct,
            "lag2_trend_recall_pct": round(100.0 * lag2_correct / lag2_total, 2) if lag2_total else None,
            "trend_signal_lag2_precision_pct": round(100.0 * lag2_correct / trend_signal_count, 2) if trend_signal_count else None,
        }
        for regime in ["TREND_UP", "TREND_DOWN", "RANGE", "CHOPPY"]:
            regime_rows = comparable[comparable["hindsight_regime"] == regime]
            regime_total = len(regime_rows)
            regime_correct = int((regime_rows[buffer_col] == True).sum())
            row[f"{regime.lower()}_buffered_recall_pct"] = round(100.0 * regime_correct / regime_total, 2) if regime_total else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["lag2_trend_recall_pct", "trend_signal_lag2_precision_pct", "overall_buffered_accuracy_pct"],
        ascending=[False, False, False],
    )


def _exact_matches(detected: pd.Series, hindsight: pd.Series) -> list[bool | None]:
    matches: list[bool | None] = []
    for detected_regime, hindsight_regime in zip(detected, hindsight):
        if hindsight_regime == "UNKNOWN" or pd.isna(hindsight_regime):
            matches.append(None)
        else:
            matches.append(detected_regime == hindsight_regime)
    return matches


def _buffered_matches(detected: pd.Series, hindsight: pd.Series, buffer_days: int) -> list[bool | None]:
    matches: list[bool | None] = []
    hindsight_values = list(hindsight)
    for idx, detected_regime in enumerate(detected):
        hindsight_regime = hindsight_values[idx]
        if hindsight_regime == "UNKNOWN" or pd.isna(hindsight_regime):
            matches.append(None)
            continue
        start = max(0, idx - buffer_days)
        end = idx + 1
        nearby = {value for value in hindsight_values[start:end] if value != "UNKNOWN" and not pd.isna(value)}
        matches.append(detected_regime in nearby)
    return matches


def _lagged_hindsight_trend_label(current: Any, prev_1: Any, prev_2: Any) -> str | None:
    if current in TREND_REGIMES:
        return str(current)
    if prev_1 in TREND_REGIMES:
        return str(prev_1)
    if prev_2 in TREND_REGIMES:
        return str(prev_2)
    return None


def _lag2_trend_matches(detected: pd.Series, lagged_hindsight_trend: pd.Series) -> list[bool | None]:
    matches: list[bool | None] = []
    for detected_regime, hindsight_regime in zip(detected, lagged_hindsight_trend):
        if hindsight_regime not in TREND_REGIMES:
            matches.append(None)
        else:
            matches.append(detected_regime == hindsight_regime)
    return matches


def _realized_range(window: pd.DataFrame, lookback: int) -> float | None:
    if len(window) < lookback:
        return None
    recent = window.tail(lookback)
    close = float(window["close_price"].iloc[-1])
    if close == 0:
        return None
    return (float(recent["high_price"].max()) - float(recent["low_price"].min())) / close


def _prior_high(window: pd.DataFrame, lookback: int) -> float | None:
    if len(window) < lookback + 1:
        return None
    return float(window["high_price"].iloc[:-1].tail(lookback).max())


def _prior_low(window: pd.DataFrame, lookback: int) -> float | None:
    if len(window) < lookback + 1:
        return None
    return float(window["low_price"].iloc[:-1].tail(lookback).min())


def _f(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _none(*values: Any) -> bool:
    return any(value is None or pd.isna(value) for value in values)


def _all(metrics: dict[str, float | None], *keys: str) -> bool:
    return not _none(*(metrics.get(key) for key in keys))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NIFTY regime detector experiments against hindsight labels.")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    parser.add_argument("--start", default="2025-01-01", help="Start date YYYY-MM-DD. Default: 2025-01-01")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD. Default: today")
    parser.add_argument("--buffer-days", type=int, default=DEFAULT_BUFFER_DAYS, help="Regime match buffer in trading rows. Default: 2")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    result = run_regime_experiments(
        underlying=args.underlying.upper(),
        start_date=date.fromisoformat(args.start),
        end_date=end_date,
        output_dir=Path(args.output_dir),
        buffer_days=args.buffer_days,
    )
    print(result)


if __name__ == "__main__":
    main()
