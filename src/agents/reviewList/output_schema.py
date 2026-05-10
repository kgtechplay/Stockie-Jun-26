from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ReviewedSector:
    """One NSE sector that has been reviewed and either approved or rejected."""
    sector: str            # normalized sector label, e.g. "NIFTY PHARMA"
    approved: bool
    final_rank: int
    final_score: float
    checks: list[str] = field(default_factory=list)
    review_notes: str | None = None


@dataclass
class ReviewListOutput:
    reference_date: date           # propagated from DailyNewsOutput (date N)
    as_of: datetime
    reviewed_sectors: list[ReviewedSector]

    def approved_sectors(self) -> list[str]:
        """Return sector names that passed review, in rank order."""
        approved = [s for s in self.reviewed_sectors if s.approved]
        approved.sort(key=lambda s: s.final_rank)
        return [s.sector for s in approved]
