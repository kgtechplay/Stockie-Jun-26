# Technical Analysis

## Gist

Technical analysis is separate from the news-analysis signal journal.

Use it when you want to:

- add or test an underlying direction strategy
- run one-date predictions for one watched stock/index
- generate historical prediction CSVs for one underlying
- backtest strategy columns and `aggregate_decision`
- select option contracts after an underlying `CALL` or `PUT`

Main outputs:

```text
output/<underlying>_prediction_<reference-date>.csv
output/historical/<underlying>_prediction.csv
```

Backtest details live in `TeamDocs/backtestStrategy.md`.

Code location: `src/technical_analysis`

The Flask Technical Analysis tab loads its stock/index dropdown from active rows in `dbo.WatchedInstrument`, so users only run predictions against instruments where the project is expected to have historical data.

## Folder Layout

```text
src/technical_analysis/
  prediction/
    features.py
    regime.py
    strategies.py
    underlying_prediction_common.py
    underlying_prediction_*.py    # optional custom/plugin strategies only
    underlying_registry.py

  selection/
    option_selection_common.py
    option_selection_*.py
    option_registry.py

  aggregator/
    underlying_aggregator.py
    option_aggregator.py
```

Removed/obsolete files:

```text
src/technical_analysis/underlying_prediction_common.py
src/technical_analysis/option_selection_common.py
src/technical_analysis/prediction/underlying_strategy_registry.py
src/technical_analysis/selection/option_strategy_registry.py
```

If your IDE still shows these files in open tabs, those tabs are stale.

## Data Prerequisites

Technical prediction reads daily OHLCV data from `dbo.UnderlyingSnapshot`.

The active universe comes from `dbo.WatchedInstrument`.

Relevant ingestion/backfill entrypoints:

```text
scripts/daily_fetch_stocks_universe.py      # builds stock universe CSVs
scripts/populate_watched_instruments.py     # seeds base index watched rows
scripts/backfill_nifty200_watched.py        # upserts Nifty 200 watched rows and batch backfills
scripts/daily_optionInstrument_refresh.py   # refreshes OptionInstrument rows
scripts/daily_market_refresh.py             # daily backfill entrypoint for active watched instruments
src/services/backfill_service.py            # INDEX/STOCK backfill orchestrator
```

`daily_market_refresh.py` calls `BackfillService`, which classifies active `WatchedInstrument` rows into `INDEX` and `STOCK`, then runs the matching underlying and option backfill jobs.

## Option Snapshot Date Boundary

Live daily option-chain snapshots should be treated as starting from:

```text
2026-05-15
```

From this date onward, `scripts/daily_NIFTYoption_snapshot.py` is the daily/live quote capture path for NIFTY option instruments. It captures all active, non-expired NIFTY option instruments into `dbo.OptionSnapshot` using `data_source = KITE_QUOTE_LIVE`, and then calculates `dbo.OptionSnapshotCalc`.

Dates earlier than `2026-05-15` should be populated through historical backfill, not live snapshot capture:

```text
scripts/backfill/backfill_NIFTYoptions_from_historical.py
```

Historical backfill writes proxy rows using Kite historical 5-minute candles and `data_source = KITE_HISTORICAL_5M_CLOSE_PROXY`. It only inserts rows when Kite has the exact historical candle needed for the configured snapshot label.

Snapshot labels:

```text
OPEN_0920  -> historical candle at 09:15, stored as snapshot_time 09:20
CLOSE_1515 -> historical candle at 15:10, stored as snapshot_time 15:15
```

This boundary avoids mixing historical proxy data and live quote data for the same date range.

## Underlying Features

Location: `src/technical_analysis/prediction/features.py`

`compute_underlying_features(window)` appends the feature columns used by prediction CSVs:

```text
ma10, ma20, ma50, ma90,
rsi14, atr14,
bb_upper, bb_middle, bb_lower, bb_width,
ret_5d, ret_20d, ret_60d,
volatility_20d,
volume_ratio,
trend_efficiency_60d,
relative_strength_vs_sector,
ma20_slope, ma50_slope, ma20_50_crossovers_20d,
recent_high_20d, recent_low_20d, range_position_20d
```

Relative strength is optional; it is `None` unless a sector/index comparison window is supplied.

## Regime Detection

Location: `src/technical_analysis/prediction/regime.py`

`detect_regime(window)` returns:

```text
TREND_UP, TREND_DOWN, RANGE, CHOPPY, UNKNOWN
```

The current rule set uses MA alignment/slopes, 60-day return, trend efficiency, volatility, range position, and MA crossover count.

## Underlying Strategies

Location: `src/technical_analysis/prediction/strategies.py`

Built-in strategies are registered in `BUILTIN_UNDERLYING_STRATEGIES`. Each entry is an `UnderlyingStrategyDefinition` with a stable strategy name and a `predict(window)` function.

Built-in strategy names:

```text
BollingerMeanReversion
MaTrend_001
RsiMeanReversion_6535
rangeBollingerMeanReversion
rangeRsiMeanReversion_6535
trendDownRangeBreakout
trendUpRangeBreakout
choppy
unknown
```

Strategy outputs are always:

```text
CALL, PUT, NO_POSITION
```

`prediction/underlying_prediction_common.py` exists as a convenience import surface for prediction helpers and built-in strategy functions.

## Underlying Registry

Location: `src/technical_analysis/prediction/underlying_registry.py`

This is the single public registry module for underlying prediction.

It:

- loads all built-ins from `strategies.py`
- optionally discovers custom files named `underlying_prediction_*.py`
- exposes `load_underlying_prediction_strategies()`
- exposes `PREDICTION_STRATEGIES`
- re-exports `DEFAULT_LOOKBACK_DAYS` and `detect_regime`

`underlying_strategy_registry.py` is no longer maintained.

## Aggregation

Location: `src/technical_analysis/aggregator/underlying_aggregator.py`

The aggregator:

- runs selected strategies
- normalizes invalid outputs to `NO_POSITION`
- adds feature columns from `compute_underlying_features()`
- uses `detected_regime` to choose which strategy group votes
- writes `aggregate_decision`

Voting rules:

- count `CALL` and `PUT`
- majority `CALL` returns `CALL`
- majority `PUT` returns `PUT`
- tie or no actionable votes returns `NO_POSITION`

Regime strategy groups:

```text
TREND_UP    -> trendUpRangeBreakout
TREND_DOWN  -> trendDownRangeBreakout
RANGE       -> rangeBollingerMeanReversion, rangeRsiMeanReversion_6535
CHOPPY      -> choppy
UNKNOWN     -> unknown
```

## Prediction CSV Schema

Consolidated prediction outputs are ordered as:

```text
date, underlying, today_volume,
<feature columns>,
detected_regime,
aggregate_decision,
<strategy columns>
```

`data_as_of_date` is intentionally not written.

Point-in-time prediction output:

```text
output/<underlying>_prediction_<reference-date>.csv
```

Historical prediction output:

```text
output/historical/<underlying>_prediction.csv
```

## Historical Prediction Service

Location: `src/services/historical_prediction.py`

Typical command:

```powershell
python src/services/historical_prediction.py --underlying RELIANCE
```

By default this generates the last 60 calendar days of requested output. Internally, `PredictionService` uses the TA default lookback of 90 rows so MA90, ret60, and trend efficiency can be computed.

Pass `--start`, `--end`, or repeated `--strategy` flags to narrow the run.

## Backtesting

Backtesting details are documented in:

```text
TeamDocs/backtestStrategy.md
```

Historical backtests read the prediction CSV, append market outcome columns, append per-strategy result columns, and write back to the same CSV.

Backtest output order:

```text
underlying, date, today_volume,
next_date, today_close, next_open, next_close, next_volume,
max_high_price, min_low_price, actual_move, max_delta_pct,
<feature columns>,
detected_regime, aggregate_decision, aggregate_decision_result,
<strategy>, <strategy_result>, ...
```

Backtest thresholds:

```text
historical underlying backtest:  profit target = 1%,  stop loss = 0.5%
news signal backtest:            profit target = 3%,  stop loss = 2%
```

Historical backtesting returns a summary dictionary with profit hits, stop hits, hit rates, and recall for `aggregate_decision` plus every individual strategy column.

## Option Selection

Option selectors choose a specific option contract after an underlying direction exists.

Folder:

```text
src/technical_analysis/selection/
  option_selection_common.py
  option_selection_highestDeltaPriceRatio.py
  option_selection_nearestExpiryATM.py
  option_selection_nearestExpiryHighOI.py
  option_registry.py
```

`option_registry.py` is the single public registry module. It discovers files named `option_selection_*.py`.

Built-in option selectors:

```text
highestDeltaPriceRatio
nearestExpiryATM
nearestExpiryHighOI
```

Selector contract:

```text
file name: option_selection_<name>.py
constant:  STRATEGY_NAME
function:  select(chain_df, prediction, trade_date)
returns:   selected option dict or None
```

Rules:

- return `None` when no valid contract exists
- use shared helpers from `selection/option_selection_common.py`
- output standard selected-option fields

## Adding A New Underlying Strategy

Preferred path: add built-in strategies to `prediction/strategies.py` by adding a new `UnderlyingStrategyDefinition`.

For custom/plugin-style strategies, create:

```text
src/technical_analysis/prediction/underlying_prediction_myStrategy.py
```

Example:

```python
from __future__ import annotations

from .underlying_prediction_common import PredictionInput, signal_ma_trend

STRATEGY_NAME = "myStrategy"


def predict(window: PredictionInput) -> str:
    return signal_ma_trend(window, short_window=10, long_window=20, band=0.001)
```

The registry discovers files automatically when they match `underlying_prediction_*.py`.

## Adding A New Option Selector

Create:

```text
src/technical_analysis/selection/option_selection_mySelector.py
```

Example:

```python
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from .option_selection_common import _common_filter, build_selection_output

STRATEGY_NAME = "mySelector"


def select(
    chain_df: pd.DataFrame,
    prediction: str,
    trade_date: pd.Timestamp,
) -> Optional[Dict]:
    df = _common_filter(chain_df, prediction, trade_date)
    if df.empty:
        return None

    row = df.sort_values("open_interest", ascending=False).iloc[0]
    return build_selection_output(row, prediction, row["_trade_date_norm"])
```

The registry discovers files automatically when they match `option_selection_*.py`.
