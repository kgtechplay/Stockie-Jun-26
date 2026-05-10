from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ImpactedSector:
    """One NSE sector impacted by the day's news findings."""
    rank: int
    sector: str            # normalized sector label, e.g. "NIFTY PHARMA"
    impact_direction: str  # "POSITIVE" | "NEGATIVE" | "NEUTRAL"
    impact_score: float    # 0.0 – 1.0
    rationale: str
    source_headlines: list[str] = field(default_factory=list)


@dataclass
class ImpactListOutput:
    reference_date: date           # propagated from DailyNewsOutput
    as_of: datetime
    impacted_sectors: list[ImpactedSector]
