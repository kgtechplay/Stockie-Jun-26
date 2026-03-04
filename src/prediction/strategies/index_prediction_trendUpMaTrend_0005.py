from __future__ import annotations

from .index_prediction_common import PredictionInput, detect_regime, signal_ma_trend

STRATEGY_NAME = "trendUpMaTrend_0005"


def predict(window: PredictionInput) -> str:
    """Trend-up regime gated MA trend (band 0.05%)."""
    if detect_regime(window) != "TREND_UP":
        return "NO_POSITION"
    return signal_ma_trend(window, short_window=5, long_window=20, band=0.0005)

