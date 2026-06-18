# NIFTY Underlying Features

These features are stored in Supabase table `SignalFeatureDaily` for NIFTY and are generated from `UnderlyingSnapshot` by `scripts/Common/calculate_underlying_features.py`.

| Column | Definition |
|---|---|
| `signal_date` | Trading date for the feature row. |
| `symbol` | Underlying symbol, for example `NIFTY`. |
| `feature_version` | Feature version tag, currently `v1`. |
| `close_1515` | Daily close price stored from `UnderlyingSnapshot.close_price`. |
| `open_915` | Daily open price stored from `UnderlyingSnapshot.open_price`. |
| `high_day` | Daily high price. |
| `low_day` | Daily low price. |
| `volume_day` | Daily volume. |
| `ma10` | Simple moving average of close over the latest 10 rows. |
| `ma20` | Simple moving average of close over the latest 20 rows. |
| `ma50` | Simple moving average of close over the latest 50 rows. |
| `ma90` | Simple moving average of close over the latest 90 rows. |
| `rsi14` | 14-period RSI using exponentially weighted average gains/losses. |
| `atr14` | 14-period ATR using true range and exponentially weighted smoothing. |
| `bb_upper` | 20-period Bollinger upper band: `bb_middle + 2 * std(close)`. |
| `bb_middle` | 20-period Bollinger middle band: 20-period moving average. |
| `bb_lower` | 20-period Bollinger lower band: `bb_middle - 2 * std(close)`. |
| `bb_width` | Bollinger width: `(bb_upper - bb_lower) / bb_middle`. |
| `ret_5d` | Return over 5 rows: `current_close / close_5_rows_ago - 1`. |
| `ret_10d` | Return over 10 rows. |
| `ret_20d` | Return over 20 rows. |
| `ret_60d` | Return over 60 rows. |
| `volatility_10d` | Standard deviation of daily close returns over the latest 10 returns. |
| `volatility_20d` | Standard deviation of daily close returns over the latest 20 returns. |
| `volume_10d` | Average daily volume over the latest 10 rows, including the current row. |
| `volume_20d` | Average daily volume over the latest 20 rows, including the current row. |
| `trend_efficiency_5d` | Absolute net move over 5 rows divided by total absolute path movement over those rows. |
| `trend_efficiency_10d` | Same trend-efficiency calculation over 10 rows. |
| `trend_efficiency_20d` | Same trend-efficiency calculation over 20 rows. |
| `trend_efficiency_60d` | Same trend-efficiency calculation over 60 rows. |
| `relative_strength_vs_sector` | 20-row return spread versus a sector window when one is provided; currently usually null for NIFTY. |
| `ma5d_slope` | 5-period MA change versus 5 rows ago: `current_ma5 / ma5_5_rows_ago - 1`. |
| `ma10d_slope` | 10-period MA change versus 5 rows ago. |
| `ma20_slope` | 20-period MA change versus 5 rows ago. |
| `ma50_slope` | 50-period MA change versus 5 rows ago. |
| `recent_high_5d` | Highest high from the prior 5 rows, excluding current row. |
| `recent_low_5d` | Lowest low from the prior 5 rows, excluding current row. |
| `recent_high_10d` | Highest high from the prior 10 rows, excluding current row. |
| `recent_low_10d` | Lowest low from the prior 10 rows, excluding current row. |
| `recent_high_20d` | Highest high from the prior 20 rows, excluding current row. |
| `recent_low_20d` | Lowest low from the prior 20 rows, excluding current row. |
| `range_position_5d` | Position of current close inside prior 5-row high/low range: `(close - recent_low_5d) / (recent_high_5d - recent_low_5d)`. |
| `range_position_10d` | Same range-position calculation using prior 10 rows. |
| `range_position_20d` | Same range-position calculation using prior 20 rows. |
| `regime` | Production regime label from `detect_regime`: `TREND_UP`, `TREND_DOWN`, `RANGE`, `CHOPPY`, or `UNKNOWN`. |
| `created_at` | Row creation timestamp. |
| `updated_at` | Last update timestamp. |

Notes:

- Rolling windows are based on available DB rows, not calendar days.
- Return values are decimal returns, so `0.02` means `2%`.
- `volume_10d` requires 10 volume rows; `volume_20d` requires 20 volume rows.
- `range_position_*` can go below `0` or above `1` if current close breaks below the recent low or above the recent high.
