from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ImpactedStock:
    company_name: str
    ticker: str | None = None
    exchange: str | None = "NSE"
    expected_stock_direction: str = "uncertain"
    company_confidence: float = 0.0
    reasoning: str = ""


@dataclass
class ImpactedSector:
    rank: int
    sector: str
    commodity: str
    commodity_direction: str
    impact_direction: str
    directness: str
    sensitivity: str
    timeline_bucket: str
    sector_confidence: float
    rationale: str
    source_event_id: str
    source_headlines: list[str] = field(default_factory=list)
    stocks: list[ImpactedStock] = field(default_factory=list)


@dataclass
class ImpactListOutput:
    reference_date: date
    as_of: datetime
    event_id: str
    impacted_sectors: list[ImpactedSector] = field(default_factory=list)
