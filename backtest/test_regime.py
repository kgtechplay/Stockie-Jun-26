"""
NIFTY regime detector experiments.

Script mode:
    python backtest/test_regime.py
    python backtest/test_regime.py --underlying NIFTY --start 2025-01-01 --end 2026-06-17

Outputs:
    output/backtest/NIFTY_regime_experiment_comparison.csv
    output/backtest/NIFTY_regime_experiment_summary.csv
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

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.technical_analysis.prediction.features import (
    compute_return,
    compute_trend_efficiency,
    compute_underlying_features,
)
from src.technical_analysis.prediction.highsight_regime import compute_hindsight_regime
from src.technical_analysis.prediction.regime import detect_regime

DEFAULT_OUTPUT_DIR = Path("output") / "backtest"
DEFAULT_LOOKBACK_DAYS = 140
DEFAULT_BUFFER_DAYS = 2

FAST_RET_5D_THRESHOLD = 0.0075
FAST_RET_10D_THRESHOLD = 0.01
FAST_RET_20D_THRESHOLD = 0.015
FAST_TREND_EFFICIENCY_10D_THRESHOLD = 0.35
FAST_RANGE_RETURN_10D_THRESHOLD = 0.0075
FAST_CHOPPY_EFFICIENCY_10D_MAX = 0.25
FAST_CHOPPY_RANGE_10D_THRESHOLD = 0.025
BREAKOUT_LOOKBACK_DAYS = 20
BREAKOUT_BUFFER_PCT = 0.002

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
    RegimeExperiment("fast_return_10_20", detect_fast_return_10_20),
    RegimeExperiment("fast_ma10_ma20", detect_fast_ma10_ma20),
    RegimeExperiment("breakout_20d", detect_breakout_20d),
    RegimeExperiment("fast_combo_v1", detect_fast_combo_v1),
    RegimeExperiment("fast_combo_v2", detect_fast_combo_v2),
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
            "close": float(ohlcv_row["close_price"]),
        }
        row.update(hindsight)
        for experiment in EXPERIMENTS:
            row[f"{experiment.name}_regime"] = experiment.detect(window)
        rows.append(row)

    out_df = pd.DataFrame(rows)
    for experiment in EXPERIMENTS:
        regime_col = f"{experiment.name}_regime"
        out_df[f"{experiment.name}_exact_match"] = _exact_matches(out_df[regime_col], out_df["hindsight_regime"])
        out_df[f"{experiment.name}_buffer_match"] = _buffered_matches(
            out_df[regime_col],
            out_df["hindsight_regime"],
            buffer_days,
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
            "Best buffered accuracy: "
            f"{winner['experiment']} {winner['buffered_accuracy_pct']:.1f}% "
            f"({int(winner['buffered_correct'])}/{int(winner['comparable_rows'])})"
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
    return {
        "close": float(closes.iloc[-1]) if len(closes) else None,
        "ma10": _f(features.get("ma10")),
        "ma20": _f(features.get("ma20")),
        "ma50": _f(features.get("ma50")),
        "ma10_slope": _ma_slope(closes, 10),
        "ma20_slope": _f(features.get("ma20_slope")),
        "ma50_slope": _f(features.get("ma50_slope")),
        "ret_5d": compute_return(closes, 5),
        "ret_10d": compute_return(closes, 10),
        "ret_20d": compute_return(closes, 20),
        "eff_10d": compute_trend_efficiency(closes, 10),
        "eff_20d": compute_trend_efficiency(closes, 20),
        "range_10d": _realized_range(window, 10),
        "range_position_20d": _f(features.get("range_position_20d")),
        "prior_high_20d": _prior_high(window, BREAKOUT_LOOKBACK_DAYS),
        "prior_low_20d": _prior_low(window, BREAKOUT_LOOKBACK_DAYS),
    }


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
    rows: list[dict[str, Any]] = []
    for experiment in EXPERIMENTS:
        exact_col = f"{experiment.name}_exact_match"
        buffer_col = f"{experiment.name}_buffer_match"
        total = len(comparable)
        exact_correct = int((comparable[exact_col] == True).sum())
        buffered_correct = int((comparable[buffer_col] == True).sum())
        row: dict[str, Any] = {
            "experiment": experiment.name,
            "buffer_days": buffer_days,
            "comparable_rows": total,
            "exact_correct": exact_correct,
            "exact_accuracy_pct": round(100.0 * exact_correct / total, 2) if total else 0.0,
            "buffered_correct": buffered_correct,
            "buffered_accuracy_pct": round(100.0 * buffered_correct / total, 2) if total else 0.0,
        }
        for regime in ["TREND_UP", "TREND_DOWN", "RANGE", "CHOPPY"]:
            regime_rows = comparable[comparable["hindsight_regime"] == regime]
            regime_total = len(regime_rows)
            regime_correct = int((regime_rows[buffer_col] == True).sum())
            row[f"{regime.lower()}_buffered_recall_pct"] = round(100.0 * regime_correct / regime_total, 2) if regime_total else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["buffered_accuracy_pct", "exact_accuracy_pct"],
        ascending=[False, False],
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


def _ma_slope(closes: pd.Series, window: int, periods: int = 3) -> float | None:
    if len(closes) < window + periods:
        return None
    ma = closes.rolling(window).mean()
    previous = float(ma.iloc[-(periods + 1)])
    if previous == 0:
        return None
    return float(ma.iloc[-1]) / previous - 1.0


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