from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DailyNewsFinding:
    headline: str
    source: str
    impacted_underlying: str
    impact_type: str
    rationale: str
    confidence: float
    related_symbols: list[str] = field(default_factory=list)


@dataclass
class DailyNewsOutput:
    as_of: datetime
    sources: list[str]
    findings: list[DailyNewsFinding]
