from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.news_sentiment.config import ARTICLE_SENTIMENT_STORE, ARTICLE_STORE, COMPOSITE_SIGNAL_STORE
from src.news_sentiment.schemas import CompositeSignal, EnrichedArticle, NewsArticle

IST = ZoneInfo("Asia/Kolkata")

ARTICLE_COLUMNS = [
    "article_id", "source", "url", "title", "summary", "published_at", "fetched_at",
    "region", "provider",
]

ENRICHED_COLUMNS = ARTICLE_COLUMNS + [
    "target_date", "window_start", "window_end", "sentiment_label", "sentiment_score",
    "sentiment_confidence", "sentiment_model", "sectors", "sector_confidences",
    "sector_weight", "weighted_sentiment",
]

COMPOSITE_COLUMNS = [
    "target_date", "window_start", "window_end", "article_count", "usable_article_count",
    "composite_score", "composite_label", "mean_confidence", "positive_count",
    "neutral_count", "negative_count", "weighted_signal_sum", "normalization_denominator",
    "source_mix", "generated_at",
]


def load_article_store(path: Path = ARTICLE_STORE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=ARTICLE_COLUMNS)
    return pd.read_csv(path)


def append_articles(articles: list[NewsArticle], path: Path = ARTICLE_STORE) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_article_store(path)
    incoming = pd.DataFrame([article.to_row() for article in articles], columns=ARTICLE_COLUMNS)
    if incoming.empty:
        return existing
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.drop_duplicates(subset=["article_id"], keep="last")
    merged = merged.sort_values(["published_at", "source", "title"]).reset_index(drop=True)
    merged.to_csv(path, index=False)
    return merged


def append_enriched_articles(
    enriched: list[EnrichedArticle],
    target_date: str,
    window_start: datetime,
    window_end: datetime,
    path: Path = ARTICLE_SENTIMENT_STORE,
) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=ENRICHED_COLUMNS)
    incoming = pd.DataFrame(
        [item.to_row(target_date, window_start, window_end) for item in enriched],
        columns=ENRICHED_COLUMNS,
    )
    if incoming.empty:
        return existing
    if "target_date" in existing.columns:
        existing = existing[existing["target_date"] != target_date]
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.drop_duplicates(subset=["target_date", "article_id"], keep="last")
    merged = merged.sort_values(["target_date", "published_at", "source"]).reset_index(drop=True)
    merged.to_csv(path, index=False)
    return merged


def append_composite_signal(signal: CompositeSignal, path: Path = COMPOSITE_SIGNAL_STORE) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=COMPOSITE_COLUMNS)
    incoming = pd.DataFrame([signal.to_row()], columns=COMPOSITE_COLUMNS)
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.drop_duplicates(subset=["target_date"], keep="last")
    merged = merged.sort_values("target_date").reset_index(drop=True)
    merged = merged.reindex(columns=COMPOSITE_COLUMNS)
    merged.to_csv(path, index=False)
    return merged
