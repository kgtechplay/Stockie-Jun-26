from __future__ import annotations

from ..underlying_prediction_common import PredictionInput, signal_bollinger_mean_reversion

STRATEGY_NAME = "BollingerMeanReversion"


def predict(window: PredictionInput) -> str:
    """Bollinger-band mean reversion."""
    return signal_bollinger_mean_reversion(window)


