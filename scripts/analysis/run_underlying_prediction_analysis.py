from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from scripts.analysis.analysis_common import (
    build_historical_underlying_views,
    connect_db,
    dataclass_to_plain,
    views_to_summary_rows,
    write_json,
)


def default_start(end_date: date) -> date:
    return end_date - timedelta(days=31)


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
    finally:
        db.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = views_to_summary_rows(views)
    summary_csv = output_dir / f"{underlying.upper()}_underlying_view_summary_{start_date}_{end_date}.csv"
    detail_json = output_dir / f"{underlying.upper()}_underlying_view_detail_{start_date}_{end_date}.json"
    pd.DataFrame(summary_rows).to_csv(summary_csv, index=False)
    write_json(detail_json, [dataclass_to_plain(view) for view in views])

    result = {
        "underlying": underlying.upper(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "views_built": len(views),
        "summary_csv": str(summary_csv),
        "detail_json": str(detail_json),
    }
    print(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run historical underlying prediction aggregation and export UnderlyingView + StrategySignal detail."
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
