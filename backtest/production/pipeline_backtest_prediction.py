from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from src.data_manager.underlying_history_reader import (
    fetch_5m_candles_for_dates,
    fetch_index_daily,
    get_db_connection,
)
from src.technical_analysis.prediction.underlying_registry import (
    DEFAULT_LOOKBACK_DAYS,
    detect_regime,
    load_underlying_prediction_strategies,
)

PRED_DIR = Path("output") / "backtest" / "NIFTY" / "production"
SIGNIFICANT_MOVE_THRESH = 0.01   # 1% — threshold for actual_move classification
PROFIT_TARGET_PCT = 0.01
STOP_LOSS_PCT = 0.005

METADATA_COLUMNS = {
    "underlying",
    "date",
    "today_volume",
    "next_date",
    "today_close",
    "next_open",
    "next_close",
    "next_volume",
    "max_high_price",
    "min_low_price",
    "actual_move",
    "max_delta_pct",
    "detected_regime",
    # internal — used by evaluate_prediction, not written to CSV
    "next_day_move_pct",
    "market_data_error",
}

# Fixed prefix columns written to every output CSV (in this order).
_FIXED_COLS = [
    "underlying",
    "date",
    "today_volume",
    "next_date",
    "today_close",
    "next_open",
    "next_close",
    "next_volume",
    "max_high_price",
    "min_low_price",
    "actual_move",
    "max_delta_pct",
]


@dataclass
class HistoricalUnderlyingBacktestRequest:
    underlying: str
    prediction_file: str | None = None
    prediction_dir: Path = PRED_DIR


def run_historical_underlying_backtest(request: HistoricalUnderlyingBacktestRequest) -> dict[str, Any]:
    underlying = request.underlying.strip().upper()
    path = resolve_prediction_file(request)
    predictions = pd.read_csv(path, parse_dates=["date"])
    if predictions.empty:
        return {
            "success": True,
            "underlying": underlying,
            "prediction_file": str(path),
            "rows": 0,
            "summary": {},
        }

    validate_prediction_file(predictions, path)
    strategy_columns = get_prediction_columns(predictions)
    enriched = ensure_backtest_columns(predictions, strategy_columns)

    market = load_market_context(underlying)
    for idx, row in enriched.iterrows():
        date_value = pd.to_datetime(row["date"]).normalize()
        row_market = market_for_date(market, date_value)
        for key, value in row_market.items():
            if key in enriched.columns:
                enriched.at[idx, key] = value
        if "detected_regime" in enriched.columns and pd.isna(enriched.at[idx, "detected_regime"]):
            enriched.at[idx, "detected_regime"] = regime_for_date(market, date_value)

        actual_move, max_delta = compute_actual_move_and_delta(row_market)
        enriched.at[idx, "actual_move"] = actual_move
        enriched.at[idx, "max_delta_pct"] = max_delta

        for column in strategy_columns:
            prediction = normalize_prediction(row.get(column))
            enriched.at[idx, f"{column}_result"] = evaluate_prediction(prediction, row_market, actual_move)

    enriched = _reorder_columns(enriched, strategy_columns)
    enriched.to_csv(path, index=False)
    return {
        "success": True,
        "underlying": underlying,
        "prediction_file": str(path),
        "rows": len(enriched),
        "strategies": strategy_columns,
        "summary": summarize_results(enriched, strategy_columns),
    }


def resolve_prediction_file(request: HistoricalUnderlyingBacktestRequest) -> Path:
    if request.prediction_file:
        path = Path(request.prediction_file)
        if path.is_absolute() or path.exists():
            return path
        return request.prediction_dir / path
    return request.prediction_dir / f"{request.underlying.strip().upper()}_prediction.csv"


def validate_prediction_file(predictions: pd.DataFrame, path: Path) -> None:
    required = {"date", "underlying"}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {', '.join(sorted(missing))}")


def get_prediction_columns(predictions: pd.DataFrame) -> list[str]:
    strategy_names = set(load_underlying_prediction_strategies().keys())
    columns = [column for column in predictions.columns if column in strategy_names]
    if "aggregate_decision" in predictions.columns:
        columns.append("aggregate_decision")
    return columns


def ensure_backtest_columns(predictions: pd.DataFrame, strategy_columns: list[str]) -> pd.DataFrame:
    out = predictions.copy()
    if "volume" in out.columns and "today_volume" not in out.columns:
        out["today_volume"] = out["volume"]
    if "volume" in out.columns:
        out = out.drop(columns=["volume"])
    if "regime" in out.columns and "detected_regime" not in out.columns:
        out["detected_regime"] = out["regime"]
    if "regime" in out.columns:
        out = out.drop(columns=["regime"])
    for column in ["today_volume", "next_date", "next_open", "next_close", "next_volume",
                   "max_high_price", "min_low_price", "actual_move", "max_delta_pct",
                   "detected_regime", "next_day_move_pct", "market_data_error"]:
        if column not in out.columns:
            out[column] = pd.NA
    for strategy in strategy_columns:
        col = f"{strategy}_result"
        if col not in out.columns:
            out[col] = pd.NA
    return out


def load_market_context(underlying: str) -> dict[str, Any]:
    conn = get_db_connection()
    try:
        daily = fetch_index_daily(conn, underlying=underlying, join_activity=False)
    finally:
        conn.close()

    if daily.empty:
        return {"daily": pd.DataFrame(), "daily_by_date": {}, "next_map": {}, "candles": {}}

    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.normalize()
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    daily["next_trade_date"] = daily["trade_date"].shift(-1)
    next_dates = [value for value in daily["next_trade_date"].dropna().unique()]
    candles = load_5m_ranges(underlying, next_dates)
    return {
        "daily": daily,
        "daily_by_date": daily.set_index("trade_date"),
        "next_map": daily.set_index("trade_date")["next_trade_date"],
        "candles": candles,
    }


def load_5m_ranges(underlying: str, dates: list[pd.Timestamp]) -> dict[pd.Timestamp, dict[str, float | None]]:
    if not dates:
        return {}
    conn = get_db_connection()
    try:
        candles_df = fetch_5m_candles_for_dates(conn, underlying=underlying, dates=dates)
    finally:
        conn.close()

    candles: dict[pd.Timestamp, dict[str, float | None]] = {}
    if candles_df.empty:
        return candles
    for _, row in candles_df.iterrows():
        trade_date = pd.to_datetime(row["trade_date"]).normalize()
        candles[trade_date] = {
            "max_high_price": float(row["max_high_price"]) if pd.notna(row["max_high_price"]) else None,
            "min_low_price": float(row["min_low_price"]) if pd.notna(row["min_low_price"]) else None,
        }
    return candles


def market_for_date(market: dict[str, Any], date_value: pd.Timestamp) -> dict[str, Any]:
    daily_by_date = market["daily_by_date"]
    next_map = market["next_map"]
    candles = market["candles"]
    if date_value not in daily_by_date.index:
        return empty_market_data(f"No daily data for {date_value.date().isoformat()}")

    next_date = next_map.get(date_value)
    today_close = float(daily_by_date.loc[date_value, "close_price"])
    today_volume = _optional_int(daily_by_date.loc[date_value].get("volume"))
    if pd.isna(next_date) or next_date not in daily_by_date.index:
        return {
            **empty_market_data("No next trading day available"),
            "today_close": today_close,
            "today_volume": today_volume,
        }

    next_row = daily_by_date.loc[next_date]
    next_open = float(next_row["open_price"])
    next_close = float(next_row["close_price"])
    next_volume = _optional_int(next_row.get("volume"))
    candle = candles.get(pd.to_datetime(next_date).normalize(), {})
    return {
        "today_close": today_close,
        "today_volume": today_volume,
        "next_date": pd.to_datetime(next_date).date().isoformat(),
        "next_open": next_open,
        "next_close": next_close,
        "next_volume": next_volume,
        # directional move from open to close — used by evaluate_prediction NO_POSITION
        "next_day_move_pct": (next_close - next_open) / next_open if next_open else None,
        "max_high_price": candle.get("max_high_price"),
        "min_low_price": candle.get("min_low_price"),
        "market_data_error": "",
    }


def regime_for_date(market: dict[str, Any], date_value: pd.Timestamp) -> str:
    daily = market.get("daily")
    if daily is None or daily.empty:
        return "UNKNOWN"
    history = daily[daily["trade_date"] <= date_value].copy()
    if history.empty:
        return "UNKNOWN"
    window = history.tail(DEFAULT_LOOKBACK_DAYS).copy()
    if len(window) < DEFAULT_LOOKBACK_DAYS:
        return "UNKNOWN"
    return detect_regime(window)


def empty_market_data(error: str) -> dict[str, Any]:
    return {
        "today_close": None,
        "today_volume": None,
        "next_date": None,
        "next_open": None,
        "next_close": None,
        "next_volume": None,
        "next_day_move_pct": None,
        "max_high_price": None,
        "min_low_price": None,
        "market_data_error": error,
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_actual_move_and_delta(market: dict[str, Any]) -> tuple[str, float | None]:
    """
    Classify the next day's actual move from the open price.

    CALL  — max_high crossed 1% above next_open  → delta = (max_high/next_open - 1)
    PUT   — min_low  crossed 1% below next_open  → delta = (1 - min_low/next_open)
    NO_POSITION — neither threshold was crossed   → delta = 0.0
    """
    next_open = market.get("next_open")
    max_high = market.get("max_high_price")
    min_low = market.get("min_low_price")
    if not next_open or max_high is None or min_low is None:
        return "NO_POSITION", None

    # CALL: high broke 1% above open AND low stayed within 0.5% of open (clean up day)
    if max_high > next_open * 1.01 and min_low > next_open * 0.995:
        return "CALL", round((max_high - next_open) / next_open * 100, 2)
    # PUT: low broke 1% below open AND high stayed within 0.5% of open (clean down day)
    if min_low < next_open * 0.99 and max_high < next_open * 1.005:
        return "PUT", round((next_open - min_low) / next_open * 100, 2)
    return "NO_POSITION", 0.0


def evaluate_prediction(prediction: str, market: dict[str, Any], actual_move: str | None = None) -> str:
    if market.get("market_data_error"):
        return "N/A"
    move = actual_move or compute_actual_move_and_delta(market)[0]
    if prediction == "NO_POSITION":
        if move == "NO_POSITION":
            return "OK_NO_TRADE"
        return "MISSED_CALL" if move == "CALL" else "MISSED_PUT"
    # prediction is CALL or PUT — correct only if it matches actual direction
    return "CORRECT" if prediction == move else "INCORRECT"


def predicted_max_delta(prediction: str, market: dict[str, Any]) -> float | None:
    next_open = market.get("next_open")
    if not next_open:
        return None
    if prediction == "CALL":
        max_high = market.get("max_high_price")
        return (max_high - next_open) / next_open if max_high is not None else None
    if prediction == "PUT":
        min_low = market.get("min_low_price")
        return (next_open - min_low) / next_open if min_low is not None else None
    if prediction == "NO_POSITION":
        max_high = market.get("max_high_price")
        min_low = market.get("min_low_price")
        deltas = []
        if max_high is not None:
            deltas.append((max_high - next_open) / next_open)
        if min_low is not None:
            deltas.append((next_open - min_low) / next_open)
        return max(deltas) if deltas else None
    return None


def normalize_prediction(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"CALL", "PUT", "NO_POSITION"} else "NO_POSITION"


def _reorder_columns(df: pd.DataFrame, strategy_columns: list[str]) -> pd.DataFrame:
    """
    Return df with columns in the canonical output order:
      fixed prefix | feature columns | regime/aggregate columns | strategy pairs
    Internal columns (next_day_move_pct, market_data_error) are dropped.
    """
    known_strategy_related = set(strategy_columns) | {f"{col}_result" for col in strategy_columns}
    indicator_columns = [
        c for c in df.columns
        if c not in _FIXED_COLS
        and c not in known_strategy_related
        and c not in {"aggregate_decision", "aggregate_decision_result", "detected_regime"}
        and c not in {"next_day_move_pct", "market_data_error"}
    ]

    ordered = [c for c in _FIXED_COLS if c in df.columns]
    ordered += [c for c in indicator_columns if c not in ordered]
    if "detected_regime" in df.columns and "detected_regime" not in ordered:
        ordered.append("detected_regime")
    if "aggregate_decision" in strategy_columns:
        ordered += [c for c in ["aggregate_decision", "aggregate_decision_result"] if c in df.columns]
    for col in sorted(strategy_columns):
        if col == "aggregate_decision":
            continue
        ordered += [c for c in [col, f"{col}_result"] if c in df.columns]
    return df[ordered]

def summarize_results(predictions: pd.DataFrame, strategy_columns: list[str]) -> dict[str, Any]:
    primary_column = "aggregate_decision" if "aggregate_decision" in strategy_columns else (
        strategy_columns[0] if strategy_columns else None
    )
    by_prediction_column: dict[str, Any] = {}
    for strategy in strategy_columns:
        if f"{strategy}_result" not in predictions.columns:
            continue
        by_prediction_column[strategy] = summarize_prediction_column(predictions, strategy)

    headline = by_prediction_column.get(primary_column, empty_summary()) if primary_column else empty_summary()
    return {
        **headline,
        "primary_prediction_column": primary_column,
        "by_prediction_column": by_prediction_column,
    }


def summarize_prediction_column(predictions: pd.DataFrame, prediction_column: str) -> dict[str, Any]:
    result_col = f"{prediction_column}_result"
    valid_market = predictions[
        predictions["next_open"].notna()
        & predictions["max_high_price"].notna()
        & predictions["min_low_price"].notna()
    ].copy()
    actionable = valid_market[valid_market[prediction_column].isin(["CALL", "PUT"])]

    profit_hits = int(
        sum(
            prediction_profit_hit(row[prediction_column], row)
            for _, row in actionable.iterrows()
        )
    )
    stop_hits = int(
        sum(
            prediction_stop_hit(row[prediction_column], row)
            for _, row in actionable.iterrows()
        )
    )
    directionally_correct = int(
        sum(
            prediction_directionally_correct(row[prediction_column], row)
            for _, row in actionable.iterrows()
        )
    )
    # recall = % of actionable predictions where the predicted direction matched actual_move
    correctly_directed = int(
        sum(
            1
            for _, row in actionable.iterrows()
            if str(row.get("actual_move", "")).strip().upper() == str(row[prediction_column]).strip().upper()
        )
    )
    actionable_count = int(len(actionable))
    return {
        "days_backtested": int(len(valid_market)),
        "actionable_predictions": actionable_count,
        "profit_hits": profit_hits,
        "stop_hits": stop_hits,
        "profit_hit_rate_pct": round(profit_hits / actionable_count * 100.0, 2) if actionable_count else 0.0,
        "stop_hit_rate_pct": round(stop_hits / actionable_count * 100.0, 2) if actionable_count else 0.0,
        "accuracy_pct": round(directionally_correct / actionable_count * 100.0, 2) if actionable_count else 0.0,
        "recall_pct": round(correctly_directed / actionable_count * 100.0, 2) if actionable_count else 0.0,
        "correctly_directed": correctly_directed,
        "by_result": valid_market[result_col].value_counts(dropna=False).to_dict() if result_col in valid_market else {},
    }


def prediction_profit_hit(prediction: Any, row: pd.Series) -> bool:
    next_open = row.get("next_open")
    if not has_price(next_open):
        return False
    if prediction == "CALL":
        max_high = row.get("max_high_price")
        return has_price(max_high) and ((max_high - next_open) / next_open) >= PROFIT_TARGET_PCT
    if prediction == "PUT":
        min_low = row.get("min_low_price")
        return has_price(min_low) and ((next_open - min_low) / next_open) >= PROFIT_TARGET_PCT
    return False


def prediction_directionally_correct(prediction: Any, row: pd.Series) -> bool:
    if prediction == "CALL":
        delta = predicted_max_delta("CALL", row)
        return delta is not None and delta > 0
    if prediction == "PUT":
        delta = predicted_max_delta("PUT", row)
        return delta is not None and delta > 0
    return False


def prediction_stop_hit(prediction: Any, row: pd.Series) -> bool:
    next_open = row.get("next_open")
    if not has_price(next_open):
        return False
    if prediction == "CALL":
        min_low = row.get("min_low_price")
        return has_price(min_low) and ((next_open - min_low) / next_open) >= STOP_LOSS_PCT
    if prediction == "PUT":
        max_high = row.get("max_high_price")
        return has_price(max_high) and ((max_high - next_open) / next_open) >= STOP_LOSS_PCT
    return False


def actual_profit_threshold_move(row: pd.Series) -> bool:
    next_open = row.get("next_open")
    max_high = row.get("max_high_price")
    min_low = row.get("min_low_price")
    if not has_price(next_open) or not has_price(max_high) or not has_price(min_low):
        return False
    call_move = (max_high - next_open) / next_open
    put_move = (next_open - min_low) / next_open
    return max(call_move, put_move) >= PROFIT_TARGET_PCT


def has_price(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def empty_summary() -> dict[str, Any]:
    return {
        "days_backtested": 0,
        "actionable_predictions": 0,
        "profit_hits": 0,
        "stop_hits": 0,
        "profit_hit_rate_pct": 0.0,
        "stop_hit_rate_pct": 0.0,
        "accuracy_pct": 0.0,
        "recall_pct": 0.0,
        "correctly_directed": 0,
        "by_result": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest consolidated historical prediction CSV for one underlying.")
    parser.add_argument("-u", "--underlying", required=True, help="Underlying stock or index symbol.")
    parser.add_argument("--prediction-file", default=None, help="Optional CSV file. Defaults to output/backtest/<UNDERLYING>_prediction.csv")
    args = parser.parse_args()

    result = run_historical_underlying_backtest(
        HistoricalUnderlyingBacktestRequest(
            underlying=args.underlying,
            prediction_file=args.prediction_file,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
