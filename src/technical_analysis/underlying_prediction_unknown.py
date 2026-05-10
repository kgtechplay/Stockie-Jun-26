from __future__ import annotations

from .underlying_prediction_common import PredictionInput

STRATEGY_NAME = "unknown"


def predict(window: PredictionInput) -> str:
    """Unknown baseline strategy. Always NO_POSITION."""
    _ = window
    return "NO_POSITION"

