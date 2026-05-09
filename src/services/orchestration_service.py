from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.agents.dailyNews.agent import DailyNewsAgent
from src.agents.impactList.agent import ImpactListAgent
from src.agents.reviewList.agent import ReviewListAgent
from src.services.backfill_service import BackfillRequest, BackfillService
from src.services.sector_watchlist_service import SectorWatchlistService


@dataclass
class OrchestrationService:
    """Runs dailyNews -> impactList -> reviewList -> BackfillService."""

    daily_news_agent: DailyNewsAgent
    impact_list_agent: ImpactListAgent
    review_list_agent: ReviewListAgent
    backfill_service: BackfillService
    sector_watchlist_service: SectorWatchlistService
    backfill_days: int = 90

    @classmethod
    def default(cls) -> "OrchestrationService":
        return cls(
            daily_news_agent=DailyNewsAgent(),
            impact_list_agent=ImpactListAgent(),
            review_list_agent=ReviewListAgent(),
            backfill_service=BackfillService(),
            sector_watchlist_service=SectorWatchlistService(),
        )

    def run(self, as_of: datetime | None = None) -> dict[str, Any]:
        timestamp = as_of or datetime.now()
        news_output = self.daily_news_agent.run(as_of=timestamp)
        impact_output = self.impact_list_agent.run(news_output, as_of=timestamp)
        review_output = self.review_list_agent.run(impact_output, as_of=timestamp)

        approved_symbols = review_output.approved_symbols()
        if approved_symbols:
            end_date = timestamp.date()
            backfill_result = self.backfill_service.run_backfill(
                BackfillRequest(
                    start_date=end_date - timedelta(days=self.backfill_days),
                    end_date=end_date,
                    underlyings=approved_symbols,
                )
            )
        else:
            backfill_result = {
                "triggered": False,
                "reason": "No approved stock symbols to backfill.",
                "underlyings": [],
            }

        sector_watchlist_result = self.sector_watchlist_service.expand_from_review(
            review_output,
            as_of=timestamp,
            backfill_days=self.backfill_days,
        )

        return {
            "as_of": timestamp.isoformat(),
            "dailyNews": news_output,
            "impactList": impact_output,
            "reviewList": review_output,
            "backfill": backfill_result,
            "sectorWatchlist": sector_watchlist_result,
        }
