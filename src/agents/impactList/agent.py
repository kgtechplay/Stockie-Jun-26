from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agents.dailyNews.output_schema import DailyNewsOutput
from src.agents.impactList.output_schema import (
    ImpactedSector,
    ImpactListOutput,
)


@dataclass
class ImpactListAgent:
    """Placeholder agent for ranking NSE sectors affected by news-driven factors."""

    config_path: Path = Path(__file__).with_name("config.yaml")

    def run(
        self,
        daily_news: DailyNewsOutput,
        as_of: datetime | None = None,
    ) -> ImpactListOutput:
        """
        Map DailyNewsFindings → ranked ImpactedSectors.

        Each finding's impacted_sector is used as-is here; real implementation
        should normalise raw text to canonical NSE sector index names, de-duplicate
        overlapping sector mentions, and compute impact scores from article signals.
        """
        timestamp = as_of or daily_news.as_of

        impacted_sectors: list[ImpactedSector] = []
        for rank, finding in enumerate(daily_news.findings, start=1):
            impacted_sectors.append(
                ImpactedSector(
                    rank=rank,
                    sector=finding.impacted_sector,
                    impact_direction=finding.impact_type if finding.impact_type != "UNKNOWN" else "NEUTRAL",
                    impact_score=finding.confidence,
                    rationale=(
                        "Replace with sector-exposure scoring: map article signals "
                        "to NSE sector indices, weight by confidence and breadth."
                    ),
                    source_headlines=[finding.headline],
                )
            )

        return ImpactListOutput(
            reference_date=daily_news.reference_date,
            as_of=timestamp,
            impacted_sectors=impacted_sectors,
        )
