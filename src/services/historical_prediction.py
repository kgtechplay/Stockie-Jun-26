from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from src.services.prediction_service import PredictionService


@dataclass
class HistoricalPredictionRequest:
    underlying: str
    start_date: date | None = None
    end_date: date | None = None
    strategies: list[str] | None = None
    lookback_days: int = 60


@dataclass
class HistoricalPredictionService:
    prediction_service: PredictionService
    output_dir: Path

    @classmethod
    def from_project_root(cls, project_root: Path) -> "HistoricalPredictionService":
        return cls(
            prediction_service=PredictionService.from_project_root(project_root),
            output_dir=project_root / "output" / "historical",
        )

    def run(self, request: HistoricalPredictionRequest) -> dict[str, Any]:
        end_date = request.end_date or datetime.now().date()
        start_date = request.start_date or (end_date - timedelta(days=request.lookback_days))
        underlying = request.underlying.strip().upper()
        if not underlying:
            raise ValueError("underlying is required")

        strategies = self.prediction_service.get_selected_strategy_names(request.strategies)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_df = self.prediction_service.generate_consolidated_predictions(
            instrument=underlying,
            start_date=start_date,
            end_date=end_date,
            strategies=strategies,
        )
        output_file = self.save_historical_predictions(underlying, output_df)

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "underlying": underlying,
            "strategies": strategies,
            "output_dir": str(self.output_dir),
            "output_file": output_file,
            "rows": len(output_df),
        }

    def save_historical_predictions(
        self,
        underlying: str,
        predictions_df: pd.DataFrame,
    ) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{underlying.upper()}_prediction.csv"
        output_path = self.output_dir / filename
        predictions_df.to_csv(output_path, index=False)
        return filename


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate historical prediction CSVs for one underlying.")
    parser.add_argument("--start", default=None, help="Start date, YYYY-MM-DD. Defaults to end minus 60 days.")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--underlying", required=True, help="Underlying to generate, e.g. RELIANCE or NIFTY.")
    parser.add_argument("--strategy", action="append", dest="strategies", help="Strategy to generate. Repeatable.")
    parser.add_argument("--lookback-days", type=int, default=60, help="Default historical window when --start is omitted.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    service = HistoricalPredictionService.from_project_root(project_root)
    result = service.run(
        HistoricalPredictionRequest(
            underlying=args.underlying,
            start_date=pd.to_datetime(args.start).date() if args.start else None,
            end_date=pd.to_datetime(args.end).date() if args.end else None,
            strategies=args.strategies,
            lookback_days=args.lookback_days,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
