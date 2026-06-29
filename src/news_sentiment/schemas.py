from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NewsArticle:
    article_id: str
    source: str
    url: str
    title: str
    summary: str
    published_at: datetime
    fetched_at: datetime
    region: str
    provider: str

    def text_for_model(self) -> str:
        return "\n\n".join(part.strip() for part in [self.title, self.summary] if part and part.strip())

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["published_at"] = self.published_at.isoformat()
        row["fetched_at"] = self.fetched_at.isoformat()
        return row


@dataclass(frozen=True)
class SentimentResult:
    label: str
    score: float
    confidence: float
    model_name: str


@dataclass(frozen=True)
class SectorTag:
    sector: str
    confidence: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EnrichedArticle:
    article: NewsArticle
    sentiment: SentimentResult
    sectors: list[SectorTag]
    sector_weight: float
    weighted_sentiment: float

    def to_row(self, target_date: str, window_start: datetime, window_end: datetime) -> dict[str, Any]:
        row = self.article.to_row()
        row.update({
            "target_date": target_date,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "sentiment_label": self.sentiment.label,
            "sentiment_score": self.sentiment.score,
            "sentiment_confidence": self.sentiment.confidence,
            "sentiment_model": self.sentiment.model_name,
            "sectors": ";".join(tag.sector for tag in self.sectors),
            "sector_confidences": ";".join(f"{tag.sector}:{tag.confidence:.3f}" for tag in self.sectors),
            "sector_weight": self.sector_weight,
            "weighted_sentiment": self.weighted_sentiment,
        })
        return row


@dataclass(frozen=True)
class CompositeSignal:
    target_date: str
    window_start: datetime
    window_end: datetime
    article_count: int
    usable_article_count: int
    composite_score: float
    composite_label: str
    mean_confidence: float
    positive_count: int
    neutral_count: int
    negative_count: int
    weighted_signal_sum: float
    normalization_denominator: float
    source_mix: str
    generated_at: datetime

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["window_start"] = self.window_start.isoformat()
        row["window_end"] = self.window_end.isoformat()
        row["generated_at"] = self.generated_at.isoformat()
        return row
