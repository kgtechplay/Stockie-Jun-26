from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agents.impactList.output_schema import ImpactListOutput
from src.agents.reviewList.output_schema import (
    ReviewedImpactCandidate,
    ReviewListOutput,
)


@dataclass
class ReviewListAgent:
    """Placeholder agent for reviewing and approving impact-ranked stocks."""

    config_path: Path = Path(__file__).with_name("config.yaml")

    def run(
        self,
        impact_list: ImpactListOutput,
        as_of: datetime | None = None,
    ) -> ReviewListOutput:
        timestamp = as_of or impact_list.as_of
        reviewed = [
            ReviewedImpactCandidate(
                tradingsymbol=candidate.tradingsymbol,
                approved=False,
                final_rank=candidate.rank,
                final_score=candidate.impact_score,
                sector=candidate.industry,
                industry=candidate.industry,
                checks=["placeholder_check"],
                review_notes=(
                    "Replace with data availability, liquidity, duplicate exposure, "
                    "and contradictory-signal checks."
                ),
            )
            for candidate in impact_list.candidates
        ]
        return ReviewListOutput(
            as_of=timestamp,
            reviewed_candidates=reviewed,
            identified_sectors=[
                candidate.industry for candidate in impact_list.candidates if candidate.industry
            ],
        )
