from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from src.agents.dailyNews.output_schema import (
    DailyNewsFinding,
    DailyNewsOutput,
)


@dataclass
class DailyNewsAgent:
    """Placeholder agent for scanning configured news sources."""

    config_path: Path = Path(__file__).with_name("config.yaml")

    def run(
        self,
        reference_date: date | None = None,
        as_of: datetime | None = None,
    ) -> DailyNewsOutput:
        """
        Process news articles for reference_date.

        reference_date — the date of the news article (used as N throughout the
                         pipeline; backfill will cover N-90 → N-1).
        as_of          — wall-clock timestamp for when this agent ran (defaults
                         to now; can differ from reference_date when reprocessing
                         historical articles).
        """
        ref_date  = reference_date or datetime.now().date()
        timestamp = as_of or datetime.now()

        return DailyNewsOutput(
            reference_date=ref_date,
            as_of=timestamp,
            sources=self._configured_sources(),
            findings=[
                DailyNewsFinding(
                    headline="Placeholder daily macro/micro news scan",
                    source="configured_news_sources",
                    impacted_sector="UNKNOWN",
                    impact_type="UNKNOWN",
                    rationale=(
                        "Replace with scraper and NLP extraction that maps news "
                        "headlines to NSE sector labels."
                    ),
                    confidence=0.0,
                )
            ],
        )

    def _configured_sources(self) -> list[str]:
        try:
            lines = self.config_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        sources: list[str] = []
        in_sources = False
        for line in lines:
            stripped = line.strip()
            if stripped == "sources:":
                in_sources = True
                continue
            if in_sources and stripped.startswith("- "):
                sources.append(stripped[2:].strip().strip('"'))
            elif in_sources and stripped and not stripped.startswith("#"):
                break
        return sources
