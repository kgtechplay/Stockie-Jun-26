from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass
class Signal:
    source: str
    instrument: str
    scope: str
    direction: str
    strength: float
    confidence: float
    horizon: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
    signal_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        self.strength = clamp01(self.strength)
        self.confidence = clamp01(self.confidence)


@dataclass
class NewsItem:
    title: str
    source: str
    category: str
    entities: list[str]
    sentiment: str
    confidence: float
    published_at: datetime | None = None
    url: str | None = None
    summary: str | None = None
    news_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        self.confidence = clamp01(self.confidence)


@dataclass
class EventItem:
    name: str
    event_type: str
    risk_level: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    expected_volatility: float | None = None
    notes: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class PredictionOutput:
    instrument: str
    timestamp: datetime
    final_decision: str
    confidence: float
    regime: str
    reasons: list[str]
    component_signals: list[Signal]

    def __post_init__(self) -> None:
        self.confidence = clamp01(self.confidence)
