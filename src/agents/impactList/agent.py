from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agents.dailyNews.output_schema import DailyNewsOutput
from src.agents.impactList.output_schema import (
    ImpactCandidate,
    ImpactListOutput,
)


@dataclass
class ImpactListAgent:
    """Placeholder agent for ranking stocks affected by news-driven factors."""

    config_path: Path = Path(__file__).with_name("config.yaml")

    def run(
        self,
        daily_news: DailyNewsOutput,
        as_of: datetime | None = None,
    ) -> ImpactListOutput:
        timestamp = as_of or daily_news.as_of
        candidates: list[ImpactCandidate] = []
        for rank, finding in enumerate(daily_news.findings, start=1):
            candidates.append(
                ImpactCandidate(
                    rank=rank,
                    tradingsymbol="PLACEHOLDER",
                    name="Placeholder stock candidate",
                    industry=finding.impacted_underlying,
                    impact_direction="UNKNOWN",
                    impact_score=0.0,
                    rationale=(
                        "Replace with industry/commodity exposure mapping, watchlist "
                        "lookup, and ordered impact scoring."
                    ),
                    source_headlines=[finding.headline],
                )
            )
        return ImpactListOutput(as_of=timestamp, candidates=candidates)
