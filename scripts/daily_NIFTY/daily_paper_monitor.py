from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.execution.paper import monitor_open_paper_trades


def _default_trade_date() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor open Stockie paper trades and close on target, stop, or time exit."
    )
    parser.add_argument("--trade-date", default=None, help="Paper trade date YYYY-MM-DD. Default: today IST")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    parser.add_argument("--model-version", default="cascade_v1", help="Prediction model version. Default: cascade_v1")
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="Exit slippage as decimal. Default: 0")
    parser.add_argument("--max-stale-seconds", type=int, default=300, help="Reject quotes older than this. Default: 300")
    parser.add_argument("--force-exit-time", default="15:15", help="HH:MM IST force exit time. Default: 15:15")
    parser.add_argument(
        "--disable-time-exit",
        action="store_true",
        help="Do not close open paper trades by intraday time; exit only on target, stop-loss, or max-open-days.",
    )
    parser.add_argument(
        "--max-open-days", type=int, default=5,
        help="Force-exit positions held longer than this many calendar days. Default: 5. Pass 0 to disable.",
    )
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.trade_date) if args.trade_date else _default_trade_date()
    max_open_days = args.max_open_days if args.max_open_days > 0 else None
    result = monitor_open_paper_trades(
        trade_date=trade_date,
        symbol=args.underlying.upper(),
        model_version=args.model_version,
        slippage_pct=args.slippage_pct,
        max_stale_seconds=args.max_stale_seconds,
        force_exit_time=None if args.disable_time_exit else _parse_time(args.force_exit_time),
        max_open_days=max_open_days,
    )
    print({
        "trade_date": trade_date.isoformat(),
        "underlying": args.underlying.upper(),
        "model_version": args.model_version,
        **result,
    })


if __name__ == "__main__":
    main()
