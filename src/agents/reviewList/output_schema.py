from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReviewedImpactCandidate:
    tradingsymbol: str
    approved: bool
    final_rank: int
    final_score: float
    sector: str | None = None
    industry: str | None = None
    checks: list[str] = field(default_factory=list)
    review_notes: str | None = None


@dataclass
class ReviewListOutput:
    as_of: datetime
    reviewed_candidates: list[ReviewedImpactCandidate]
    identified_sectors: list[str] = field(default_factory=list)

    def approved_symbols(self) -> list[str]:
        return [
            item.tradingsymbol
            for item in self.reviewed_candidates
            if item.approved and item.tradingsymbol
        ]

    def sectors(self) -> list[str]:
        seen: set[str] = set()
        sectors: list[str] = []
        for value in self.identified_sectors:
            cleaned = _clean_sector(value)
            if cleaned and cleaned not in seen:
                sectors.append(cleaned)
                seen.add(cleaned)

        for item in self.reviewed_candidates:
            for value in (item.sector, item.industry):
                cleaned = _clean_sector(value)
                if cleaned and cleaned not in seen:
                    sectors.append(cleaned)
                    seen.add(cleaned)
        return sectors


def _clean_sector(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None
