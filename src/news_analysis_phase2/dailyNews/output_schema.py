from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class NewsArticle:
    title: str
    source: str
    published_at: datetime
    region: str | None = None
    url: str | None = None


@dataclass
class CommodityImpact:
    commodity: str
    commodity_group: str
    impact_type: str
    expected_price_direction: str
    impact_mechanism: list[str] = field(default_factory=list)
    timeline: str = "uncertain"
    reasoning: str = ""
    evidence_from_article: list[str] = field(default_factory=list)
    alternate_view: str | None = None
    confidence: float = 0.0


@dataclass
class DailyNewsOutput:
    event_id: str
    reference_date: date
    as_of: datetime
    article: NewsArticle
    event_summary: str
    event_type: str
    commodities: list[CommodityImpact] = field(default_factory=list)
    overall_confidence: float = 0.0
    requires_human_review: bool = True
