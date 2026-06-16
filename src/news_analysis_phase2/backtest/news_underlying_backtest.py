from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from src.data_manager.underlying_history_reader import fetch_index_daily, get_db_connection

OUTPUT_DIR = Path("output")
EVENT_WINDOWS = [1, 3, 5, 10, 20, 60, 120]
PROFIT_TARGET_PCT = 0.03
STOP_LOSS_PCT = 0.02


@dataclass
class NewsBacktestRequest:
    reference_date: date | None = None
    output_dir: Path = OUTPUT_DIR
    prediction_file: str | None = None
    strategies: list[str] | None = None
    signal_journal_file: str | None = None
    published_date: date | None = None
    news_event_id: str | None = None
    force: bool = False


def run_news_underlying_backtest(request: NewsBacktestRequest) -> dict[str, Any]:
    if request.signal_journal_file:
        return run_signal_journal_backtest(request)
    return run_prediction_matrix_backtest(request)


def run_signal_journal_backtest(request: NewsBacktestRequest) -> dict[str, Any]:
    journal_path = resolve_signal_journal_file(request)
    signals = pd.read_csv(journal_path)
    if signals.empty:
        return empty_result(journal_path, reason="Signal journal is empty.")
    required = {
        "signal_id",
        "ticker",
        "entry_allowed_from",
        "expected_stock_direction",
        "suggested_max_holding_days",
        "signal_status",
    }
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Signal journal missing required columns: {', '.join(sorted(missing))}")

    signals_to_process = filter_signal_journal(signals, request)
    enriched = signals.copy()
    result_records: list[dict[str, Any]] = []
    for idx, signal in signals_to_process.iterrows():
        if str(signal.get("signal_status", "")).strip().lower() != "approved":
            enriched.at[idx, "backtest_status"] = "SKIPPED_MONITOR_ONLY"
            continue
        if not request.force and is_already_backtested(signal):
            continue

        result = backtest_signal(signal)
        for key, value in result.items():
            enriched.at[idx, key] = value
        result_records.append(
            {
                "signal_id": signal["signal_id"],
                "ticker": signal["ticker"],
                "expected_stock_direction": signal["expected_stock_direction"],
                **result,
            }
        )

    enriched.to_csv(journal_path, index=False)
    results_df = pd.DataFrame(result_records)
    return {
        "success": True,
        "mode": "signal_journal",
        "signal_journal_file": str(journal_path),
        "output_file": str(journal_path),
        "rows": len(results_df),
        "filtered_rows": len(signals_to_process),
        "skipped_already_backtested": count_already_backtested(signals_to_process) if not request.force else 0,
        "filters": {
            "published_date": request.published_date.isoformat() if request.published_date else None,
            "news_event_id": request.news_event_id,
        },
        "summary": summarize_signal_results(results_df),
    }


def backtest_signal(signal: pd.Series) -> dict[str, Any]:
    ticker = str(signal["ticker"]).upper()
    entry_time = pd.to_datetime(signal["entry_allowed_from"])
    direction = normalize_signal_direction(signal["expected_stock_direction"])
    holding_days = int(float(signal.get("suggested_max_holding_days") or 1))

    daily = load_daily_market_data(ticker, entry_time.date())
    if daily.empty:
        return {"backtest_status": "INSUFFICIENT_DATA", "market_data_error": "No daily market data."}

    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.normalize()
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    entry_date = pd.Timestamp(entry_time.date()).normalize()
    candidates = daily[daily["trade_date"] >= entry_date]
    if candidates.empty:
        return {"backtest_status": "INSUFFICIENT_DATA", "market_data_error": "No row on or after entry date."}

    entry_idx = int(candidates.index[0])
    entry_row = daily.loc[entry_idx]
    entry_price = float(entry_row["open_price"] if pd.notna(entry_row["open_price"]) else entry_row["close_price"])
    window_daily = daily.iloc[entry_idx : entry_idx + max(holding_days, max(EVENT_WINDOWS)) + 1].copy()

    result: dict[str, Any] = {
        "backtest_status": "OK",
        "market_data_error": "",
        "entry_trade_date": pd.to_datetime(entry_row["trade_date"]).date().isoformat(),
        "entry_price": entry_price,
    }
    result.update(event_study_results(window_daily, entry_price, direction))
    result.update(triple_barrier_result(window_daily, entry_price, direction, holding_days))
    result.update(mfe_mae_result(window_daily, entry_price, direction))
    return result


def event_study_results(daily: pd.DataFrame, entry_price: float, direction: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in EVENT_WINDOWS:
        if horizon >= len(daily):
            result[f"event_{horizon}d_return"] = None
            result[f"event_{horizon}d_correct"] = None
            continue
        exit_row = daily.iloc[horizon]
        exit_price = float(exit_row["close_price"])
        stock_return = (exit_price - entry_price) / entry_price if entry_price else None
        if stock_return is None:
            correct = None
        elif direction == "up":
            correct = stock_return > 0
        else:
            correct = stock_return < 0
        result[f"event_{horizon}d_return"] = round(stock_return, 6) if stock_return is not None else None
        result[f"event_{horizon}d_correct"] = correct
    return result


def triple_barrier_result(
    daily: pd.DataFrame,
    entry_price: float,
    direction: str,
    holding_days: int,
) -> dict[str, Any]:
    if daily.empty or not entry_price:
        return {"triple_barrier_label": "insufficient_data"}

    if direction == "up":
        profit_price = entry_price * (1 + PROFIT_TARGET_PCT)
        stop_price = entry_price * (1 - STOP_LOSS_PCT)
    else:
        profit_price = entry_price * (1 - PROFIT_TARGET_PCT)
        stop_price = entry_price * (1 + STOP_LOSS_PCT)

    evaluation = daily.iloc[: holding_days + 1]
    for _, row in evaluation.iterrows():
        high = float(row["high_price"])
        low = float(row["low_price"])
        if direction == "up":
            if high >= profit_price:
                return _barrier_payload("profit_hit", row, entry_price, profit_price, stop_price, direction)
            if low <= stop_price:
                return _barrier_payload("stop_hit", row, entry_price, profit_price, stop_price, direction)
        else:
            if low <= profit_price:
                return _barrier_payload("profit_hit", row, entry_price, profit_price, stop_price, direction)
            if high >= stop_price:
                return _barrier_payload("stop_hit", row, entry_price, profit_price, stop_price, direction)

    exit_row = evaluation.iloc[-1]
    exit_price = float(exit_row["close_price"])
    realized_return = directional_return(entry_price, exit_price, direction)
    if realized_return > 0:
        label = "vertical_timeout_positive"
    elif realized_return < 0:
        label = "vertical_timeout_negative"
    else:
        label = "vertical_timeout_flat"
    return _barrier_payload(label, exit_row, entry_price, profit_price, stop_price, direction)


def mfe_mae_result(daily: pd.DataFrame, entry_price: float, direction: str) -> dict[str, Any]:
    if daily.empty or not entry_price:
        return {
            "max_favorable_return": None,
            "max_adverse_return": None,
            "days_to_peak": None,
            "days_to_worst": None,
        }

    favorable: list[float] = []
    adverse: list[float] = []
    for _, row in daily.iterrows():
        high = float(row["high_price"])
        low = float(row["low_price"])
        if direction == "up":
            favorable.append((high - entry_price) / entry_price)
            adverse.append((low - entry_price) / entry_price)
        else:
            favorable.append((entry_price - low) / entry_price)
            adverse.append((entry_price - high) / entry_price)

    peak_idx = int(pd.Series(favorable).idxmax())
    worst_idx = int(pd.Series(adverse).idxmin())
    return {
        "max_favorable_return": round(float(favorable[peak_idx]), 6),
        "max_adverse_return": round(float(adverse[worst_idx]), 6),
        "days_to_peak": peak_idx,
        "days_to_worst": worst_idx,
    }


def run_prediction_matrix_backtest(request: NewsBacktestRequest) -> dict[str, Any]:
    prediction_path = resolve_prediction_file(request)
    predictions = pd.read_csv(prediction_path)
    if predictions.empty:
        return empty_result(prediction_path, reason="Prediction file is empty.")
    if "instrument" not in predictions.columns:
        raise ValueError(f"Prediction file must contain an instrument column: {prediction_path}")
    return {
        "success": False,
        "mode": "prediction_matrix",
        "reason": "Prediction-matrix watched backtest has been superseded by signal-journal backtesting.",
        "prediction_file": str(prediction_path),
        "rows": len(predictions),
    }


def filter_signal_journal(signals: pd.DataFrame, request: NewsBacktestRequest) -> pd.DataFrame:
    filtered = signals
    if request.published_date:
        if "published_at" not in filtered.columns:
            raise ValueError("published_at column is required when filtering by published_date")
        published = pd.to_datetime(filtered["published_at"], errors="coerce").dt.date
        filtered = filtered[published == request.published_date]
    if request.news_event_id:
        if "news_event_id" not in filtered.columns:
            raise ValueError("news_event_id column is required when filtering by news_event_id")
        filtered = filtered[filtered["news_event_id"].astype(str) == str(request.news_event_id)]
    return filtered


def is_already_backtested(signal: pd.Series) -> bool:
    status = str(signal.get("backtest_status") or "").strip()
    if not status or status.lower() == "nan":
        return False
    return any(
        has_value(signal.get(column))
        for column in [
            "triple_barrier_label",
            "directionally_correct",
            "event_1d_correct",
            "realized_return",
        ]
    )


def count_already_backtested(signals: pd.DataFrame) -> int:
    if signals.empty or "backtest_status" not in signals.columns:
        return 0
    return int(sum(is_already_backtested(row) for _, row in signals.iterrows()))


def has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    return str(value).strip() != ""


def load_daily_market_data(ticker: str, start_date: date) -> pd.DataFrame:
    conn = get_db_connection()
    try:
        return fetch_index_daily(
            conn,
            underlying=ticker,
            start_date=start_date.isoformat(),
            join_activity=False,
        )
    finally:
        conn.close()


def resolve_signal_journal_file(request: NewsBacktestRequest) -> Path:
    path = Path(str(request.signal_journal_file))
    return path if path.is_absolute() else request.output_dir / path


def resolve_prediction_file(request: NewsBacktestRequest) -> Path:
    if request.prediction_file:
        path = Path(request.prediction_file)
        return path if path.is_absolute() else request.output_dir / path
    if not request.reference_date:
        raise ValueError("reference_date is required when prediction_file is not supplied")
    return request.output_dir / f"{request.reference_date.isoformat()}.csv"


def normalize_signal_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"call", "long", "up"}:
        return "up"
    if text in {"put", "short", "down"}:
        return "down"
    return "up"


def directional_return(entry_price: float, exit_price: float, direction: str) -> float:
    raw = (exit_price - entry_price) / entry_price if entry_price else 0.0
    return raw if direction == "up" else -raw


def summarize_signal_results(results_df: pd.DataFrame) -> dict[str, Any]:
    if results_df.empty:
        return {
            "approved_signals_backtested": 0,
            "profit_hit_rate_pct": 0.0,
            "stop_hit_rate_pct": 0.0,
        }

    total = len(results_df)
    profit_hits = int((results_df["triple_barrier_label"] == "profit_hit").sum())
    stop_hits = int((results_df["triple_barrier_label"] == "stop_hit").sum())
    return {
        "approved_signals_backtested": total,
        "profit_hits": profit_hits,
        "stop_hits": stop_hits,
        "profit_hit_rate_pct": round(profit_hits / total * 100.0, 2),
        "stop_hit_rate_pct": round(stop_hits / total * 100.0, 2),
        "by_triple_barrier_label": results_df["triple_barrier_label"].value_counts(dropna=False).to_dict(),
    }


def empty_result(path: Path, reason: str) -> dict[str, Any]:
    return {
        "success": True,
        "reason": reason,
        "output_file": str(path),
        "rows": 0,
        "summary": summarize_signal_results(pd.DataFrame()),
    }


def _barrier_payload(
    label: str,
    row: pd.Series,
    entry_price: float,
    profit_price: float,
    stop_price: float,
    direction: str,
) -> dict[str, Any]:
    exit_price = float(row["close_price"])
    realized_return = directional_return(entry_price, exit_price, direction)
    return {
        "triple_barrier_label": label,
        "triple_barrier_exit_date": pd.to_datetime(row["trade_date"]).date().isoformat(),
        "triple_barrier_exit_price": exit_price,
        "profit_target_price": round(profit_price, 4),
        "stop_loss_price": round(stop_price, 4),
        "realized_return": round(realized_return, 6),
        "directionally_correct": realized_return > 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest news-analysis signals or legacy prediction matrix.")
    parser.add_argument("--reference-date", default=None, help="Reference date for legacy matrix mode, YYYY-MM-DD")
    parser.add_argument("--prediction-file", default=None, help="Optional legacy prediction CSV file under output/")
    parser.add_argument("--signal-journal-file", default=None, help="Signal journal CSV path. Defaults to output/trade_signal_journal.csv")
    parser.add_argument("--published-date", default=None, help="Only backtest signals whose published_at date matches YYYY-MM-DD.")
    parser.add_argument("--news-event-id", default=None, help="Only backtest signals for this news_event_id.")
    parser.add_argument("--force", action="store_true", help="Re-run backtest for rows that already have backtest results.")
    args = parser.parse_args()

    result = run_news_underlying_backtest(
        NewsBacktestRequest(
            reference_date=pd.to_datetime(args.reference_date).date() if args.reference_date else None,
            prediction_file=args.prediction_file,
            signal_journal_file=args.signal_journal_file or "trade_signal_journal.csv",
            published_date=pd.to_datetime(args.published_date).date() if args.published_date else None,
            news_event_id=args.news_event_id,
            force=args.force,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
