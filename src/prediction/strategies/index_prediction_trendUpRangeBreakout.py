from __future__ import annotations

from typing import Callable

from .index_prediction_common import PredictionInput

IndexPredictionFunc = Callable[[PredictionInput], str]


STRATEGY_NAME = "trendUpRangeBreakout"


def predict(window: PredictionInput) -> str:
    """Trend-up regime gated breakout. Input: rolling OHLC DataFrame (needs high/low/close). Output: CALL|PUT|NO_POSITION."""
    from .index_prediction_common import detect_regime, signal_range_breakout

    if detect_regime(window) != "TREND_UP":
        return "NO_POSITION"
    return signal_range_breakout(window)

