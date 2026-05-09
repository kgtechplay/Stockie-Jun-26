from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ImpactCandidate:
    rank: int
    tradingsymbol: str
    name: str
    industry: str
    impact_direction: str
    impact_score: float
    rationale: str
    source_headlines: list[str] = field(default_factory=list)


@dataclass
class ImpactListOutput:
    as_of: datetime
    candidates: list[ImpactCandidate]
