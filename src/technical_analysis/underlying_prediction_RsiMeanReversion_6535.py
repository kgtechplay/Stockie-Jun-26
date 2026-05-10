from __future__ import annotations

from .underlying_prediction_common import PredictionInput, signal_rsi_mean_reversion

STRATEGY_NAME = "RsiMeanReversion_6535"


def predict(window: PredictionInput) -> str:
    """RSI mean reversion (65/35)."""
    return signal_rsi_mean_reversion(window, rsi_period=14, overbought=65.0, oversold=35.0)

