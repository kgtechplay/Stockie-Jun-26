from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class DailyNewsFinding:
    headline: str
    source: str
    impacted_sector: str       # sector / industry label inferred from the article
    impact_type: str           # e.g. "POSITIVE", "NEGATIVE", "NEUTRAL"
    rationale: str
    confidence: float          # 0.0 – 1.0
    raw_symbols: list[str] = field(default_factory=list)
    # raw company/symbol mentions from the article — used as context by impactList
    # but the pipeline operates at sector granularity, not individual stocks


@dataclass
class DailyNewsOutput:
    reference_date: date       # date of the news article being processed
    as_of: datetime            # wall-clock time when the agent ran
    sources: list[str]
    findings: list[DailyNewsFinding]
