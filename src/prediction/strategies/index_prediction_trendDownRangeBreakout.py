from __future__ import annotations

from .index_prediction_common import PredictionInput, detect_regime, signal_range_breakout

STRATEGY_NAME = "trendDownRangeBreakout"


def predict(window: PredictionInput) -> str:
    """Trend-down regime gated breakout."""
    if detect_regime(window) != "TREND_DOWN":
        return "NO_POSITION"
    return signal_range_breakout(window)

