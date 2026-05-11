from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

import pandas as pd

from src.technical_analysis.prediction.underlying_registry import (
    detect_regime,
    load_underlying_prediction_strategies,
)

REGIME_STRATEGY_GROUPS = {
    "TREND_UP": ["trendUpRangeBreakout"],
    "TREND_DOWN": ["trendDownRangeBreakout"],
    "RANGE": ["rangeBollingerMeanReversion", "rangeRsiMeanReversion_6535"],
    "CHOPPY": ["choppy"],
    "UNKNOWN": ["unknown"],
}


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


def get_underlying_strategy_predictions(
    window: pd.Series | pd.DataFrame,
    strategies: list[str] | None = None,
) -> dict[str, str]:
    registry = load_underlying_prediction_strategies()
    selected = strategies or sorted(registry.keys())
    output: dict[str, str] = {}
    for name in selected:
        fn = registry.get(name)
        if fn is None:
            continue
        output[name] = fn(window)
    return output


def majority_vote(predictions: dict[str, str]) -> str:
    calls_puts = [d for d in predictions.values() if d in ("CALL", "PUT")]
    if not calls_puts:
        return "NO_POSITION"
    counts = Counter(calls_puts)
    if counts["CALL"] > counts["PUT"]:
        return "CALL"
    if counts["PUT"] > counts["CALL"]:
        return "PUT"
    return "NO_POSITION"


def get_regime_strategy_columns(regime: str, strategy_columns: list[str]) -> list[str]:
    regime_group = REGIME_STRATEGY_GROUPS.get(str(regime or "").upper(), [])
    selected = [name for name in regime_group if name in strategy_columns]
    return selected or strategy_columns


def aggregate_underlying_decision(row: pd.Series, strategy_columns: list[str]) -> str:
    selected_columns = get_regime_strategy_columns(str(row.get("regime", "")), strategy_columns)
    predictions = {
        strategy: row.get(strategy)
        for strategy in selected_columns
        if row.get(strategy) in ("CALL", "PUT", "NO_POSITION")
    }
    return majority_vote(predictions)


def add_aggregate_decision_column(
    predictions_df: pd.DataFrame,
    strategy_columns: list[str],
    output_column: str = "aggregate_decision",
) -> pd.DataFrame:
    out = predictions_df.copy()
    out[output_column] = out.apply(
        lambda row: aggregate_underlying_decision(row, strategy_columns),
        axis=1,
    )
    return out


def run_underlying_prediction(
    instrument: str,
    window: pd.Series | pd.DataFrame,
    as_of: datetime,
    strategies: list[str] | None = None,
) -> PredictionOutput:
    regime = detect_regime(window)
    per_strategy = get_underlying_strategy_predictions(window=window, strategies=strategies)
    selected_names = get_regime_strategy_columns(regime, list(per_strategy.keys()))
    final_decision = majority_vote(
        {name: per_strategy[name] for name in selected_names if name in per_strategy}
    )

    ta_signals: list[Signal] = []
    for name, direction in per_strategy.items():
        ta_signals.append(
            Signal(
                source="TA",
                instrument=instrument,
                scope="UNDERLYING",
                direction=direction,
                strength=0.5 if direction in ("CALL", "PUT") else 0.0,
                confidence=0.55,
                horizon="1D",
                reason=f"TA:{name}",
                metadata={"strategy": name},
            )
        )

    non_neutral = [per_strategy[name] for name in selected_names if per_strategy.get(name) in ("CALL", "PUT")]
    confidence = 0.5 if not non_neutral else min(0.85, len(non_neutral) / max(len(per_strategy), 1))
    reasons = [f"regime:{regime}"]
    reasons.extend(f"TA:{name}:{per_strategy[name]}" for name in selected_names if name in per_strategy)
    reasons = reasons[:5]

    return PredictionOutput(
        instrument=instrument,
        timestamp=as_of,
        final_decision=final_decision,
        confidence=clamp01(confidence),
        regime=regime,
        reasons=reasons,
        component_signals=ta_signals,
    )

