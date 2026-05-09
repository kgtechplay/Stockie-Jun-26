from __future__ import annotations

import math
from typing import Union

import pandas as pd

PredictionInput = Union[pd.Series, pd.DataFrame]
DEFAULT_LOOKBACK_DAYS = 20
DEFAULT_TREND_Z_THRESH = 1.0


def get_closes(window: PredictionInput) -> pd.Series:
    if isinstance(window, pd.Series):
        return window.astype(float)
    if not isinstance(window, pd.DataFrame):
        raise TypeError(f"window must be pd.Series or pd.DataFrame, got {type(window)}")
    if "close_price" not in window.columns:
        raise ValueError("DataFrame window must contain a 'close_price' column")
    return window["close_price"].astype(float)


def get_column(window: PredictionInput, col: str) -> pd.Series | None:
    if isinstance(window, pd.DataFrame) and col in window.columns:
        return window[col].astype(float)
    return None


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def signal_rsi_mean_reversion(
    window: PredictionInput,
    rsi_period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> str:
    closes = get_closes(window)
    if len(closes) < rsi_period + 2:
        return "NO_POSITION"
    rsi = compute_rsi(closes, period=rsi_period)
    current_rsi = float(rsi.iloc[-1])
    if current_rsi >= overbought:
        return "PUT"
    if current_rsi <= oversold:
        return "CALL"
    return "NO_POSITION"


def signal_ma_trend(
    window: PredictionInput,
    short_window: int = 5,
    long_window: int = 20,
    band: float = 0.001,
) -> str:
    closes = get_closes(window)
    if len(closes) < long_window:
        return "NO_POSITION"
    short_ma = float(closes.iloc[-short_window:].mean())
    long_ma = float(closes.iloc[-long_window:].mean())
    if long_ma == 0:
        return "NO_POSITION"
    spread_pct = (short_ma - long_ma) / long_ma
    if spread_pct > band:
        return "CALL"
    if spread_pct < -band:
        return "PUT"
    return "NO_POSITION"


def signal_bollinger_mean_reversion(
    window: PredictionInput,
    bb_period: int = 20,
    num_std: float = 2.0,
) -> str:
    closes = get_closes(window)
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
    if last < lower:
        return "CALL"
    return "NO_POSITION"


def signal_range_breakout(window: PredictionInput, lookback: int = 20) -> str:
    closes = get_closes(window)
    highs = get_column(window, "high_price")
    lows = get_column(window, "low_price")
    if highs is None or lows is None:
        return "NO_POSITION"
    if len(closes) < lookback + 1:
        return "NO_POSITION"
    last_close = float(closes.iloc[-1])
    recent_high = float(highs.iloc[-(lookback + 1):-1].max())
    recent_low = float(lows.iloc[-(lookback + 1):-1].min())
    if last_close > recent_high:
        return "CALL"
    if last_close < recent_low:
        return "PUT"
    return "NO_POSITION"


def detect_regime(
    window: PredictionInput,
    min_len: int = 15,
    trend_z_thresh: float = DEFAULT_TREND_Z_THRESH,
    chop_signchange_thresh: float = 0.6,
) -> str:
    closes = get_closes(window)
    n = len(closes)
    if n < min_len:
        return "UNKNOWN"
    rets = closes.pct_change().dropna()
    if len(rets) < 5:
        return "UNKNOWN"
    cum_ret = closes.iloc[-1] / closes.iloc[0] - 1.0
    vol = rets.std()
    if vol == 0 or pd.isna(vol):
        return "RANGE"
    trend_z = abs(cum_ret) / (vol * math.sqrt(len(rets)))
    signs = (rets > 0).astype(int) * 2 - 1
    sign_changes = (signs.diff().abs() > 0).sum()
    sign_change_frac = sign_changes / (len(signs) - 1)
    if trend_z >= trend_z_thresh and sign_change_frac <= 0.5:
        return "TREND_UP" if cum_ret > 0 else "TREND_DOWN"
    if sign_change_frac >= chop_signchange_thresh:
        return "CHOPPY"
    return "RANGE"
