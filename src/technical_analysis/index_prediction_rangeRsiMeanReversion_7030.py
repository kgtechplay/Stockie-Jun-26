from __future__ import annotations

from .index_prediction_common import PredictionInput, detect_regime, signal_rsi_mean_reversion

STRATEGY_NAME = "rangeRsiMeanReversion_7030"


def predict(window: PredictionInput) -> str:
    """Range-regime gated RSI mean reversion (70/30)."""
    if detect_regime(window) != "RANGE":
        return "NO_POSITION"
    return signal_rsi_mean_reversion(window, rsi_period=14, overbought=70.0, oversold=30.0)
