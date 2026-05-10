from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
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

OUTPUT_DIR = Path("output")
SIGNIFICANT_MOVE_THRESH = 0.01
MATRIX_METADATA_COLUMNS = {
    "reference_date",
    "instrument",
    "status",
    "error",
}


@dataclass
class WatchedBacktestRequest:
    reference_date: date
    output_dir: Path = OUTPUT_DIR
    prediction_file: str | None = None
    strategies: list[str] | None = None


def run_watched_underlying_backtest(request: WatchedBacktestRequest) -> dict[str, Any]:
    prediction_path = resolve_prediction_file(request)
    predictions = load_prediction_matrix(request)
    strategy_columns = select_strategy_columns(predictions, request.strategies)

    result_records: list[dict[str, Any]] = []
    enriched = predictions.copy()
    for idx, row in predictions.iterrows():
        instrument = str(row["instrument"]).upper()
        market = load_next_day_market_data(instrument, request.reference_date)
        add_market_columns(enriched, idx, market)

        for strategy in strategy_columns:
            prediction = normalize_prediction(row.get(strategy))
            result = evaluate_prediction(prediction, market)
            enriched.at[idx, f"{strategy}_backtest_result"] = result
            result_records.append(
                {
                    "reference_date": request.reference_date.isoformat(),
                    "instrument": instrument,
                    "strategy": strategy,
                    "prediction": prediction,
                    **market,
                    "result": result,
                }
            )

    enriched.to_csv(prediction_path, index=False)
    results_df = pd.DataFrame(result_records)

    return {
        "success": True,
        "reference_date": request.reference_date.isoformat(),
        "prediction_file": str(prediction_path),
        "output_file": str(prediction_path),
        "rows": len(results_df),
        "summary": summarize_results(results_df),
    }


def load_prediction_matrix(request: WatchedBacktestRequest) -> pd.DataFrame:
    prediction_path = resolve_prediction_file(request)
    if prediction_path.suffix.lower() != ".csv":
        raise ValueError(f"Watched backtest expects a CSV prediction file: {prediction_path}")
    df = pd.read_csv(prediction_path)

    if df.empty:
        raise ValueError(f"Prediction file is empty: {prediction_path}")
    if "instrument" not in df.columns:
        raise ValueError(f"Prediction file must contain an instrument column: {prediction_path}")
    return df


def resolve_prediction_file(request: WatchedBacktestRequest) -> Path:
    if request.prediction_file:
        path = Path(request.prediction_file)
        return path if path.is_absolute() else request.output_dir / path

    csv_path = request.output_dir / f"{request.reference_date.isoformat()}.csv"
    if csv_path.exists():
        return csv_path

    raise FileNotFoundError(f"No CSV prediction file found for {request.reference_date} in {request.output_dir}")


def select_strategy_columns(predictions: pd.DataFrame, requested: list[str] | None) -> list[str]:
    if requested:
        missing = [name for name in requested if name not in predictions.columns]
        if missing:
            raise ValueError(f"Requested strategy columns missing from prediction file: {', '.join(missing)}")
        return requested

    strategy_columns = [
        col
        for col in predictions.columns
        if col not in MATRIX_METADATA_COLUMNS and not col.endswith("_backtest_result")
    ]
    return [col for col in strategy_columns if predictions[col].isin(["CALL", "PUT", "NO_POSITION"]).any()]


def add_market_columns(predictions: pd.DataFrame, idx: int, market: dict[str, Any]) -> None:
    for column in [
        "today_close_1515",
        "next_date",
        "next_open_0915",
        "next_close_1515",
        "next_day_move_pct",
        "max_high_price",
        "min_low_price",
        "market_data_error",
    ]:
        predictions.at[idx, column] = market.get(column)


def load_next_day_market_data(instrument: str, reference_date: date) -> dict[str, Any]:
    conn = get_db_connection()
    try:
        daily = fetch_index_daily(
            conn,
            underlying=instrument,
            start_date=reference_date.isoformat(),
            join_activity=False,
        )
    finally:
        conn.close()

    if daily.empty:
        return empty_market_data(error=f"No underlying daily data for {instrument} on or after {reference_date}")

    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.normalize()
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    ref_ts = pd.Timestamp(reference_date).normalize()
    ref_rows = daily[daily["trade_date"] == ref_ts]
    if ref_rows.empty:
        return empty_market_data(error=f"No underlying daily row for {instrument} on {reference_date}")

    ref_index = int(ref_rows.index[0])
    if ref_index + 1 >= len(daily):
        return empty_market_data(error=f"No next trading day available for {instrument} after {reference_date}")

    today = daily.loc[ref_index]
    next_day = daily.loc[ref_index + 1]
    today_close = float(today["close_price"])
    next_open = float(next_day["open_price"])
    next_close = float(next_day["close_price"])
    next_date = pd.to_datetime(next_day["trade_date"]).normalize()

    candle = load_next_day_5m_range(instrument, next_date)
    return {
        "today_close_1515": today_close,
        "next_date": next_date.date().isoformat(),
        "next_open_0915": next_open,
        "next_close_1515": next_close,
        "next_day_move_pct": (next_open - today_close) / today_close if today_close else None,
        "max_high_price": candle.get("max_high_price"),
        "min_low_price": candle.get("min_low_price"),
        "market_data_error": "",
    }


def load_next_day_5m_range(instrument: str, next_date: pd.Timestamp) -> dict[str, float | None]:
    conn = get_db_connection()
    try:
        candles = fetch_5m_candles_for_dates(conn, underlying=instrument, dates=[next_date])
    finally:
        conn.close()

    if candles.empty:
        return {"max_high_price": None, "min_low_price": None}

    row = candles.iloc[0]
    return {
        "max_high_price": float(row["max_high_price"]) if pd.notna(row["max_high_price"]) else None,
        "min_low_price": float(row["min_low_price"]) if pd.notna(row["min_low_price"]) else None,
    }


def empty_market_data(error: str) -> dict[str, Any]:
    return {
        "today_close_1515": None,
        "next_date": None,
        "next_open_0915": None,
        "next_close_1515": None,
        "next_day_move_pct": None,
        "max_high_price": None,
        "min_low_price": None,
        "market_data_error": error,
    }


def evaluate_prediction(prediction: str, market: dict[str, Any]) -> str:
    if market.get("market_data_error"):
        return "N/A"

    today_close = market["today_close_1515"]
    max_high = market["max_high_price"]
    min_low = market["min_low_price"]
    next_day_move_pct = market["next_day_move_pct"]

    if prediction == "CALL":
        if max_high is None or today_close is None:
            return "N/A"
        return "CORRECT" if max_high > today_close else "INCORRECT"

    if prediction == "PUT":
        if min_low is None or today_close is None:
            return "N/A"
        return "CORRECT" if min_low < today_close else "INCORRECT"

    if prediction == "NO_POSITION":
        if next_day_move_pct is None or abs(next_day_move_pct) < SIGNIFICANT_MOVE_THRESH:
            return "OK_NO_TRADE"
        return "MISSED_CALL" if next_day_move_pct > 0 else "MISSED_PUT"

    return "N/A"


def normalize_prediction(value: Any) -> str:
    text = str(value).strip().upper()
    return text if text in {"CALL", "PUT", "NO_POSITION"} else "NO_POSITION"


def summarize_results(results_df: pd.DataFrame) -> dict[str, Any]:
    if results_df.empty:
        return {
            "total": 0,
            "actionable": 0,
            "correct": 0,
            "accuracy_pct": 0.0,
        }

    actionable = results_df[results_df["prediction"].isin(["CALL", "PUT"])]
    correct = actionable[actionable["result"] == "CORRECT"]
    accuracy = (len(correct) / len(actionable) * 100.0) if len(actionable) else 0.0

    return {
        "total": int(len(results_df)),
        "actionable": int(len(actionable)),
        "correct": int(len(correct)),
        "accuracy_pct": round(accuracy, 2),
        "by_result": results_df["result"].value_counts(dropna=False).to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest watched prediction matrix for one reference date.")
    parser.add_argument("--reference-date", required=True, help="Reference date, YYYY-MM-DD")
    parser.add_argument("--prediction-file", default=None, help="Optional prediction CSV file under output/")
    parser.add_argument("--strategy", action="append", dest="strategies", help="Strategy column to backtest. Repeatable.")
    args = parser.parse_args()

    result = run_watched_underlying_backtest(
        WatchedBacktestRequest(
            reference_date=pd.to_datetime(args.reference_date).date(),
            prediction_file=args.prediction_file,
            strategies=args.strategies,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
