from __future__ import annotations

from .index_prediction_common import PredictionInput, detect_regime, signal_rsi_mean_reversion

STRATEGY_NAME = "rangeRsiMeanReversion_6535"


def predict(window: PredictionInput) -> str:
    """Range-regime gated RSI mean reversion (65/35)."""
    if detect_regime(window) != "RANGE":
        return "NO_POSITION"
    return signal_rsi_mean_reversion(window, rsi_period=14, overbought=65.0, oversold=35.0)
