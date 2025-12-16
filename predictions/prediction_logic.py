# prediction_logic.py
"""
Prediction strategy functions and registry for index direction predictions.

This module contains all prediction strategy implementations and the strategy registry.
"""

from typing import Callable
import pandas as pd

# Constants used by prediction strategies
LOOKBACK_DAYS = 10
TREND_THRESH = 0.003  # 0.3% move over last 10 days to call trend

# Type definition for prediction functions
# Input: window_closes (pd.Series), optional parameters
# Output: str ("CALL", "PUT", or "NO_POSITION")
PredictionFunction = Callable[[pd.Series], str]


def predict_trend_following(window_closes: pd.Series,
                            trend_thresh: float = TREND_THRESH) -> str:
    """
    Strategy 1: Trend Following
    Use last LOOKBACK_DAYS closes to decide:
      - "CALL" (expect up), "PUT" (expect down), or "NO_POSITION".
    Original strategy - looks for trend direction and mean comparison.
    """
    first_close = float(window_closes.iloc[0])
    last_close = float(window_closes.iloc[-1])
    mean_close = float(window_closes.mean())

    trend_pct = (last_close - first_close) / first_close if first_close != 0 else 0.0

    if trend_pct > trend_thresh and last_close > mean_close:
        return "CALL"
    elif trend_pct < -trend_thresh and last_close < mean_close:
        return "PUT"
    else:
        return "NO_POSITION"


def predict_momentum(window_closes: pd.Series,
                     momentum_thresh: float = 0.005) -> str:
    """
    Strategy 2: Momentum
    Uses recent momentum (last 3 days vs previous 3 days) to predict direction.
    More sensitive to short-term momentum changes.
    """
    if len(window_closes) < 6:
        return "NO_POSITION"
    
    recent_avg = float(window_closes.iloc[-3:].mean())
    previous_avg = float(window_closes.iloc[-6:-3].mean()) if len(window_closes) >= 6 else float(window_closes.iloc[0])
    
    momentum_pct = (recent_avg - previous_avg) / previous_avg if previous_avg != 0 else 0.0
    
    if momentum_pct > momentum_thresh:
        return "CALL"
    elif momentum_pct < -momentum_thresh:
        return "PUT"
    else:
        return "NO_POSITION"


def predict_mean_reversion(window_closes: pd.Series,
                           deviation_thresh: float = 0.01) -> str:
    """
    Strategy 3: Mean Reversion
    Predicts opposite direction when price deviates significantly from mean.
    Expects price to revert to mean.
    """
    current_close = float(window_closes.iloc[-1])
    mean_close = float(window_closes.mean())
    std_close = float(window_closes.std())
    
    if std_close == 0:
        return "NO_POSITION"
    
    z_score = (current_close - mean_close) / std_close
    
    if z_score > 1.0:  # Price is significantly above mean, expect reversion down
        return "PUT"
    elif z_score < -1.0:  # Price is significantly below mean, expect reversion up
        return "CALL"
    else:
        return "NO_POSITION"


# Registry of available prediction strategies
PREDICTION_STRATEGIES = {
    "trendFollowing": predict_trend_following,
    "momentum": predict_momentum,
    "meanReversion": predict_mean_reversion,
}

