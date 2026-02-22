from __future__ import annotations

from collections import Counter
from src.prediction.contracts import EventItem, NewsItem, Signal, clamp01


class ImpactScoringAgent:
    """Converts event/news context into structured directional signals."""

    def score(self, instrument: str, news: list[NewsItem], events: list[EventItem]) -> list[Signal]:
        signals: list[Signal] = []

        # EventCalendarAgent already returns events scoped around the current as_of window.
        high_risk_next_24h = any(e.risk_level == "HIGH" for e in events)
        if high_risk_next_24h:
            signals.append(
                Signal(
                    source="EVENT",
                    instrument=instrument,
                    scope="INDEX",
                    direction="NO_POSITION",
                    strength=0.7,
                    confidence=0.7,
                    horizon="1D",
                    reason="High-risk event window (e.g., expiry/RBI/Fed) - reduce directional exposure",
                )
            )

        if any(e.risk_level == "MEDIUM" for e in events):
            signals.append(
                Signal(
                    source="EVENT",
                    instrument=instrument,
                    scope="INDEX",
                    direction="NO_POSITION",
                    strength=0.4,
                    confidence=0.6,
                    horizon="1D",
                    reason="Medium event risk - be selective / reduce confidence",
                )
            )

        direction_counts = Counter((n.category, _news_to_direction(n)) for n in news)
        net_score = 0.0

        for item in news:
            direction = _news_to_direction(item)
            strength = 0.3

            if item.category in ("MACRO_GLOBAL", "INDIA_POLICY"):
                strength += 0.2
            if instrument.upper() == "BANKNIFTY" and "BANKS" in item.entities:
                strength += 0.2

            base_conf = item.confidence if item.confidence else 0.5
            agreement = direction_counts[(item.category, direction)]
            if agreement > 1:
                base_conf = min(0.75, base_conf + 0.05 * (agreement - 1))

            reason = item.title[:160]
            signals.append(
                Signal(
                    source="NEWS",
                    instrument=instrument,
                    scope="INDEX",
                    direction=direction,
                    strength=strength,
                    confidence=clamp01(base_conf),
                    horizon="1D",
                    reason=reason,
                    metadata={
                        "category": item.category,
                        "entities": item.entities,
                        "news_id": item.news_id,
                    },
                )
            )

            net_score += _dir_to_sign(direction) * strength

        if news:
            if net_score >= 0.6:
                signals.append(
                    Signal(
                        source="NEWS",
                        instrument=instrument,
                        scope="INDEX",
                        direction="CALL",
                        strength=0.5,
                        confidence=0.65,
                        horizon="1D",
                        reason="Aggregate news flow tilted positive",
                    )
                )
            elif net_score <= -0.6:
                signals.append(
                    Signal(
                        source="NEWS",
                        instrument=instrument,
                        scope="INDEX",
                        direction="PUT",
                        strength=0.5,
                        confidence=0.65,
                        horizon="1D",
                        reason="Aggregate news flow tilted negative",
                    )
                )

        return signals


def _news_to_direction(item: NewsItem) -> str:
    if item.sentiment == "POS":
        return "CALL"
    if item.sentiment == "NEG":
        return "PUT"
    return "NO_POSITION"


def _dir_to_sign(direction: str) -> int:
    if direction == "CALL":
        return 1
    if direction == "PUT":
        return -1
    return 0
