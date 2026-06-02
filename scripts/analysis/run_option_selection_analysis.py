from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from scripts.analysis.analysis_common import (
    build_historical_underlying_views,
    connect_db,
    dataclass_to_plain,
    fetch_spot_close_by_date,
    write_json,
)
from src.technical_analysis.optionselection.option_selector import select_option_strategy
from src.technical_analysis.optionselection.schema import OptionSelectionResult, OptionStrategyCandidate


def default_start(end_date: date) -> date:
    return end_date - timedelta(days=31)


def summarize_selection(result: OptionSelectionResult) -> dict[str, Any]:
    candidate = result.selected_strategy
    legs = [
        {
            "side": leg.side,
            "tradingsymbol": leg.contract.tradingsymbol,
            "instrument_token": leg.contract.instrument_token,
            "expiry": leg.contract.expiry,
            "strike": leg.contract.strike,
            "option_type": leg.contract.option_type,
            "last_price": leg.contract.last_price,
            "bid": leg.contract.bid,
            "ask": leg.contract.ask,
            "iv": leg.contract.iv,
            "delta": leg.contract.delta,
            "theta": leg.contract.theta,
            "liquidity_score": leg.features.liquidity_score,
            "spread_pct": leg.features.spread_pct,
        }
        for leg in candidate.legs
    ]
    return {
        "trade_date": result.trade_date,
        "underlying": result.underlying,
        "option_bias": result.option_bias,
        "decision": "NO_TRADE" if result.no_trade_reason else "TRADE",
        "no_trade_reason": result.no_trade_reason,
        "evaluated_candidate_count": result.evaluated_candidate_count,
        "strategy_type": candidate.strategy_type,
        "score": candidate.score,
        "confidence": candidate.confidence,
        "direction": candidate.direction,
        "entry_debit_or_credit": candidate.entry_debit_or_credit,
        "max_profit": candidate.max_profit,
        "max_loss": candidate.max_loss,
        "breakeven": candidate.breakeven,
        "reward_risk": candidate.reward_risk,
        "total_delta": candidate.total_delta,
        "total_theta": candidate.total_theta,
        "expected_underlying_move_abs": candidate.expected_underlying_move_abs,
        "expected_underlying_move_pct": candidate.expected_underlying_move_pct,
        "expected_holding_days": candidate.expected_holding_days,
        "legs": legs,
        "reasons": candidate.reasons,
        "warnings": candidate.warnings,
    }


def fetch_same_day_leg_prices(db, candidate: OptionStrategyCandidate, trade_date: str) -> dict[str, dict[str, float | None]]:
    if not candidate.legs:
        return {}
    tokens = [
        leg.contract.instrument_token
        for leg in candidate.legs
        if leg.contract.instrument_token is not None
    ]
    if not tokens:
        return {}

    is_postgres = getattr(db, "db_kind", "") == "postgres"
    option_instrument = '"OptionInstrument"' if is_postgres else "dbo.OptionInstrument"
    option_snapshot = '"OptionSnapshot"' if is_postgres else "dbo.OptionSnapshot"
    placeholder = "%s" if is_postgres else "?"
    token_placeholders = ",".join(placeholder for _ in tokens)
    trade_date_expr = "os.trade_date" if is_postgres else "CAST(os.snapshot_time AS date)"
    sql = f"""
        SELECT
            oi.instrument_token,
            os.snapshot_time,
            os.last_price,
            os.bid_price,
            os.ask_price
        FROM {option_snapshot} os
        INNER JOIN {option_instrument} oi
            ON oi.id = os.option_instrument_id
        WHERE oi.instrument_token IN ({token_placeholders})
          AND {trade_date_expr} = {placeholder}
        ORDER BY oi.instrument_token, os.snapshot_time
    """
    df = pd.read_sql(sql, db.conn, params=[*tokens, trade_date])
    if df.empty:
        return {}

    output: dict[str, dict[str, float | None]] = {}
    for token, group in df.groupby("instrument_token"):
        ordered = group.sort_values("snapshot_time")
        entry = ordered.iloc[0]
        exit_ = ordered.iloc[-1]
        output[str(int(token))] = {
            "entry_last": _float_or_none(entry.get("last_price")),
            "entry_ask": _float_or_none(entry.get("ask_price")),
            "entry_bid": _float_or_none(entry.get("bid_price")),
            "exit_last": _float_or_none(exit_.get("last_price")),
            "exit_bid": _float_or_none(exit_.get("bid_price")),
            "exit_ask": _float_or_none(exit_.get("ask_price")),
        }
    return output


def estimate_same_day_return(candidate: OptionStrategyCandidate, prices: dict[str, dict[str, float | None]]) -> float | None:
    if not candidate.legs:
        return None
    entry_value = 0.0
    exit_value = 0.0
    for leg in candidate.legs:
        token = str(leg.contract.instrument_token)
        price = prices.get(token)
        if not price:
            return None
        if leg.side == "BUY":
            entry = price.get("entry_ask") or price.get("entry_last")
            exit_ = price.get("exit_bid") or price.get("exit_last")
            sign = 1.0
        else:
            entry = price.get("entry_bid") or price.get("entry_last")
            exit_ = price.get("exit_ask") or price.get("exit_last")
            sign = -1.0
        if entry is None or exit_ is None:
            return None
        entry_value += sign * entry
        exit_value += sign * exit_
    if entry_value == 0:
        return None
    return (exit_value - entry_value) / abs(entry_value)


def _float_or_none(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def run_analysis(
    underlying: str,
    start_date: date,
    end_date: date,
    lookback_days: int,
    warmup_days: int,
    min_history_days: int,
    output_dir: Path,
) -> dict[str, object]:
    db = connect_db()
    try:
        views = build_historical_underlying_views(
            db=db,
            underlying=underlying,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            warmup_days=warmup_days,
            min_history_days=min_history_days,
        )
        spot_by_date = fetch_spot_close_by_date(db, underlying, start_date, end_date)
        rows: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        for view in views:
            spot_price = spot_by_date.get(view.trade_date)
            if spot_price is None:
                continue
            result = select_option_strategy(
                db_client=db,
                underlying_view=view,
                spot_price=spot_price,
                as_of_time=f"{view.trade_date} 15:15:00",
            )
            summary = summarize_selection(result)
            prices = fetch_same_day_leg_prices(db, result.selected_strategy, view.trade_date)
            summary["same_day_return_pct"] = estimate_same_day_return(result.selected_strategy, prices)
            summary["underlying_raw_signal"] = view.raw_signal
            summary["underlying_strength_score"] = view.strength_score
            summary["underlying_regime"] = view.stock_regime
            rows.append({key: value for key, value in summary.items() if key not in {"legs", "reasons", "warnings"}})
            details.append({
                "underlying_view": dataclass_to_plain(view),
                "selection": summary,
                "selection_result": dataclass_to_plain(result),
                "same_day_leg_prices": prices,
            })
    finally:
        db.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = output_dir / f"{underlying.upper()}_option_selection_analysis_{start_date}_{end_date}.csv"
    detail_json = output_dir / f"{underlying.upper()}_option_selection_analysis_{start_date}_{end_date}.json"
    pd.DataFrame(rows).to_csv(summary_csv, index=False)
    write_json(detail_json, details)
    result = {
        "underlying": underlying.upper(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "views_evaluated": len(views),
        "option_selection_rows": len(rows),
        "summary_csv": str(summary_csv),
        "detail_json": str(detail_json),
    }
    print(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run option-selection analysis/backtest-style reports from historical UnderlyingView rows."
    )
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD. Defaults to one month before --end.")
    parser.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--warmup-days", type=int, default=120)
    parser.add_argument("--min-history-days", type=int, default=20)
    parser.add_argument("--output-dir", default="output/analysis")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end)
    start_date = date.fromisoformat(args.start) if args.start else default_start(end_date)
    run_analysis(
        underlying=args.underlying.upper(),
        start_date=start_date,
        end_date=end_date,
        lookback_days=args.lookback_days,
        warmup_days=args.warmup_days,
        min_history_days=args.min_history_days,
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
