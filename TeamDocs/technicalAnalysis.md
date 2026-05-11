# Technical Analysis

## Gist

Technical analysis is separate from the news-analysis signal journal.

Use it when you want to:

- add or test an underlying direction strategy
- run one-date predictions for one watched stock/index
- generate 30-day historical predictions for one underlying
- backtest strategy columns and `aggregate_decision`

Main outputs:

```text
output/<underlying>_prediction_<reference-date>.csv
output/historical/<underlying>_prediction.csv
```

Backtest details live in `TeamDocs/backtestStrategy.md`.

Code location: `src/technical_analysis`

The Flask Technical Analysis tab loads its stock/index dropdown from active rows in `WatchedInstrument`, so users only run predictions against instruments where the project is expected to have historical data.

## Folder Layout

```text
src/technical_analysis/
  underlying_prediction_common.py
  option_selection_common.py

  prediction/
    underlying_prediction_*.py
    underlying_registry.py
    underlying_strategy_registry.py

  selection/
    option_selection_*.py
    option_registry.py
    option_strategy_registry.py

  aggregator/
    underlying_aggregator.py
    option_aggregator.py
```

## Underlying Prediction Strategies

Underlying strategies predict direction for a stock or index.

Required pattern:

```text
file name: underlying_prediction_<name>.py
constant:  STRATEGY_NAME
function:  predict(window)
returns:   CALL, PUT, or NO_POSITION
```

Rules:

- use only data available in the supplied window
- avoid look-ahead bias
- return `NO_POSITION` when data is insufficient
- keep thresholds explicit and easy to test

## Underlying Aggregator

Location: `src/technical_analysis/aggregator/underlying_aggregator.py`

The aggregator runs selected strategies and combines their outputs.

Voting rules:

- count `CALL` and `PUT`
- majority `CALL` returns `CALL`
- majority `PUT` returns `PUT`
- tie or no actionable votes returns `NO_POSITION`

## Option Selection Strategies

Option selectors choose a specific option contract after an underlying direction exists.

Required pattern:

```text
file name: option_selection_<name>.py
constant:  STRATEGY_NAME
function:  select(chain_df, prediction, trade_date)
returns:   selected option dict or None
```

Rules:

- return `None` when no valid contract exists
- use shared helpers from `option_selection_common.py`
- output standard selected-option fields

## Adding A New Underlying Strategy

Create:

```text
src/technical_analysis/prediction/underlying_prediction_myStrategy.py
```

Example:

```python
from __future__ import annotations

from ..underlying_prediction_common import PredictionInput, signal_ma_trend

STRATEGY_NAME = "myStrategy"


def predict(window: PredictionInput) -> str:
    return signal_ma_trend(window, short_window=5, long_window=20, band=0.001)
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

from ..option_selection_common import _common_filter, build_selection_output

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

## Historical Prediction Service

Location: `src/services/historical_prediction.py`

This service runs underlying strategies for one requested underlying across historical dates.

Output:

```text
output/historical/<underlying>_prediction.csv
```

Each file has one row per date, one column per strategy, and an `aggregate_decision` column.
Historical backtests append per-strategy result columns to the same file.

Typical command:

```powershell
python src/services/historical_prediction.py --underlying RELIANCE
```

By default this generates the last 60 days. Pass `--start`, `--end`, or `--strategy` to narrow the run.

Point-in-time predictions use `PredictionService` directly and write:

```text
output/<underlying>_prediction_<reference-date>.csv
```

That file has the same consolidated schema but only one date row.

## Backtesting

Backtesting details are documented in:

```text
TeamDocs/backtestStrategy.md
```

Backtest thresholds differ between the two paths:

```text
historical underlying backtest:  profit target = 1%,  stop loss = 0.5%
news signal backtest:            profit target = 3%,  stop loss = 2%
```

Historical backtesting returns a summary dictionary with profit hits, stop hits, hit rates, and recall for `aggregate_decision` plus every individual strategy column.
