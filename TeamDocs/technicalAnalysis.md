# Technical Analysis

This document explains how technical analysis strategies are organized, added, applied, and tested for indices or stocks.

Current code location: `src/technical_analysis`
Prediction services: `src/services/prediction_service.py`, `src/prediction/aggregator`
Backtests: `src/backtest`

## Purpose

Technical analysis has two layers:

1. Index/underlying prediction strategies decide `CALL`, `PUT`, or `NO_POSITION`.
2. Option selection strategies choose a concrete option contract for a `CALL` or `PUT` signal.

The intended flow is:

```text
UnderlyingSnapshot / UnderlyingCandle5m
  -> index prediction strategy
  -> CALL / PUT / NO_POSITION
  -> option selection strategy
  -> selected option contract
  -> backtest or trade plan
```

## Strategy Types

| Type | File Pattern | Required Function | Required Constant | Output |
|---|---|---|---|---|
| Index prediction | `index_prediction_*.py` | `predict(window)` | `STRATEGY_NAME` | `CALL`, `PUT`, or `NO_POSITION` |
| Option selection | `option_selection_*.py` | `select(chain_df, prediction, trade_date)` | `STRATEGY_NAME` | Dict describing selected option, or `None` |

## Current Index Prediction Strategies

Index prediction strategies live in `src/technical_analysis`.

Examples:

| File | Intent |
|---|---|
| `index_prediction_MaTrend_001.py` | Moving-average trend signal with a 0.1 percent band. |
| `index_prediction_MaTrend_0005.py` | Moving-average trend signal with a tighter band. |
| `index_prediction_RsiMeanReversion_7030.py` | RSI mean reversion with 70/30 thresholds. |
| `index_prediction_BollingerMeanReversion.py` | Bollinger-band mean reversion. |
| `index_prediction_trendUpRangeBreakout.py` | Range breakout variant for trend-up regimes. |
| `index_prediction_choppy.py` | Strategy variant for choppy regimes. |
| `index_prediction_unknown.py` | Fallback/unknown-regime strategy. |

Shared helpers live in:

```text
src/technical_analysis/index_prediction_common.py
```

Important helpers:

| Helper | Purpose |
|---|---|
| `get_closes(window)` | Extracts close prices from a Series/DataFrame. |
| `compute_rsi(closes)` | Computes RSI. |
| `signal_rsi_mean_reversion(...)` | Generic RSI signal helper. |
| `signal_ma_trend(...)` | Generic moving-average trend helper. |
| `signal_bollinger_mean_reversion(...)` | Generic Bollinger signal helper. |
| `signal_range_breakout(...)` | Generic breakout helper. |
| `detect_regime(window)` | Classifies `TREND_UP`, `TREND_DOWN`, `CHOPPY`, `RANGE`, or `UNKNOWN`. |

## Adding an Index Prediction Strategy

Create a new file:

```text
src/technical_analysis/index_prediction_myStrategy.py
```

Template:

```python
from __future__ import annotations

from .index_prediction_common import PredictionInput, signal_ma_trend

STRATEGY_NAME = "myStrategy"


def predict(window: PredictionInput) -> str:
    return signal_ma_trend(window, short_window=5, long_window=20, band=0.001)
```

Rules:

- File name must start with `index_prediction_`.
- Define `STRATEGY_NAME`.
- Define `predict(window)`.
- Return only `CALL`, `PUT`, or `NO_POSITION`.
- `window` can be a `pd.Series` of closes or a `pd.DataFrame` containing at least `close_price`.
- If the strategy needs high/low/volume, require those columns and return `NO_POSITION` when missing.

## Index Strategy Discovery

The registry discovers strategy files by scanning:

```text
src/technical_analysis/index_prediction_*.py
```

It loads any module that has:

```python
STRATEGY_NAME = "..."
def predict(window): ...
```

The loaded mapping is:

```python
{
    "strategyName": predict_function
}
```

## Applying Index Strategies

The index aggregator runs one or more strategies and uses majority vote.

Conceptual usage:

```python
from src.prediction.aggregator.index_aggregator import run_index_prediction

output = run_index_prediction(
    instrument="NIFTY",
    window=price_window,
    as_of=timestamp,
    strategies=["MaTrend_001", "RsiMeanReversion_7030"],
)
```

Result:

| Field | Meaning |
|---|---|
| `final_decision` | Final `CALL`, `PUT`, or `NO_POSITION`. |
| `confidence` | Aggregated confidence score. |
| `regime` | Detected market regime. |
| `component_signals` | Per-strategy signal records. |
| `reasons` | Short trace of strategy decisions. |

## Current Option Selection Strategies

Option selection strategies live in `src/technical_analysis`.

Examples:

| File | Intent |
|---|---|
| `option_selection_nearestExpiryATM.py` | Select nearest-expiry ATM option with liquidity tie-breakers. |
| `option_selection_nearestExpiryHighOI.py` | Select nearest-expiry option with high open interest. |
| `option_selection_highestDeltaPriceRatio.py` | Select option using delta-to-price style scoring. |

Shared helpers live in:

```text
src/technical_analysis/option_selection_common.py
```

Important helpers:

| Helper | Purpose |
|---|---|
| `_common_filter(chain_df, prediction, trade_date)` | Filters by `CALL`/`PUT`, non-expired options, and positive price. |
| `build_selection_output(row, prediction, trade_date_norm)` | Builds a standard option selection dict. |

## Adding an Option Selection Strategy

Create a new file:

```text
src/technical_analysis/option_selection_mySelector.py
```

Template:

```python
from __future__ import annotations

from typing import Optional, Dict
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

Expected option-chain columns include:

| Column | Meaning |
|---|---|
| `trade_date` | Snapshot trade date. |
| `instrument_token` | Option Kite token. |
| `tradingsymbol` | Option symbol. |
| `strike` | Strike price. |
| `expiry` | Expiry date. |
| `option_side` | `CALL` or `PUT`. |
| `option_price` | Option price used for selection. |
| `underlying_price` | Underlying price at snapshot time. |
| `open_interest` | Open interest. |
| `option_volume` | Volume, if available. |

## Option Strategy Discovery

The option registry discovers files by scanning:

```text
src/technical_analysis/option_selection_*.py
```

It loads any module that has:

```python
STRATEGY_NAME = "..."
def select(chain_df, prediction, trade_date): ...
```

## Applying Option Selection

The option aggregator applies one selector to a prediction DataFrame and option-chain DataFrame.

Conceptual usage:

```python
from src.prediction.aggregator.option_aggregator import apply_all_option_selection_strategies

outputs = apply_all_option_selection_strategies(
    preds=predictions_df,
    options_df=option_snapshots_df,
    strategies=["nearestExpiryATM"],
)
```

Output:

```text
{
  "nearestExpiryATM": dataframe_with_selected_option_columns
}
```

Standard output columns:

| Column | Meaning |
|---|---|
| `option_trade_date` | Date of selected option trade. |
| `option_instrument_token` | Selected option token. |
| `option_tradingsymbol` | Selected option symbol. |
| `option_strike` | Selected strike. |
| `option_expiry` | Selected expiry. |
| `option_type` | `CALL` or `PUT`. |
| `selection_option_price_1515` | Option price used for selection. |

## Applying Strategies to an Index

For an index such as `NIFTY` or `BANKNIFTY`:

1. Ensure data exists in:
   - `UnderlyingSnapshot`
   - `UnderlyingCandle5m`
   - `OptionInstrument`
   - `OptionSnapshot`
   - `OptionSnapshotCalc`
2. Load the rolling price window from the data manager/readers.
3. Run one or more index prediction strategies.
4. Load option-chain snapshots for the same trade dates.
5. Run one or more option selection strategies.
6. Backtest the combined prediction and option selection output.

## Applying Strategies to a Stock

For a stock:

1. Ensure the stock exists in `WatchedInstrument` as `instrument_type = STOCK`.
2. Ensure the daily/backfill data setup has run:
   - underlying daily snapshot
   - underlying 5-minute candles
   - option instruments
   - option snapshots
3. Run the same prediction functions against the stock's `UnderlyingSnapshot`/`UnderlyingCandle5m` window.
4. Run option selection against that stock's option chain.

The same technical strategy function can work for either index or stock as long as the input DataFrame has the required columns.

## Testing and Backtesting

Backtest code lives in:

```text
src/backtest/index_backtest.py
src/backtest/e2e_backtest.py
```

Current backtest concept:

| File | Purpose |
|---|---|
| `index_backtest.py` | Tests index prediction output against next-day movement and intraday opportunity. |
| `e2e_backtest.py` | Tests full prediction plus option-selection combinations. |

Typical validation workflow:

1. Add the strategy file.
2. Confirm it appears in the relevant registry.
3. Run prediction generation for the target index/stock.
4. Run index backtest.
5. Run option selection.
6. Run end-to-end backtest.
7. Compare accuracy, missed opportunities, option selector accuracy, and PnL.

## Strategy Quality Checklist

Before keeping a strategy:

- It handles too-short windows gracefully.
- It returns `NO_POSITION` instead of throwing on missing data.
- It avoids look-ahead bias.
- It uses only data available at the decision time.
- It can be tested independently with a small DataFrame.
- It has a clear `STRATEGY_NAME`.
- It does not mutate shared input data unless it copies first.

## Registry Compatibility Note

Some callers import:

```python
src.technical_analysis.index_registry
src.technical_analysis.option_registry
```

The current strategy registry implementations live in:

```text
src/technical_analysis/index_strategy_registry.py
src/technical_analysis/option_strategy_registry.py
```

The intended stable import path for callers should remain the shorter `index_registry` and `option_registry`, with those modules re-exporting the strategy registry functions.
