from __future__ import annotations

from .index_prediction_common import PredictionInput

STRATEGY_NAME = "choppy"


def predict(window: PredictionInput) -> str:
    """Choppy baseline strategy. Always NO_POSITION."""
    _ = window
    return "NO_POSITION"
