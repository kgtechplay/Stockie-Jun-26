# scripts/daily/daily_underlying_prediction.py
"""
Build UnderlyingView (regime + strategy signals + scoring) for today and persist to UnderlyingPredictionDaily.

Run after daily_market_refresh.py has populated UnderlyingSnapshot and SignalFeatureDaily.

Usage:
    python scripts/daily/daily_underlying_prediction.py
    python scripts/daily/daily_underlying_prediction.py --underlying NIFTY
    python scripts/daily/daily_underlying_prediction.py --date 2026-06-15
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from scripts.analysis.analysis_common import (
    build_view_for_window,
    fetch_underlying_history,
    connect_db,
)
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

LOOKBACK_DAYS = 120
WARMUP_DAYS = 150
MIN_HISTORY_DAYS = 20


def view_to_db_row(view) -> dict:
    from dataclasses import asdict
    d = asdict(view)
    reasons = d.pop("reasons", None)
    warnings = d.pop("warnings", None)
    signals = d.pop("strategy_signals", None)
    return {
        **d,
        "reasons": reasons,
        "warnings": warnings,
        "strategy_signals": signals,
    }


def run_daily_underlying_prediction(
    target_date: date,
    underlyings: list[str] | None = None,
) -> dict:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()

    try:
        if underlyings:
            symbols = [s.upper() for s in underlyings]
        else:
            watched = db.get_watched_instruments(instrument_type="INDEX")
            symbols = [w.tradingsymbol for w in watched if w.tradingsymbol in ("NIFTY", "BANKNIFTY")]
            if not symbols:
                symbols = ["NIFTY"]

        results = {}
        for symbol in symbols:
            print(f"Building prediction for {symbol} on {target_date}...")
            df = fetch_underlying_history(db, symbol, target_date, target_date, WARMUP_DAYS)
            if df.empty or target_date not in df["trade_date"].values:
                print(f"  {symbol}: no data for {target_date}, skipping")
                results[symbol] = "no_data"
                continue

            window = df[df["trade_date"] <= target_date].tail(LOOKBACK_DAYS)
            if len(window) < MIN_HISTORY_DAYS:
                print(f"  {symbol}: insufficient history ({len(window)} days), skipping")
                results[symbol] = "insufficient_history"
                continue

            view = build_view_for_window(symbol, target_date, window)
            row = view_to_db_row(view)
            db.upsert_underlying_prediction_daily([row])
            print(f"  {symbol}: {view.direction} | regime={view.stock_regime} | score={view.strength_score}")
            results[symbol] = {
                "direction": view.direction,
                "regime": view.stock_regime,
                "strength_score": view.strength_score,
                "option_bias": view.option_bias,
                "is_option_eligible": view.is_option_eligible,
            }
    finally:
        db.close()

    return {
        "trade_date": target_date.isoformat(),
        "symbols": symbols,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and persist daily underlying prediction (regime + signals).")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--underlying", action="append", dest="underlyings", help="Symbol(s). Can repeat.")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    symbols = [s.strip().upper() for s in args.underlyings] if args.underlyings else None

    print(f"Daily underlying prediction: {target_date}")
    result = run_daily_underlying_prediction(target_date=target_date, underlyings=symbols)
    print("\nDone.")
    print(result)


if __name__ == "__main__":
    main()
