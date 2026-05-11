from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ReviewedSectorImpact:
    sector: str
    review_action: str
    final_score: float
    review_notes: str | None = None
    checks: list[str] = field(default_factory=list)


@dataclass
class SignalStock:
    company_name: str
    ticker: str | None = None
    exchange: str | None = "NSE"


@dataclass
class ApprovedTradeSignal:
    signal_id: str | None
    news_event_id: str
    published_at: datetime
    processed_at: datetime
    commodity: str
    commodity_direction: str
    commodity_confidence: float
    sector: str
    sub_sector: str | None
    stock: SignalStock
    expected_stock_direction: str
    directness: str
    sensitivity: str
    timeline_bucket: str
    sector_confidence: float
    company_confidence: float
    reviewer_confidence: float
    final_trade_score: float | None = None
    entry_allowed_from: datetime | None = None
    suggested_max_holding_days: int | None = None
    signal_status: str = "monitor_only"
    impact_channel: str = "other"
    reasoning: str = ""
    risks_to_thesis: list[str] = field(default_factory=list)
    invalidation_triggers: list[str] = field(default_factory=list)


@dataclass
class ReviewListOutput:
    reference_date: date
    as_of: datetime
    event_id: str
    review_summary: str = ""
    overall_quality_score: float = 0.0
    reviewed_sector_impacts: list[ReviewedSectorImpact] = field(default_factory=list)
    missing_sector_impacts: list[dict] = field(default_factory=list)
    removed_or_flagged_items: list[dict] = field(default_factory=list)
    approved_trade_signals: list[ApprovedTradeSignal] = field(default_factory=list)
    final_recommendation: str = "reject_and_rerun"

    def approved_sectors(self) -> list[str]:
        sectors = []
        seen = set()
        for signal in self.approved_trade_signals:
            key = signal.sector.strip().upper()
            if key and key not in seen:
                sectors.append(signal.sector)
                seen.add(key)
        return sectors
