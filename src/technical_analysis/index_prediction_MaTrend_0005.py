from __future__ import annotations

from .index_prediction_common import PredictionInput, signal_ma_trend

STRATEGY_NAME = "MaTrend_0005"


def predict(window: PredictionInput) -> str:
    """MA trend (band 0.05%). Input: rolling close series/DataFrame. Output: CALL|PUT|NO_POSITION."""
    return signal_ma_trend(window, short_window=5, long_window=20, band=0.0005)
