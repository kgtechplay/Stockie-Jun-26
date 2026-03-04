from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.prediction.contracts import NewsItem


MACRO_KEYWORDS = {"fed", "cpi", "jobs", "rate", "bond", "treasury", "brent", "crude", "dxy"}
INDIA_POLICY_KEYWORDS = {"rbi", "sebi", "budget", "gst", "inflation", "repo"}
SECTOR_KEYWORDS = {"bank", "it", "pharma", "auto"}

ENTITY_MAP: dict[str, list[str]] = {
    "hdfc": ["HDFCBANK", "BANKS"],
    "reliance": ["RELIANCE"],
    "infosys": ["INFY", "IT"],
    "icici": ["ICICIBANK", "BANKS"],
    "tcs": ["TCS", "IT"],
}

POS_WORDS = {"surge", "beats", "record", "upgrades"}
NEG_WORDS = {"falls", "miss", "downgrade", "probe", "ban", "weak"}


def classify_news(title: str) -> str:
    lower = title.lower()
    if any(k in lower for k in MACRO_KEYWORDS):
        return "MACRO_GLOBAL"
    if any(k in lower for k in INDIA_POLICY_KEYWORDS):
        return "INDIA_POLICY"
    if any(k in lower for k in SECTOR_KEYWORDS):
        return "SECTOR"
    return "UNKNOWN"


def extract_entities(title: str) -> list[str]:
    lower = title.lower()
    entities: list[str] = []
    for key, mapped in ENTITY_MAP.items():
        if key in lower:
            for ent in mapped:
                if ent not in entities:
                    entities.append(ent)
    return entities


def infer_sentiment(title: str) -> tuple[str, float]:
    lower = title.lower()
    if any(k in lower for k in POS_WORDS):
        return "POS", 0.6
    if any(k in lower for k in NEG_WORDS):
        return "NEG", 0.6
    return "NEUTRAL", 0.4


class NewsAgent:
    """MVP news agent. Uses local sample JSON if configured."""

    def fetch_news(self, as_of: datetime, lookback_hours: int = 24) -> list[NewsItem]:
        sample_path = os.getenv("NEWS_SAMPLE_JSON_PATH", "").strip()
        if not sample_path:
            return []

        path = Path(sample_path)
        if not path.exists() or not path.is_file():
            return []

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, list):
            return []

        cutoff = as_of - timedelta(hours=lookback_hours)
        items: list[NewsItem] = []

        for row in raw:
            if not isinstance(row, dict):
                continue

            title = str(row.get("title", "")).strip()
            if not title:
                continue

            published_at = _parse_dt(row.get("published_at"))
            if published_at is not None and published_at < cutoff:
                continue

            sentiment, sent_conf = infer_sentiment(title)
            category = str(row.get("category") or classify_news(title))
            entities = row.get("entities")
            if not isinstance(entities, list):
                entities = extract_entities(title)

            confidence = _safe_float(row.get("confidence"), sent_conf)

            items.append(
                NewsItem(
                    title=title,
                    source=str(row.get("source") or "UNKNOWN"),
                    published_at=published_at,
                    url=str(row.get("url")) if row.get("url") else None,
                    category=category,
                    entities=[str(e) for e in entities],
                    summary=str(row.get("summary")) if row.get("summary") else None,
                    sentiment=str(row.get("sentiment") or sentiment),
                    confidence=confidence,
                )
            )

        return items


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)

