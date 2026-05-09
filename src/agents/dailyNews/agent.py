from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agents.dailyNews.output_schema import (
    DailyNewsFinding,
    DailyNewsOutput,
)


@dataclass
class DailyNewsAgent:
    """Placeholder agent for scanning configured news sources."""

    config_path: Path = Path(__file__).with_name("config.yaml")

    def run(self, as_of: datetime | None = None) -> DailyNewsOutput:
        timestamp = as_of or datetime.now()
        return DailyNewsOutput(
            as_of=timestamp,
            sources=self._configured_sources(),
            findings=[
                DailyNewsFinding(
                    headline="Placeholder daily macro/micro news scan",
                    source="configured_news_sources",
                    impacted_underlying="UNKNOWN",
                    impact_type="UNKNOWN",
                    rationale=(
                        "Replace with scraper and NLP extraction that maps news to "
                        "industries, commodities, or macro factors."
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
