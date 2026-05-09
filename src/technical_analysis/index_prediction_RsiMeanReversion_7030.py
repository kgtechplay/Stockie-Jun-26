from __future__ import annotations

from .index_prediction_common import PredictionInput, signal_rsi_mean_reversion

STRATEGY_NAME = "RsiMeanReversion_7030"


def predict(window: PredictionInput) -> str:
    """RSI mean reversion (70/30)."""
    return signal_rsi_mean_reversion(window, rsi_period=14, overbought=70.0, oversold=30.0)
