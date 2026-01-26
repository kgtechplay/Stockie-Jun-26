"""
prediction_logic.py

Central place for index-direction prediction strategies (e.g., NIFTY, BANKNIFTY).

All strategies take a rolling "window" of recent data and return:
    "CALL", "PUT", or "NO_POSITION"

The window can be:
    * pd.Series          -> interpreted as a series of close prices
    * pd.DataFrame       -> must contain at least 'close_price'
                            (and may optionally contain 'high_price', 'low_price')

We provide:
    - Indicator-based strategies:
        * RSI mean reversion
        * Moving-average trend (momentum)
        * Bollinger-band mean reversion
        * Donchian-style range breakout
    - Regime detection: TREND_UP / TREND_DOWN / RANGE / CHOPPY / UNKNOWN
    - 8 regime+strategy combinations:
        * trendUp_rangeBreakout
        * trendUp_maTrend
        * trendDown_rangeBreakout
        * trendDown_maTrend
        * range_rsiMeanReversion
        * range_bollingerReversion
        * choppy
        * unknown
"""

from typing import Callable, Union, Dict
import math
import pandas as pd

# ---------------------------------------------------------------------------
# Types & shared constants
# ---------------------------------------------------------------------------

# Window passed into each prediction strategy:
# - Either a Series of closes
# - Or a DataFrame with 'close_price' (and optionally highs/lows)
PredictionInput = Union[pd.Series, pd.DataFrame]

# Strategy signature: input window -> "CALL" / "PUT" / "NO_POSITION"
PredictionFunction = Callable[[PredictionInput], str]

# General thresholds (tune via backtests)
DEFAULT_LOOKBACK_DAYS = 20      # Typical lookback for indicator-based strategies
DEFAULT_TREND_Z_THRESH = 1.0    # Strength threshold for trend detection (z-score units)


# ---------------------------------------------------------------------------
# Helpers to extract data from the rolling window
# ---------------------------------------------------------------------------

def _get_closes(window: PredictionInput) -> pd.Series:
    """
    Helper:
        - If `window` is a Series -> treat it as closes.
        - If `window` is a DataFrame -> use the 'close_price' column.

    Always returns a float Series.
    """
    if isinstance(window, pd.Series):
        return window.astype(float)

    if not isinstance(window, pd.DataFrame):
        raise TypeError(f"window must be pd.Series or pd.DataFrame, got {type(window)}")

    if "close_price" not in window.columns:
        raise ValueError("DataFrame window must contain a 'close_price' column")

    return window["close_price"].astype(float)


def _get_column(window: PredictionInput, col: str) -> Union[pd.Series, None]:
    """
    Helper to fetch a column (e.g. 'high_price', 'low_price') from the window
    when the window is a DataFrame.

    Returns:
        - pd.Series if the column exists
        - None if not available
    """
    if isinstance(window, pd.DataFrame) and col in window.columns:
        return window[col].astype(float)
    return None


# ---------------------------------------------------------------------------
# Strategy: RSI Mean Reversion
# ---------------------------------------------------------------------------

def _compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute RSI (Relative Strength Index) using Wilder-style EMA smoothing.

    Args:
        closes: Series of close prices
        period: RSI lookback period (typically 14)

    Returns:
        Series of RSI values aligned with `closes`
    """
    delta = closes.diff()

    # Positive and negative moves
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    # Wilder EMA: alpha = 1 / period
    roll_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = roll_up / roll_down.replace(0.0, 1e-9)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def predict_rsi_mean_reversion(
    window: PredictionInput,
    rsi_period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> str:
    """
    RSI Mean Reversion strategy.

    Idea:
      - RSI above `overbought` (e.g. 70) => market is stretched up
            -> expect pullback -> "PUT"
      - RSI below `oversold` (e.g. 30)  => market is stretched down
            -> expect bounce -> "CALL"
      - Otherwise: "NO_POSITION"

    Works best when the index is range-bound / not strongly trending.
    """
    closes = _get_closes(window)

    # Need enough data to compute a stable RSI
    if len(closes) < rsi_period + 2:
        return "NO_POSITION"

    rsi = _compute_rsi(closes, period=rsi_period)
    current_rsi = float(rsi.iloc[-1])

    if current_rsi >= overbought:
        return "PUT"
    elif current_rsi <= oversold:
        return "CALL"
    else:
        return "NO_POSITION"


# ---------------------------------------------------------------------------
# Strategy: Moving-Average Trend (Momentum-style)
# ---------------------------------------------------------------------------

def predict_ma_trend(
    window: PredictionInput,
    short_window: int = 5,
    long_window: int = 20,
    band: float = 0.001,  # 0.1% neutrality band to avoid whipsaws
) -> str:
    """
    Moving-Average Trend strategy (momentum-style).

    Logic:
      - Compute short and long simple moving averages (SMA).
      - spread_pct = (short_ma - long_ma) / long_ma

      If:
        spread_pct > +band -> "CALL" (uptrend)
        spread_pct < -band -> "PUT"  (downtrend)
        otherwise          -> "NO_POSITION"

    Notes:
      - Requires window length >= long_window.
      - `band` controls how strong the MA difference must be to count as a trend.
    """
    closes = _get_closes(window)
    n = len(closes)
    if n < long_window:
        return "NO_POSITION"

    short_ma = float(closes.iloc[-short_window:].mean())
    long_ma = float(closes.iloc[-long_window:].mean())

    if long_ma == 0:
        return "NO_POSITION"

    spread_pct = (short_ma - long_ma) / long_ma

    if spread_pct > band:
        return "CALL"
    elif spread_pct < -band:
        return "PUT"
    else:
        return "NO_POSITION"


# ---------------------------------------------------------------------------
# Strategy: Bollinger Band Mean Reversion
# ---------------------------------------------------------------------------

def predict_bollinger_mean_reversion(
    window: PredictionInput,
    bb_period: int = 20,
    num_std: float = 2.0,
) -> str:
    """
    Bollinger-band Mean Reversion strategy.

    Logic:
      - Compute rolling mean (MA) and standard deviation over bb_period.
      - Upper band = MA + num_std * std
        Lower band = MA - num_std * std

      If:
        close > upper band -> "PUT"  (stretched up)
        close < lower band -> "CALL" (stretched down)
        otherwise          -> "NO_POSITION"
    """
    closes = _get_closes(window)
    if len(closes) < bb_period:
        return "NO_POSITION"

    ma = closes.rolling(bb_period).mean().iloc[-1]
    std = closes.rolling(bb_period).std(ddof=0).iloc[-1]

    if pd.isna(ma) or pd.isna(std) or std == 0:
        return "NO_POSITION"

    upper = ma + num_std * std
    lower = ma - num_std * std
    last = float(closes.iloc[-1])

    if last > upper:
        return "PUT"
    elif last < lower:
        return "CALL"
    else:
        return "NO_POSITION"


# ---------------------------------------------------------------------------
# Strategy: Donchian-style Range Breakout (Momentum)
# ---------------------------------------------------------------------------

def predict_range_breakout(
    window: PredictionInput,
    lookback: int = 20,
) -> str:
    """
    Donchian Channel / Range Breakout strategy (momentum-style).

    Requires the window to be a DataFrame that contains:
      - 'close_price'
      - 'high_price'
      - 'low_price'

    Logic:
      - recent_high = max(high) over last `lookback` days (excluding today)
      - recent_low  = min(low)  over last `lookback` days (excluding today)
      - last_close  = today's close

      If:
        last_close > recent_high -> "CALL" (upside breakout)
        last_close < recent_low  -> "PUT"  (downside breakout)
        otherwise                -> "NO_POSITION"
    """
    closes = _get_closes(window)
    highs = _get_column(window, "high_price")
    lows = _get_column(window, "low_price")

    # If we don't have highs/lows, skip this strategy gracefully
    if highs is None or lows is None:
        return "NO_POSITION"

    if len(closes) < lookback + 1:
        return "NO_POSITION"

    last_close = float(closes.iloc[-1])

    # Use previous 'lookback' days, excluding the most recent one
    recent_high = float(highs.iloc[-(lookback + 1):-1].max())
    recent_low = float(lows.iloc[-(lookback + 1):-1].min())

    if last_close > recent_high:
        return "CALL"
    elif last_close < recent_low:
        return "PUT"
    else:
        return "NO_POSITION"


# ---------------------------------------------------------------------------
# Regime Detection: Trend vs Range vs Choppy
# ---------------------------------------------------------------------------

def detect_regime(
    window: PredictionInput,
    min_len: int = 15,
    trend_z_thresh: float = DEFAULT_TREND_Z_THRESH,
    chop_signchange_thresh: float = 0.6,
) -> str:
    """
    Classify the current regime (based only on closes).

    Returns one of:
        - "TREND_UP"
        - "TREND_DOWN"
        - "RANGE"
        - "CHOPPY"
        - "UNKNOWN"

    Heuristic:
      1) Compute daily returns and:
          - cumulative return (cum_ret)
          - realised volatility (vol)
          - trend_z = |cum_ret| / (vol * sqrt(N))

      2) Compute the fraction of sign changes in daily returns:
          - sign_change_frac = sign_changes / (N - 1)

      Rules:
        - If trend_z >= trend_z_thresh AND sign_change_frac <= 0.5:
             => TREND_UP (cum_ret > 0) or TREND_DOWN (cum_ret < 0)
        - Else:
             If sign_change_frac >= chop_signchange_thresh:
                 => CHOPPY
             else:
                 => RANGE
    """
    closes = _get_closes(window)
    n = len(closes)
    if n < min_len:
        return "UNKNOWN"

    rets = closes.pct_change().dropna()
    if len(rets) < 5:
        return "UNKNOWN"

    cum_ret = closes.iloc[-1] / closes.iloc[0] - 1.0
    vol = rets.std()

    if vol == 0 or pd.isna(vol):
        # No movement -> effectively a flat range
        return "RANGE"

    trend_z = abs(cum_ret) / (vol * math.sqrt(len(rets)))

    # Convert daily returns to sign (+1 / -1), then count sign changes
    signs = (rets > 0).astype(int) * 2 - 1  # +1 for up, -1 for down
    sign_changes = (signs.diff().abs() > 0).sum()
    sign_change_frac = sign_changes / (len(signs) - 1)

    # Strong directional move with relatively few flips -> trending
    if trend_z >= trend_z_thresh and sign_change_frac <= 0.5:
        if cum_ret > 0:
            return "TREND_UP"
        else:
            return "TREND_DOWN"

    # Not trending strongly: decide RANGE vs CHOPPY via sign-change frequency
    if sign_change_frac >= chop_signchange_thresh:
        return "CHOPPY"
    else:
        return "RANGE"


# ---------------------------------------------------------------------------
# 8 Regime+Strategy combinations
# ---------------------------------------------------------------------------

def predict_trendUp_rangeBreakout(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_UP, using rangeBreakout logic.

    - If regime != TREND_UP -> NO_POSITION
    - If regime == TREND_UP -> use predict_range_breakout
    """
    regime = detect_regime(window)
    if regime != "TREND_UP":
        return "NO_POSITION"
    return predict_range_breakout(window)


def predict_trendUp_maTrend(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_UP, using MA trend logic.

    - If regime != TREND_UP -> NO_POSITION
    - If regime == TREND_UP -> use predict_ma_trend
    """
    regime = detect_regime(window)
    if regime != "TREND_UP":
        return "NO_POSITION"
    return predict_ma_trend(window)


def predict_trendDown_rangeBreakout(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_DOWN, using rangeBreakout logic.

    - If regime != TREND_DOWN -> NO_POSITION
    - If regime == TREND_DOWN -> use predict_range_breakout
    """
    regime = detect_regime(window)
    if regime != "TREND_DOWN":
        return "NO_POSITION"
    return predict_range_breakout(window)


def predict_trendDown_maTrend(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_DOWN, using MA trend logic.

    - If regime != TREND_DOWN -> NO_POSITION
    - If regime == TREND_DOWN -> use predict_ma_trend
    """
    regime = detect_regime(window)
    if regime != "TREND_DOWN":
        return "NO_POSITION"
    return predict_ma_trend(window)


def predict_trendUp_maTrend_001(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_UP, using MA trend logic with band=0.001.

    - If regime != TREND_UP -> NO_POSITION
    - If regime == TREND_UP -> use predict_ma_trend with short_window=5, long_window=20, band=0.001
    """
    regime = detect_regime(window)
    if regime != "TREND_UP":
        return "NO_POSITION"
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.001)


def predict_trendUp_maTrend_0005(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_UP, using MA trend logic with band=0.0005.

    - If regime != TREND_UP -> NO_POSITION
    - If regime == TREND_UP -> use predict_ma_trend with short_window=5, long_window=20, band=0.0005
    """
    regime = detect_regime(window)
    if regime != "TREND_UP":
        return "NO_POSITION"
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.0005)


def predict_trendDown_maTrend_001(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_DOWN, using MA trend logic with band=0.001.

    - If regime != TREND_DOWN -> NO_POSITION
    - If regime == TREND_DOWN -> use predict_ma_trend with short_window=5, long_window=20, band=0.001
    """
    regime = detect_regime(window)
    if regime != "TREND_DOWN":
        return "NO_POSITION"
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.001)


def predict_trendDown_maTrend_0005(window: PredictionInput) -> str:
    """
    Only trades when regime is TREND_DOWN, using MA trend logic with band=0.0005.

    - If regime != TREND_DOWN -> NO_POSITION
    - If regime == TREND_DOWN -> use predict_ma_trend with short_window=5, long_window=20, band=0.0005
    """
    regime = detect_regime(window)
    if regime != "TREND_DOWN":
        return "NO_POSITION"
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.0005)


def predict_maTrend_001(window: PredictionInput) -> str:
    """
    MA trend strategy with band=0.001, without regime checking.

    - Uses predict_ma_trend with short_window=5, long_window=20, band=0.001
    - No regime filtering - trades in any market condition
    """
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.001)


def predict_maTrend_0005(window: PredictionInput) -> str:
    """
    MA trend strategy with band=0.0005, without regime checking.

    - Uses predict_ma_trend with short_window=5, long_window=20, band=0.0005
    - No regime filtering - trades in any market condition
    """
    return predict_ma_trend(window, short_window=5, long_window=20, band=0.0005)


def predict_range_rsiMeanReversion(window: PredictionInput) -> str:
    """
    Only trades when regime is RANGE, using RSI mean-reversion logic.

    - If regime != RANGE -> NO_POSITION
    - If regime == RANGE -> use predict_rsi_mean_reversion
    """
    regime = detect_regime(window)
    if regime != "RANGE":
        return "NO_POSITION"
    return predict_rsi_mean_reversion(window)


def predict_range_rsiMeanReversion_7030(window: PredictionInput) -> str:
    """
    Only trades when regime is RANGE, using RSI mean-reversion logic with overbought=70, oversold=30.

    - If regime != RANGE -> NO_POSITION
    - If regime == RANGE -> use predict_rsi_mean_reversion with rsi_period=14, overbought=70, oversold=30
    """
    regime = detect_regime(window)
    if regime != "RANGE":
        return "NO_POSITION"
    return predict_rsi_mean_reversion(window, rsi_period=14, overbought=70.0, oversold=30.0)


def predict_range_rsiMeanReversion_6535(window: PredictionInput) -> str:
    """
    Only trades when regime is RANGE, using RSI mean-reversion logic with overbought=65, oversold=35.

    - If regime != RANGE -> NO_POSITION
    - If regime == RANGE -> use predict_rsi_mean_reversion with rsi_period=14, overbought=65, oversold=35
    """
    regime = detect_regime(window)
    if regime != "RANGE":
        return "NO_POSITION"
    return predict_rsi_mean_reversion(window, rsi_period=14, overbought=65.0, oversold=35.0)


def predict_rsiMeanReversion_7030(window: PredictionInput) -> str:
    """
    RSI mean-reversion strategy with overbought=70, oversold=30, without regime checking.

    - Uses predict_rsi_mean_reversion with rsi_period=14, overbought=70, oversold=30
    - No regime filtering - trades in any market condition
    """
    return predict_rsi_mean_reversion(window, rsi_period=14, overbought=70.0, oversold=30.0)


def predict_rsiMeanReversion_6535(window: PredictionInput) -> str:
    """
    RSI mean-reversion strategy with overbought=65, oversold=35, without regime checking.

    - Uses predict_rsi_mean_reversion with rsi_period=14, overbought=65, oversold=35
    - No regime filtering - trades in any market condition
    """
    return predict_rsi_mean_reversion(window, rsi_period=14, overbought=65.0, oversold=35.0)


def predict_range_bollingerReversion(window: PredictionInput) -> str:
    """
    Only trades when regime is RANGE, using Bollinger-band mean reversion.

    - If regime != RANGE -> NO_POSITION
    - If regime == RANGE -> use predict_bollinger_mean_reversion
    """
    regime = detect_regime(window)
    if regime != "RANGE":
        return "NO_POSITION"
    return predict_bollinger_mean_reversion(window)


def predict_bollingerMeanReversion(window: PredictionInput) -> str:
    """
    Bollinger-band mean reversion strategy without regime checking.

    - Uses predict_bollinger_mean_reversion
    - No regime filtering - trades in any market condition
    """
    return predict_bollinger_mean_reversion(window)


def predict_choppy(window: PredictionInput) -> str:
    """
    Strategy for CHOPPY regime: by design, do nothing.

    - If regime == CHOPPY  -> NO_POSITION
    - Else                 -> NO_POSITION

    This gives you a baseline "no trades when choppy" to compare against.
    """
    regime = detect_regime(window)
    if regime == "CHOPPY":
        return "NO_POSITION"
    return "NO_POSITION"


def predict_unknown(window: PredictionInput) -> str:
    """
    Strategy for UNKNOWN regime: by design, do nothing.

    - If regime == UNKNOWN -> NO_POSITION
    - Else                 -> NO_POSITION

    Mainly useful as a diagnostic / baseline.
    """
    regime = detect_regime(window)
    if regime == "UNKNOWN":
        return "NO_POSITION"
    return "NO_POSITION"


# ---------------------------------------------------------------------------
# (Optional) Meta-strategy: Regime-Adaptive (kept for reference/use)
# ---------------------------------------------------------------------------

def predict_regime_adaptive(window: PredictionInput) -> str:
    """
    Regime-Adaptive meta-strategy (optional).

    This is a single combined strategy that:
      - Detects regime
      - Chooses an indicator strategy accordingly

    You can ignore this if you only want to backtest the 8 explicit combos,
    but it's kept as a convenience.
    """
    regime = detect_regime(window)

    if regime in ("TREND_UP", "TREND_DOWN"):
        breakout_sig = predict_range_breakout(window)
        if breakout_sig in ("CALL", "PUT"):
            return breakout_sig

        trend_sig = predict_ma_trend(window)
        if trend_sig in ("CALL", "PUT"):
            return trend_sig

        return "CALL" if regime == "TREND_UP" else "PUT"

    elif regime == "RANGE":
        rsi_sig = predict_rsi_mean_reversion(window)
        if rsi_sig in ("CALL", "PUT"):
            return rsi_sig

        boll_sig = predict_bollinger_mean_reversion(window)
        if boll_sig in ("CALL", "PUT"):
            return boll_sig

        return "NO_POSITION"

    elif regime in ("CHOPPY", "UNKNOWN"):
        return "NO_POSITION"

    return "NO_POSITION"


# ---------------------------------------------------------------------------
# Registry of all strategies
# ---------------------------------------------------------------------------

PREDICTION_STRATEGIES: Dict[str, PredictionFunction] = {
    # Regime + indicator combinations
    "trendUpRangeBreakout":     predict_trendUp_rangeBreakout,
    "MaTrend_001":              predict_maTrend_001,
    "MaTrend_0005":             predict_maTrend_0005,
    "trendUpMaTrend_001":       predict_trendUp_maTrend_001,
    "trendUpMaTrend_0005":      predict_trendUp_maTrend_0005,
    "trendDownRangeBreakout":   predict_trendDown_rangeBreakout,
    "trendDownMaTrend_001":     predict_trendDown_maTrend_001,
    "trendDownMaTrend_0005":    predict_trendDown_maTrend_0005,
    "RsiMeanReversion_7030":    predict_rsiMeanReversion_7030,
    "RsiMeanReversion_6535":    predict_rsiMeanReversion_6535,
    "rangeRsiMeanReversion_7030":   predict_range_rsiMeanReversion_7030,
    "rangeRsiMeanReversion_6535":   predict_range_rsiMeanReversion_6535,
    "BollingerMeanReversion":       predict_bollingerMeanReversion,
    "rangeBollingerMeanReversion":  predict_range_bollingerReversion,
    "choppy":                   predict_choppy,
    "unknown":                  predict_unknown,
}
