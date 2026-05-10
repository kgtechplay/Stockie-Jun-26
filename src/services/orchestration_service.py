from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.agents.dailyNews.agent import DailyNewsAgent
from src.agents.impactList.agent import ImpactListAgent
from src.agents.reviewList.agent import ReviewListAgent
from src.backtest.watched_underlying_backtest import WatchedBacktestRequest, run_watched_underlying_backtest
from src.services.backfill_service import BackfillRequest, BackfillService
from src.services.prediction_service import PredictionService
from src.services.sector_watchlist_service import SectorWatchlistService


@dataclass
class OrchestrationService:
    """
    Top-level service for the news-to-data-to-prediction flow.

    Flow:
      1. dailyNews reads news for reference_date.
      2. impactList identifies impacted sectors.
      3. reviewList approves sectors and carries the reference_date forward.
      4. sector_watchlist_service expands sectors into stocks and inserts new
         stocks into WatchedInstrument.
      5. backfill_service backfills the returned new stocks for the 3-month
         window ending one day before reference_date.
      6. prediction_service runs each prediction strategy for each returned
         stock at reference_date, writes one output/<reference_date>.csv
         matrix, and adds an aggregate decision column.
      7. watched_underlying_backtest evaluates the reference-date matrix
         against the next trading day when market data is available.
    """

    daily_news_agent: DailyNewsAgent
    impact_list_agent: ImpactListAgent
    review_list_agent: ReviewListAgent
    sector_watchlist_service: SectorWatchlistService
    backfill_service: BackfillService
    prediction_service: PredictionService
    backfill_days: int = 90

    @classmethod
    def default(cls) -> "OrchestrationService":
        project_root = Path(__file__).resolve().parents[2]
        return cls(
            daily_news_agent=DailyNewsAgent(),
            impact_list_agent=ImpactListAgent(),
            review_list_agent=ReviewListAgent(),
            sector_watchlist_service=SectorWatchlistService(),
            backfill_service=BackfillService(),
            prediction_service=PredictionService.from_project_root(project_root),
        )

    def run(
        self,
        reference_date: date | None = None,
        as_of: datetime | None = None,
        prediction_strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        ref_date = reference_date or datetime.now().date()
        timestamp = as_of or datetime.now()

        news_output = self.daily_news_agent.run(reference_date=ref_date, as_of=timestamp)
        impact_output = self.impact_list_agent.run(news_output, as_of=timestamp)
        review_output = self.review_list_agent.run(impact_output, as_of=timestamp)

        review_reference_date = review_output.reference_date
        sector_watchlist_result = self.sector_watchlist_service.expand_from_review(review_output)
        all_symbols = sector_watchlist_result.get("symbols", [])
        new_symbols = sector_watchlist_result.get("new_symbols", [])

        backfill_result = self._run_backfill(
            symbols=new_symbols,
            reference_date=review_reference_date,
        )
        prediction_result = self._run_predictions(
            symbols=all_symbols,
            reference_date=review_reference_date,
            strategies=prediction_strategies,
        )
        backtest_result = self._run_watched_backtest(
            prediction_result=prediction_result,
            reference_date=review_reference_date,
            strategies=prediction_strategies,
        )

        return {
            "reference_date": review_reference_date.isoformat(),
            "as_of": timestamp.isoformat(),
            "dailyNews": news_output,
            "impactList": impact_output,
            "reviewList": review_output,
            "sectorWatchlist": sector_watchlist_result,
            "backfill": backfill_result,
            "predictions": prediction_result,
            "backtest": backtest_result,
        }

    def _run_backfill(
        self,
        symbols: list[str],
        reference_date: date,
    ) -> dict[str, Any]:
        if not symbols:
            return {
                "triggered": False,
                "reason": "No new stocks returned by sector_watchlist_service.",
                "underlyings": [],
            }

        start_date = reference_date - timedelta(days=self.backfill_days)
        end_date = reference_date - timedelta(days=1)
        if start_date > end_date:
            return {
                "triggered": False,
                "reason": "Invalid backfill window.",
                "underlyings": symbols,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

        result = self.backfill_service.run_backfill(
            BackfillRequest(
                start_date=start_date,
                end_date=end_date,
                underlyings=symbols,
            )
        )
        return {"triggered": True, **result}

    def _run_predictions(
        self,
        symbols: list[str],
        reference_date: date,
        strategies: list[str] | None,
    ) -> dict[str, Any]:
        if not symbols:
            return {
                "triggered": False,
                "reason": "No stocks returned by sector_watchlist_service.",
                "outputs": {},
            }

        outputs = self.prediction_service.run_reference_date_predictions_for_symbols(
            instruments=symbols,
            reference_date=reference_date,
            strategies=strategies,
        )
        return {
            "triggered": True,
            "reference_date": reference_date.isoformat(),
            "symbols": symbols,
            "outputs": outputs,
        }

    def _run_watched_backtest(
        self,
        prediction_result: dict[str, Any],
        reference_date: date,
        strategies: list[str] | None,
    ) -> dict[str, Any]:
        if not prediction_result.get("triggered"):
            return {
                "triggered": False,
                "reason": "Predictions were not triggered.",
            }

        outputs = prediction_result.get("outputs") or {}
        prediction_file = outputs.get("output_file")
        if not prediction_file:
            return {
                "triggered": False,
                "reason": "Prediction output file was not returned.",
            }

        try:
            result = run_watched_underlying_backtest(
                WatchedBacktestRequest(
                    reference_date=reference_date,
                    output_dir=self.prediction_service.output_dir,
                    prediction_file=prediction_file,
                    strategies=strategies,
                )
            )
            return {"triggered": True, **result}
        except Exception as exc:
            return {
                "triggered": False,
                "reason": str(exc),
                "prediction_file": prediction_file,
            }
