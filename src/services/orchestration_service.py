from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.agents.dailyNews.agent import DailyNewsAgent
from src.agents.impactList.agent import ImpactListAgent
from src.agents.reviewList.agent import ReviewListAgent
from src.services.backfill_service import BackfillService
from src.services.prediction_service import PredictionService
from src.services.sector_watchlist_service import SectorWatchlistService


@dataclass
class OrchestrationService:
    """
    Runs the full news-to-prediction pipeline:

      dailyNews(reference_date)
        → impactList   (sectors impacted by the day's news)
        → reviewList   (approved sectors, ranked)
        → SectorWatchlistService
              expand sectors → NSE constituents
              register new stocks in WatchedInstrument
              load option instruments
              backfill data  [reference_date - 90d, reference_date - 1d]
              run predictions for reference_date (using data through N-1)
    """

    daily_news_agent:       DailyNewsAgent
    impact_list_agent:      ImpactListAgent
    review_list_agent:      ReviewListAgent
    sector_watchlist_service: SectorWatchlistService
    backfill_days: int = 90

    @classmethod
    def default(cls) -> "OrchestrationService":
        prediction_service = PredictionService.from_project_root(
            Path(__file__).resolve().parents[2]
        )
        return cls(
            daily_news_agent=DailyNewsAgent(),
            impact_list_agent=ImpactListAgent(),
            review_list_agent=ReviewListAgent(),
            sector_watchlist_service=SectorWatchlistService(
                backfill_service=BackfillService(),
                prediction_service=prediction_service,
            ),
        )

    def run(
        self,
        reference_date: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Run the pipeline for a given news reference date.

        reference_date — date of the news article (N).  Backfill covers N-90 → N-1;
                         predictions are generated for N.
                         Defaults to today when omitted.
        as_of          — wall-clock timestamp (defaults to now).
        """
        ref_date  = reference_date or datetime.now().date()
        timestamp = as_of or datetime.now()

        # ── Agent chain ───────────────────────────────────────────────────
        news_output   = self.daily_news_agent.run(reference_date=ref_date, as_of=timestamp)
        impact_output = self.impact_list_agent.run(news_output, as_of=timestamp)
        review_output = self.review_list_agent.run(impact_output, as_of=timestamp)

        # ── Sector expansion + backfill + predictions ─────────────────────
        sector_watchlist_result = self.sector_watchlist_service.expand_from_review(
            review_output,
            backfill_days=self.backfill_days,
        )

        return {
            "reference_date": ref_date.isoformat(),
            "as_of": timestamp.isoformat(),
            "dailyNews":     news_output,
            "impactList":    impact_output,
            "reviewList":    review_output,
            "sectorWatchlist": sector_watchlist_result,
        }
