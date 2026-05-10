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

from src.data_manager.underlying_history_reader import get_active_underlyings
from src.services.prediction_service import PredictionService
from src.technical_analysis.underlying_registry import load_underlying_prediction_strategies


@dataclass
class HistoricalPredictionRequest:
    start_date: date | None = None
    end_date: date | None = None
    underlyings: list[str] | None = None
    strategies: list[str] | None = None
    lookback_days: int = 90
    use_agentic: bool = False


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

    def run(self, request: HistoricalPredictionRequest | None = None) -> dict[str, Any]:
        request = request or HistoricalPredictionRequest()
        end_date = request.end_date or datetime.now().date()
        start_date = request.start_date or (end_date - timedelta(days=request.lookback_days))
        warmup_start = start_date - timedelta(days=max(self.prediction_service.default_lookback * 3, 45))

        underlyings = request.underlyings or get_active_underlyings(instrument_type=None)
        underlyings = sorted({symbol.upper() for symbol in underlyings if symbol})

        registry = load_underlying_prediction_strategies()
        strategies = request.strategies or sorted(registry.keys())
        missing = [strategy for strategy in strategies if strategy not in registry]
        if missing:
            available = ", ".join(sorted(registry.keys()))
            raise ValueError(f"Unknown strategies: {', '.join(missing)}. Available strategies: {available}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        files: list[str] = []
        errors: list[dict[str, str]] = []
        for underlying in underlyings:
            for strategy in strategies:
                try:
                    predictions = self.prediction_service.generate_predictions_for_strategy(
                        instrument=underlying,
                        strategy=strategy,
                        use_agentic=request.use_agentic,
                        start_date=warmup_start.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                    predictions = predictions.copy()
                    predictions["date"] = pd.to_datetime(predictions["date"]).dt.normalize()
                    predictions = predictions[
                        (predictions["date"] >= pd.Timestamp(start_date))
                        & (predictions["date"] <= pd.Timestamp(end_date))
                    ]
                    output_file = self.save_historical_predictions(underlying, strategy, predictions)
                    files.append(output_file)
                except Exception as exc:
                    errors.append(
                        {
                            "underlying": underlying,
                            "strategy": strategy,
                            "error": str(exc),
                        }
                    )

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "underlyings": underlyings,
            "strategies": strategies,
            "output_dir": str(self.output_dir),
            "files": files,
            "errors": errors,
        }

    def save_historical_predictions(
        self,
        underlying: str,
        strategy: str,
        predictions_df: pd.DataFrame,
    ) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{underlying.upper()}_{strategy}.csv"
        output_path = self.output_dir / filename
        output = predictions_df[["date", "prediction"]].copy()
        output["date"] = pd.to_datetime(output["date"]).dt.date
        output.to_csv(output_path, index=False)
        return filename


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate historical prediction CSVs for watched underlyings.")
    parser.add_argument("--start", default=None, help="Start date, YYYY-MM-DD. Defaults to end minus 90 days.")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--underlying", action="append", dest="underlyings", help="Underlying to generate. Repeatable.")
    parser.add_argument("--strategy", action="append", dest="strategies", help="Strategy to generate. Repeatable.")
    parser.add_argument("--agentic", action="store_true", help="Use aggregator path for each individual strategy.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    service = HistoricalPredictionService.from_project_root(project_root)
    result = service.run(
        HistoricalPredictionRequest(
            start_date=pd.to_datetime(args.start).date() if args.start else None,
            end_date=pd.to_datetime(args.end).date() if args.end else None,
            underlyings=args.underlyings,
            strategies=args.strategies,
            use_agentic=args.agentic,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
