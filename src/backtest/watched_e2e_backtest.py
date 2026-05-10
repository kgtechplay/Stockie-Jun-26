from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from src.backtest.watched_underlying_backtest import (
    WatchedBacktestRequest,
    run_watched_underlying_backtest,
)


def run_watched_e2e_backtest(
    reference_date: date,
    output_dir: Path = Path("output"),
    prediction_file: str | None = None,
    strategies: list[str] | None = None,
    skip_option: bool = True,
) -> dict[str, Any]:
    """
    Run watched-list backtest for the reference-date prediction matrix.

    Today this validates the underlying direction decision. The option leg is
    intentionally marked skipped until the watched prediction output includes
    selected option contracts.
    """
    underlying_result = run_watched_underlying_backtest(
        WatchedBacktestRequest(
            reference_date=reference_date,
            output_dir=output_dir,
            prediction_file=prediction_file,
            strategies=strategies,
        )
    )

    option_result = {
        "skipped": True,
        "reason": "Watched prediction matrix does not include selected option contracts yet.",
    }
    if not skip_option:
        option_result["reason"] = "Option watched backtest is not implemented for matrix-only prediction output."

    return {
        "success": True,
        "reference_date": reference_date.isoformat(),
        "underlying": underlying_result,
        "option": option_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run watched end-to-end backtest for one reference date.")
    parser.add_argument("--reference-date", required=True, help="Reference date, YYYY-MM-DD")
    parser.add_argument("--prediction-file", default=None, help="Optional prediction CSV/JSON file under output/")
    parser.add_argument("--strategy", action="append", dest="strategies", help="Strategy column to backtest. Repeatable.")
    parser.add_argument("--include-option", action="store_true", help="Request option leg when implemented.")
    args = parser.parse_args()

    result = run_watched_e2e_backtest(
        reference_date=pd.to_datetime(args.reference_date).date(),
        prediction_file=args.prediction_file,
        strategies=args.strategies,
        skip_option=not args.include_option,
    )
    print(result)


if __name__ == "__main__":
    main()
