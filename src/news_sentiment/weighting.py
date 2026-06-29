from __future__ import annotations

from collections import Counter
from datetime import datetime

from src.news_sentiment.schemas import CompositeSignal, EnrichedArticle, NewsArticle, SectorTag, SentimentResult
from src.news_sentiment.config import NEGATIVE_THRESHOLD, POSITIVE_THRESHOLD
from src.news_sentiment.sector_weights import load_nifty50_sector_weights


def sector_weight(tags: list[SectorTag], sector_weights: dict[str, float] | None = None) -> float:
    if not tags:
        return 0.0
    weights = sector_weights or load_nifty50_sector_weights()
    weighted = 0.0
    for tag in tags:
        weighted += float(weights.get(tag.sector, 0.0)) * float(tag.confidence)
    return min(1.0, max(0.0, weighted))


def enrich_article(
    article: NewsArticle,
    sentiment: SentimentResult,
    sectors: list[SectorTag],
    sector_weights: dict[str, float] | None = None,
) -> EnrichedArticle:
    weight = sector_weight(sectors, sector_weights=sector_weights)
    weighted_sentiment = float(sentiment.score) * float(sentiment.confidence) * weight
    return EnrichedArticle(article, sentiment, sectors, weight, weighted_sentiment)


def composite_label(score: float) -> str:
    if score >= POSITIVE_THRESHOLD:
        return "positive"
    if score <= NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def build_composite_signal(
    target_date: str,
    window_start: datetime,
    window_end: datetime,
    enriched: list[EnrichedArticle],
    generated_at: datetime,
) -> CompositeSignal:
    usable = [item for item in enriched if item.sector_weight > 0 and item.sentiment.confidence > 0]
    denominator = sum(item.sector_weight * item.sentiment.confidence for item in usable)
    weighted_signal_sum = sum(item.weighted_sentiment for item in usable)
    composite = weighted_signal_sum / denominator if denominator else 0.0
    composite = max(-1.0, min(1.0, composite))
    labels = Counter(item.sentiment.label for item in enriched)
    sources = Counter(item.article.provider for item in enriched)
    mean_confidence = (
        sum(item.sentiment.confidence for item in usable) / len(usable) if usable else 0.0
    )
    return CompositeSignal(
        target_date=target_date,
        window_start=window_start,
        window_end=window_end,
        article_count=len(enriched),
        usable_article_count=len(usable),
        composite_score=round(composite, 4),
        composite_label=composite_label(composite),
        mean_confidence=round(mean_confidence, 4),
        positive_count=int(labels.get("positive", 0)),
        neutral_count=int(labels.get("neutral", 0)),
        negative_count=int(labels.get("negative", 0)),
        weighted_signal_sum=round(weighted_signal_sum, 6),
        normalization_denominator=round(denominator, 6),
        source_mix=";".join(f"{source}:{count}" for source, count in sorted(sources.items())),
        generated_at=generated_at,
    )
