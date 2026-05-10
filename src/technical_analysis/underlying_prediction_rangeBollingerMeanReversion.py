from __future__ import annotations

from .underlying_prediction_common import PredictionInput, detect_regime, signal_bollinger_mean_reversion

STRATEGY_NAME = "rangeBollingerMeanReversion"


def predict(window: PredictionInput) -> str:
    """Range-regime gated Bollinger-band mean reversion."""
    if detect_regime(window) != "RANGE":
        return "NO_POSITION"
    return signal_bollinger_mean_reversion(window)

