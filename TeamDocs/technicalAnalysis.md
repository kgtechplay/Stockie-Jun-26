# Technical Analysis

This document explains how technical-analysis strategies are organized, how aggregators combine their output, and how to add a new strategy.

Code location: `src/technical_analysis`  
Aggregators: `src/technical_analysis/aggregator`  
Prediction service: `src/services/prediction_service.py`

## Folder Layout

```text
src/technical_analysis/
  underlying_prediction_*.py          underlying direction strategies
  underlying_prediction_common.py     shared helper functions
  underlying_registry.py              stable import path for strategy discovery
  underlying_strategy_registry.py     implementation of strategy discovery

  option_selection_*.py               option contract selection strategies
  option_selection_common.py          shared option-selection helpers
  option_registry.py                  stable import path for option selector discovery
  option_strategy_registry.py         implementation of option selector discovery

  aggregator/
    underlying_aggregator.py          runs/combines underlying predictions
    option_aggregator.py              applies option selection to prediction rows
```

## Strategy Types

| Type | File Pattern | Required Function | Required Constant | Output |
|---|---|---|---|---|
| Underlying prediction | `underlying_prediction_*.py` | `predict(window)` | `STRATEGY_NAME` | `CALL`, `PUT`, or `NO_POSITION` |
| Option selection | `option_selection_*.py` | `select(chain_df, prediction, trade_date)` | `STRATEGY_NAME` | Selected option dict or `None` |

## Underlying Prediction Strategies

Underlying strategies decide the direction for a stock or index.

Current strategy files:

| File | Purpose |
|---|---|
| `underlying_prediction_MaTrend_001.py` | Moving-average trend signal with a 0.1 percent band. |
| `underlying_prediction_MaTrend_0005.py` | Moving-average trend signal with a tighter 0.05 percent band. |
| `underlying_prediction_trendUpMaTrend_001.py` | Moving-average trend signal gated to `TREND_UP` regime. |
| `underlying_prediction_trendUpMaTrend_0005.py` | Tighter moving-average trend signal gated to `TREND_UP` regime. |
| `underlying_prediction_trendDownMaTrend_001.py` | Moving-average trend signal gated to `TREND_DOWN` regime. |
| `underlying_prediction_trendDownMaTrend_0005.py` | Tighter moving-average trend signal gated to `TREND_DOWN` regime. |
| `underlying_prediction_RsiMeanReversion_7030.py` | RSI mean reversion using 70/30 thresholds. |
| `underlying_prediction_RsiMeanReversion_6535.py` | RSI mean reversion using 65/35 thresholds. |
| `underlying_prediction_rangeRsiMeanReversion_7030.py` | RSI mean reversion gated to `RANGE` regime. |
| `underlying_prediction_rangeRsiMeanReversion_6535.py` | RSI mean reversion gated to `RANGE` regime with 65/35 thresholds. |
| `underlying_prediction_BollingerMeanReversion.py` | Bollinger-band mean reversion. |
| `underlying_prediction_rangeBollingerMeanReversion.py` | Bollinger-band mean reversion gated to `RANGE` regime. |
| `underlying_prediction_trendUpRangeBreakout.py` | Range breakout gated to `TREND_UP` regime. |
| `underlying_prediction_trendDownRangeBreakout.py` | Range breakout gated to `TREND_DOWN` regime. |
| `underlying_prediction_choppy.py` | Choppy-regime strategy variant. |
| `underlying_prediction_unknown.py` | Fallback strategy for unknown regimes. |

Shared helpers live in `underlying_prediction_common.py`.

Important helpers:

| Helper | Purpose |
|---|---|
| `get_closes(window)` | Extracts close prices from a Series/DataFrame. |
| `compute_rsi(closes)` | Computes RSI. |
| `signal_rsi_mean_reversion(...)` | Generic RSI mean-reversion signal. |
| `signal_ma_trend(...)` | Generic moving-average trend signal. |
| `signal_bollinger_mean_reversion(...)` | Generic Bollinger-band mean-reversion signal. |
| `signal_range_breakout(...)` | Generic range breakout signal. |
| `detect_regime(window)` | Classifies `TREND_UP`, `TREND_DOWN`, `CHOPPY`, `RANGE`, or `UNKNOWN`. |

## Underlying Aggregator

Location: `src/technical_analysis/aggregator/underlying_aggregator.py`

The underlying aggregator is not a strategy. It combines strategy outputs.

It does three main jobs:

1. Loads selected strategy functions from `underlying_registry`.
2. Runs each strategy against the same price window.
3. Produces a majority-vote decision.

Voting rules:

- Count only `CALL` and `PUT`.
- If `CALL` count is greater than `PUT`, aggregate decision is `CALL`.
- If `PUT` count is greater than `CALL`, aggregate decision is `PUT`.
- If there is a tie, or every strategy returns `NO_POSITION`, aggregate decision is `NO_POSITION`.

In the reference-date prediction output, the aggregator adds:

```text
aggregate_decision
```

Example:

```text
instrument,MaTrend_001,RsiMeanReversion_6535,aggregate_decision
INFY,CALL,PUT,NO_POSITION
TCS,CALL,CALL,CALL
```

## Option Selection Strategies

Option selectors choose a concrete option contract after an underlying prediction has produced `CALL` or `PUT`.

Current selector files:

| File | Purpose |
|---|---|
| `option_selection_nearestExpiryATM.py` | Select nearest-expiry ATM option with liquidity tie-breakers. |
| `option_selection_nearestExpiryHighOI.py` | Select nearest-expiry option with high open interest. |
| `option_selection_highestDeltaPriceRatio.py` | Select option using delta-to-price style scoring. |

Shared helpers live in `option_selection_common.py`.

## Option Aggregator

Location: `src/technical_analysis/aggregator/option_aggregator.py`

The option aggregator applies option-selection strategies to prediction rows.

It:

- receives prediction rows
- receives option snapshot/chain data
- skips rows where prediction is not `CALL` or `PUT`
- applies the selected option strategy
- fills standard selected-option columns

Standard selected-option columns:

| Column | Meaning |
|---|---|
| `option_trade_date` | Date of selected option trade. |
| `option_instrument_token` | Selected option token. |
| `option_tradingsymbol` | Selected option symbol. |
| `option_strike` | Selected strike. |
| `option_expiry` | Selected expiry. |
| `option_type` | `CALL` or `PUT`. |
| `selection_option_price_1515` | Option price used for selection. |

## Adding An Underlying Prediction Strategy

Create a new file:

```text
src/technical_analysis/underlying_prediction_myStrategy.py
```

Template:

```python
from __future__ import annotations

from .underlying_prediction_common import PredictionInput, signal_ma_trend

STRATEGY_NAME = "myStrategy"


def predict(window: PredictionInput) -> str:
    return signal_ma_trend(window, short_window=5, long_window=20, band=0.001)
```

Rules:

- File name must start with `underlying_prediction_`.
- Define `STRATEGY_NAME`.
- Define `predict(window)`.
- Return only `CALL`, `PUT`, or `NO_POSITION`.
- Use only data available inside the supplied `window`.
- Do not look into future rows.
- Return `NO_POSITION` when required columns or enough history are missing.
- Do not mutate the input DataFrame unless you copy it first.

Discovery is automatic. The registry scans:

```text
src/technical_analysis/underlying_prediction_*.py
```

## Adding An Option Selection Strategy

Create a new file:

```text
src/technical_analysis/option_selection_mySelector.py
```

Template:

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

Rules:

- File name must start with `option_selection_`.
- Define `STRATEGY_NAME`.
- Define `select(chain_df, prediction, trade_date)`.
- Return `None` when no valid option exists.
- Return the standard dict from `build_selection_output()` when an option is selected.

Discovery is automatic. The registry scans:

```text
src/technical_analysis/option_selection_*.py
```

## Prediction Output

`PredictionService.run_reference_date_predictions_for_symbols()` uses the underlying strategies and aggregator to write one CSV file per reference date:

```text
output/<reference_date>.csv
```

Example:

```text
output/2026-05-08.csv
```

File shape:

```text
reference_date,instrument,status,error,<strategy_1>,<strategy_2>,...,aggregate_decision
```

Each row is a stock or index. Each strategy column stores that strategy's direction for that instrument. The final column is the majority-vote aggregate decision.

## Backtesting

There are two backtest styles:

| File | Purpose |
|---|---|
| `src/backtest/watched_underlying_backtest.py` | Backtests the new watched prediction matrix for one reference date. |
| `src/backtest/watched_e2e_backtest.py` | Wrapper for watched end-to-end backtesting. Currently runs the underlying leg and marks the option leg skipped until selected option contracts are included in the watched output. |
| `src/backtest/historical_underlying_backtest.py` | Historical batch backtest for older per-underlying/per-strategy prediction CSV files. |
| `src/backtest/historical_e2e_backtest.py` | Historical batch end-to-end backtest for older prediction plus option-selection CSV files. |

Historical prediction generation lives in:

```text
src/services/historical_prediction.py
```

It writes one file per watched underlying and strategy:

```text
output/historical/<underlying>_<strategy>.csv
```

Each historical file has date rows and a `prediction` column, which lets `historical_underlying_backtest.py` add result columns back to the same file.

Watched backtest input:

```text
output/<reference_date>.csv
```

The watched backtest evaluates every `instrument x strategy` combination, including `aggregate_decision`, against the next trading day. It adds result columns back to the same `output/<reference_date>.csv` file.

## Quality Checklist

Before keeping a strategy:

- It handles short windows gracefully.
- It returns `NO_POSITION` instead of throwing for normal missing-data cases.
- It avoids look-ahead bias.
- It uses clear thresholds.
- It has a clear `STRATEGY_NAME`.
- It can be tested with a small DataFrame.
- It works for stocks and indices when the required columns are present.
