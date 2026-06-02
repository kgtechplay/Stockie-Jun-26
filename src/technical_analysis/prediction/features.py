from __future__ import annotations

from typing import Any

import pandas as pd

PredictionInput = pd.Series | pd.DataFrame
FeatureOutput = dict[str, Any]
FEATURE_COLUMNS = [
    "ma10",
    "ma20",
    "ma50",
    "ma90",
    "rsi14",
    "atr14",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "volatility_20d",
    "volume_ratio",
    "trend_efficiency_60d",
    "relative_strength_vs_sector",
    "ma20_slope",
    "ma50_slope",
    "ma20_50_crossovers_20d",
    "recent_high_20d",
    "recent_low_20d",
    "range_position_20d",
]


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


def round_feature(value: Any, digits: int = 4) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def compute_true_range(window: pd.DataFrame) -> pd.Series:
    highs = get_column(window, "high_price")
    lows = get_column(window, "low_price")
    closes = get_closes(window)
    if highs is None or lows is None:
        return pd.Series(dtype=float)
    prev_close = closes.shift(1)
    ranges = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def compute_atr(window: pd.DataFrame, period: int = 14) -> pd.Series:
    true_range = compute_true_range(window)
    if true_range.empty:
        return true_range
    return true_range.ewm(alpha=1.0 / period, adjust=False).mean()


def compute_return(closes: pd.Series, days: int) -> float | None:
    if len(closes) < days + 1:
        return None
    start = float(closes.iloc[-(days + 1)])
    if start == 0:
        return None
    return float(closes.iloc[-1]) / start - 1.0


def compute_volume_ratio(window: PredictionInput, lookback: int = 20) -> float | None:
    volumes = get_column(window, "volume")
    if volumes is None or len(volumes) < lookback + 1:
        return None
    avg_volume = float(volumes.iloc[-(lookback + 1):-1].mean())
    if avg_volume == 0:
        return None
    return float(volumes.iloc[-1]) / avg_volume


def compute_trend_efficiency(closes: pd.Series, days: int = 60) -> float | None:
    if len(closes) < days + 1:
        return None
    close_slice = closes.iloc[-(days + 1):]
    net_move = abs(float(close_slice.iloc[-1]) - float(close_slice.iloc[0]))
    path_move = float(close_slice.diff().abs().sum())
    if path_move == 0:
        return None
    return net_move / path_move


def count_ma_crossovers(
    closes: pd.Series,
    short_window: int = 20,
    long_window: int = 50,
    lookback: int = 20,
) -> int | None:
    if len(closes) < long_window + lookback:
        return None
    short_ma = closes.rolling(short_window).mean()
    long_ma = closes.rolling(long_window).mean()
    spread_sign = (short_ma - long_ma).dropna().tail(lookback + 1).apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0)
    if len(spread_sign) < 2:
        return None
    return int((spread_sign.diff().abs() > 0).sum())


def compute_relative_strength_vs_sector(
    stock_window: PredictionInput,
    sector_window: PredictionInput | None,
    days: int = 20,
) -> float | None:
    if sector_window is None:
        return None
    stock_ret = compute_return(get_closes(stock_window), days)
    sector_ret = compute_return(get_closes(sector_window), days)
    if stock_ret is None or sector_ret is None:
        return None
    return stock_ret - sector_ret


def compute_underlying_features(
    window: PredictionInput,
    sector_window: PredictionInput | None = None,
) -> FeatureOutput:
    closes = get_closes(window)
    highs = get_column(window, "high_price")
    lows = get_column(window, "low_price")
    current_close = float(closes.iloc[-1]) if len(closes) else None

    features: FeatureOutput = {
        "ma10": round_feature(closes.tail(10).mean()) if len(closes) >= 10 else None,
        "ma20": round_feature(closes.tail(20).mean()) if len(closes) >= 20 else None,
        "ma50": round_feature(closes.tail(50).mean()) if len(closes) >= 50 else None,
        "ma90": round_feature(closes.tail(90).mean()) if len(closes) >= 90 else None,
        "rsi14": round_feature(compute_rsi(closes, 14).iloc[-1]) if len(closes) >= 16 else None,
        "ret_5d": round_feature(compute_return(closes, 5), 6),
        "ret_20d": round_feature(compute_return(closes, 20), 6),
        "ret_60d": round_feature(compute_return(closes, 60), 6),
        "volatility_20d": round_feature(closes.pct_change().dropna().tail(20).std(), 6)
        if len(closes) >= 21
        else None,
        "volume_ratio": round_feature(compute_volume_ratio(window), 6),
        "trend_efficiency_60d": round_feature(compute_trend_efficiency(closes, 60), 6),
        "relative_strength_vs_sector": round_feature(
            compute_relative_strength_vs_sector(window, sector_window, 20),
            6,
        ),
        "ma20_slope": round_feature(_ma_slope(closes, 20), 6),
        "ma50_slope": round_feature(_ma_slope(closes, 50), 6),
        "ma20_50_crossovers_20d": count_ma_crossovers(closes),
    }

    if isinstance(window, pd.DataFrame):
        atr14 = compute_atr(window, 14)
        features["atr14"] = round_feature(atr14.iloc[-1]) if len(atr14) >= 14 else None
    else:
        features["atr14"] = None

    if len(closes) >= 20:
        bb_middle = closes.rolling(20).mean().iloc[-1]
        bb_std = closes.rolling(20).std(ddof=0).iloc[-1]
        bb_upper = bb_middle + 2.0 * bb_std
        bb_lower = bb_middle - 2.0 * bb_std
        features["bb_upper"] = round_feature(bb_upper)
        features["bb_middle"] = round_feature(bb_middle)
        features["bb_lower"] = round_feature(bb_lower)
        features["bb_width"] = round_feature((bb_upper - bb_lower) / bb_middle, 6) if bb_middle else None
    else:
        features["bb_upper"] = None
        features["bb_middle"] = None
        features["bb_lower"] = None
        features["bb_width"] = None

    if highs is not None and lows is not None and len(closes) >= 2:
        prior_highs = highs.iloc[:-1].tail(20)
        prior_lows = lows.iloc[:-1].tail(20)
        features["recent_high_20d"] = round_feature(prior_highs.max())
        features["recent_low_20d"] = round_feature(prior_lows.min())
        if current_close is not None and features["recent_high_20d"] and features["recent_low_20d"]:
            range_width = float(features["recent_high_20d"]) - float(features["recent_low_20d"])
            features["range_position_20d"] = round_feature(
                (current_close - float(features["recent_low_20d"])) / range_width,
                6,
            ) if range_width else None
        else:
            features["range_position_20d"] = None
    else:
        features["recent_high_20d"] = None
        features["recent_low_20d"] = None
        features["range_position_20d"] = None

    return {column: features.get(column) for column in FEATURE_COLUMNS}


def _ma_slope(closes: pd.Series, window: int, periods: int = 5) -> float | None:
    if len(closes) < window + periods:
        return None
    ma = closes.rolling(window).mean()
    previous = float(ma.iloc[-(periods + 1)])
    if previous == 0:
        return None
    return float(ma.iloc[-1]) / previous - 1.0
