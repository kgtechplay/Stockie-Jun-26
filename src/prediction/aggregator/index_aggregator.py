from __future__ import annotations

from collections import Counter
from datetime import datetime

import pandas as pd

from src.prediction.contracts import PredictionOutput, Signal, clamp01
from src.technical_analysis.index_registry import detect_regime, load_index_prediction_strategies


def get_index_strategy_predictions(
    window: pd.Series | pd.DataFrame,
    strategies: list[str] | None = None,
) -> dict[str, str]:
    registry = load_index_prediction_strategies()
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


def run_index_prediction(
    instrument: str,
    window: pd.Series | pd.DataFrame,
    as_of: datetime,
    strategies: list[str] | None = None,
) -> PredictionOutput:
    regime = detect_regime(window)
    per_strategy = get_index_strategy_predictions(window=window, strategies=strategies)
    final_decision = majority_vote(per_strategy)

    ta_signals: list[Signal] = []
    for name, direction in per_strategy.items():
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

    non_neutral = [d for d in per_strategy.values() if d in ("CALL", "PUT")]
    confidence = 0.5 if not non_neutral else min(0.85, len(non_neutral) / max(len(per_strategy), 1))
    reasons = [f"TA:{name}:{decision}" for name, decision in per_strategy.items()][:5]

    return PredictionOutput(
        instrument=instrument,
        timestamp=as_of,
        final_decision=final_decision,
        confidence=clamp01(confidence),
        regime=regime,
        reasons=reasons,
        component_signals=ta_signals,
    )
