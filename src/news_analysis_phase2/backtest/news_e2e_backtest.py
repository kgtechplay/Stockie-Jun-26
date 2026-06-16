from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from src.news_analysis_phase2.backtest.news_underlying_backtest import (
    NewsBacktestRequest,
    run_news_underlying_backtest,
)


def run_news_e2e_backtest(
    reference_date: date,
    output_dir: Path = Path("output"),
    prediction_file: str | None = None,
    strategies: list[str] | None = None,
    skip_option: bool = True,
    signal_journal_file: str | None = None,
) -> dict[str, Any]:
    """
    Run news-analysis backtest for the current signal journal.

    Today this validates the underlying direction decision. The option leg is
    intentionally marked skipped until approved signal rows include selected
    option contracts.
    """
    underlying_result = run_news_underlying_backtest(
        NewsBacktestRequest(
            reference_date=reference_date,
            output_dir=output_dir,
            prediction_file=prediction_file,
            strategies=strategies,
            signal_journal_file=signal_journal_file or "trade_signal_journal.csv",
        )
    )

    option_result = {
        "skipped": True,
        "reason": "News signal journal does not include selected option contracts yet.",
    }
    if not skip_option:
        option_result["reason"] = "Option news backtest is not implemented for signal-journal output yet."

    return {
        "success": True,
        "reference_date": reference_date.isoformat(),
        "underlying": underlying_result,
        "option": option_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run news end-to-end backtest for one reference date.")
    parser.add_argument("--reference-date", required=True, help="Reference date, YYYY-MM-DD")
    parser.add_argument("--prediction-file", default=None, help="Optional prediction CSV/JSON file under output/")
    parser.add_argument("--signal-journal-file", default=None, help="Signal journal CSV path.")
    parser.add_argument("--strategy", action="append", dest="strategies", help="Strategy column to backtest. Repeatable.")
    parser.add_argument("--include-option", action="store_true", help="Request option leg when implemented.")
    args = parser.parse_args()

    result = run_news_e2e_backtest(
        reference_date=pd.to_datetime(args.reference_date).date(),
        prediction_file=args.prediction_file,
        strategies=args.strategies,
        signal_journal_file=args.signal_journal_file,
        skip_option=not args.include_option,
    )
    print(result)


if __name__ == "__main__":
    main()
