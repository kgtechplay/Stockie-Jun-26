from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.prediction.agents.event_calendar_agent import EventCalendarAgent
from src.prediction.agents.impact_scoring_agent import ImpactScoringAgent
from src.prediction.agents.news_agent import NewsAgent
from src.prediction.contracts import PredictionOutput, Signal, clamp01
from src.prediction.technical.strategies import PREDICTION_STRATEGIES, detect_regime


DEFAULT_STRATEGIES = [
    "MaTrend_001",
    "RsiMeanReversion_7030",
    "BollingerMeanReversion",
    "trendUpMaTrend_001",
    "trendDownMaTrend_001",
]


def run_index_prediction(
    instrument: str,
    window: pd.Series | pd.DataFrame,
    as_of: datetime,
    strategies: list[str] | None = None,
) -> PredictionOutput:
    selected = strategies or DEFAULT_STRATEGIES
    regime = detect_regime(window)

    ta_signals: list[Signal] = []
    for name in selected:
        fn = PREDICTION_STRATEGIES.get(name)
        if fn is None:
            continue
        direction = fn(window)
        ta_signals.append(
            Signal(
                source="TA",
                instrument=instrument,
                scope="INDEX",
                direction=direction,
                strength=0.5 if direction in ("CALL", "PUT") else 0.0,
                confidence=0.55,
                horizon="1D",
                reason=f"TA:{name}",
                metadata={"strategy": name},
            )
        )

    events = EventCalendarAgent().get_upcoming_events(instrument, as_of)
    news = NewsAgent().fetch_news(as_of)
    impact_signals = ImpactScoringAgent().score(instrument, news, events)

    component_signals = ta_signals + impact_signals

    event_gate = next(
        (
            s
            for s in component_signals
            if s.source == "EVENT" and s.direction == "NO_POSITION" and s.strength >= 0.6
        ),
        None,
    )

    score = 0.0
    for s in component_signals:
        weight = _source_weight(s.source, regime)
        score += _dir_to_sign(s.direction) * s.strength * s.confidence * weight

    long_threshold = 0.25
    short_threshold = -0.25
    if regime == "CHOPPY":
        long_threshold = 0.35
        short_threshold = -0.35

    if event_gate is not None:
        final_decision = "NO_POSITION"
        ta_conf = max((s.confidence for s in ta_signals), default=0.6)
        confidence = min(0.65, ta_conf)
    else:
        if score > long_threshold:
            final_decision = "CALL"
        elif score < short_threshold:
            final_decision = "PUT"
        else:
            final_decision = "NO_POSITION"

        confidence = min(0.85, 0.5 + abs(score))
        if regime == "CHOPPY":
            confidence = max(0.0, confidence - 0.1)

    ranked = sorted(
        component_signals,
        key=lambda s: s.strength * s.confidence * _source_weight(s.source, regime),
        reverse=True,
    )

    reasons: list[str] = []
    if event_gate is not None:
        reasons.append(event_gate.reason)

    for sig in ranked:
        if sig.reason not in reasons:
            reasons.append(sig.reason)
        if len(reasons) >= 5:
            break

    return PredictionOutput(
        instrument=instrument,
        timestamp=as_of,
        final_decision=final_decision,
        confidence=clamp01(confidence),
        regime=regime,
        reasons=reasons[:5],
        component_signals=component_signals,
    )


def _source_weight(source: str, regime: str) -> float:
    base = {"TA": 1.0, "NEWS": 0.8, "EVENT": 1.2}.get(source, 1.0)
    if regime == "CHOPPY" and source == "TA":
        return base * 0.85
    return base


def _dir_to_sign(direction: str) -> int:
    if direction == "CALL":
        return 1
    if direction == "PUT":
        return -1
    return 0
