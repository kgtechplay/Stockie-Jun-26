# Backtest Strategy

## Gist

There are two underlying backtests:

| Backtest | Input | Use Case |
|---|---|---|
| News signal backtest | `output/trade_signal_journal.csv` | Did a news-derived signal work after it was created? |
| Historical underlying backtest | `output/historical/<underlying>_prediction.csv` or one single-date prediction CSV | Did technical-analysis strategies work across dates? |

Thresholds differ between the two paths:

```text
historical underlying backtest:  profit target = 1%,  stop loss = 0.5%
news signal backtest:            profit target = 3%,  stop loss = 2%
```

If you only need the headline: news backtest updates the signal journal; historical backtest updates consolidated prediction CSVs and returns hit-rate/recall summaries.

## Thresholds

### Historical Underlying Backtest

```python
PROFIT_TARGET_PCT = 0.01   # 1%
STOP_LOSS_PCT = 0.005      # 0.5%
```

| Prediction | Profit Hit | Stop Hit |
|---|---|---|
| `CALL` | `(max_high - next_open) / next_open >= 1%` | `(next_open - min_low) / next_open >= 0.5%` |
| `PUT` | `(next_open - min_low) / next_open >= 1%` | `(max_high - next_open) / next_open >= 0.5%` |

### News Signal Backtest

```python
PROFIT_TARGET_PCT = 0.03   # 3%
STOP_LOSS_PCT = 0.02       # 2%
```

| Prediction | Profit Hit | Stop Hit |
|---|---|---|
| `up` | price moves up at least 3% from entry | price moves down at least 2% from entry |
| `down` | price moves down at least 3% from entry | price moves up at least 2% from entry |

## 1. News Signal Backtest

Primary file:

```text
src/backtest/news_underlying_backtest.py
```

Primary input:

```text
output/trade_signal_journal.csv
```

This is the news-analysis backtest path:

```text
dailyNews
  -> impactList
  -> reviewList
  -> signal_normalizer
  -> output/trade_signal_journal.csv
  -> news_underlying_backtest
```

Each row in the journal is one finalized news-driven stock signal.

### Signal Identity

`signal_id` identifies one ticker-level signal from one news event.

It is generated from:

```text
news_event_id + published_at + ticker + expected_stock_direction
```

Visible example:

```text
SIG_EVT20260511001_20260511_INDIGO_DOWN_4E2802
```

`commodity` and `sector` remain normal journal columns, but they are not part of the signal identity.

### Required Columns

```text
signal_id
ticker
entry_allowed_from
expected_stock_direction
suggested_max_holding_days
signal_status
```

Only `signal_status = approved` rows are backtested. `monitor_only` rows remain in the journal and are skipped for trading metrics.

By default, rows that already have backtest results are skipped. Use `--force` to recalculate every approved row.

### Event Study

Fixed event windows:

```text
1, 3, 5, 10, 20, 60, 120 trading days
```

For each window:

- entry price is the first available open on or after `entry_allowed_from`
- exit price is the close at the fixed horizon
- return is `(exit_price - entry_price) / entry_price`
- `up` is correct when return is positive
- `down` is correct when return is negative

Columns added back to the journal:

```text
event_1d_return
event_1d_correct
event_3d_return
event_3d_correct
...
event_120d_return
event_120d_correct
```

### Triple Barrier

For an `up` signal:

```text
profit if high >= entry_price * 1.03
stop   if low  <= entry_price * 0.98
```

For a `down` signal:

```text
profit if low  <= entry_price * 0.97
stop   if high >= entry_price * 1.02
```

These use the news backtest thresholds (3% / 2%).

Vertical barrier:

```text
entry_allowed_from + suggested_max_holding_days
```

Possible labels:

```text
profit_hit
stop_hit
vertical_timeout_positive
vertical_timeout_negative
vertical_timeout_flat
insufficient_data
```

### MFE / MAE

MFE means maximum favorable excursion.  
MAE means maximum adverse excursion.

For `up` signals:

```text
MFE = max(high / entry_price - 1)
MAE = min(low / entry_price - 1)
```

For `down` signals:

```text
MFE = max(entry_price / low - 1)
MAE = min(entry_price / high - 1)
```

The backtest also records:

```text
days_to_peak
days_to_worst
```

### News Backtest Output

The same journal is updated with columns such as:

```text
backtest_status
market_data_error
entry_trade_date
entry_price
triple_barrier_label
triple_barrier_exit_date
triple_barrier_exit_price
profit_target_price
stop_loss_price
realized_return
directionally_correct
max_favorable_return
max_adverse_return
days_to_peak
days_to_worst
```

### News Summary Dict

`run_news_underlying_backtest()` returns:

```text
success
mode
signal_journal_file
output_file
rows
skipped_already_backtested
summary
```

The nested `summary` contains:

```text
approved_signals_backtested
profit_hits
stop_hits
profit_hit_rate_pct
stop_hit_rate_pct
by_triple_barrier_label
```

### Running Manually

```powershell
python src/backtest/news_underlying_backtest.py --signal-journal-file output/trade_signal_journal.csv
```

Force a full rerun:

```powershell
python src/backtest/news_underlying_backtest.py --signal-journal-file output/trade_signal_journal.csv --force
```

## 2. News E2E Backtest

File:

```text
src/backtest/news_e2e_backtest.py
```

Current status:

- wraps the news underlying backtest
- option leg is intentionally skipped for now
- will later connect approved underlying signals to option-selection strategies

## 3. Historical Underlying Backtest

File:

```text
src/backtest/historical_underlying_backtest.py
```

Historical prediction is intentionally one-underlying-at-a-time.

Historical prediction file:

```text
output/historical/<underlying>_prediction.csv
```

Generated by:

```powershell
python src/services/historical_prediction.py --underlying RELIANCE
```

By default, historical prediction generates the last 60 days.

Schema before backtest:

```text
date,underlying,<strategy_1>,<strategy_2>,...,aggregate_decision
```

The same backtest can also process a single-date prediction file from `PredictionService`:

```text
output/<underlying>_prediction_<reference-date>.csv
```

### Historical Backtest Logic

For each date row, the backtest enriches with next-day market data and classifies the actual move.

**Output column order:**

```text
underlying, date, next_date, today_close, next_open, next_close,
max_high_price, min_low_price, actual_move, max_delta_pct,
aggregate_decision, aggregate_decision_result, detected_regime,
<strategy_1>, <strategy_1_result>, ...
```

**actual_move classification** (two-sided threshold against `next_open`):

```text
CALL        — max_high > next_open × 1.01  AND  min_low > next_open × 0.995  (clean up day)
PUT         — min_low  < next_open × 0.99  AND  max_high < next_open × 1.005 (clean down day)
NO_POSITION — neither condition met (volatile/mixed or insufficient move)
```

**max_delta_pct** — the size of the actual move from open:

```text
CALL        → (max_high − next_open) / next_open × 100
PUT         → (next_open − min_low)  / next_open × 100
NO_POSITION → 0
```

**Result values** per strategy column and `aggregate_decision`:

```text
CORRECT       — prediction is CALL/PUT and matches actual_move
INCORRECT     — prediction is CALL/PUT but does not match actual_move
OK_NO_TRADE   — prediction is NO_POSITION and actual_move is NO_POSITION
MISSED_CALL   — prediction is NO_POSITION but actual_move is CALL
MISSED_PUT    — prediction is NO_POSITION but actual_move is PUT
N/A           — no market data available
```

### Historical Summary Dict

`run_historical_underlying_backtest()` returns:

```text
success
underlying
prediction_file
rows
strategies
summary
```

The top-level `summary` uses `aggregate_decision` as the primary prediction column when it exists.

Headline summary fields:

```text
days_backtested
actionable_predictions
profit_hits
stop_hits
profit_hit_rate_pct
stop_hit_rate_pct
accuracy_pct
recall_pct
correctly_directed
by_result
primary_prediction_column
by_prediction_column
```

Definitions:

- `days_backtested`: rows with valid next-day market data (`next_open`, `max_high_price`, `min_low_price` all present).
- `actionable_predictions`: rows where the primary prediction is `CALL` or `PUT`.
- `profit_hits`: actionable predictions where the move from `next_open` reached the 1% profit threshold.
- `stop_hits`: actionable predictions where the adverse move from `next_open` reached the 0.5% stop threshold.
- `profit_hit_rate_pct`: `profit_hits / actionable_predictions × 100`.
- `stop_hit_rate_pct`: `stop_hits / actionable_predictions × 100`.
- `accuracy_pct`: actionable predictions where the intraday high/low confirmed movement in the predicted direction from `next_open` (any positive delta).
- `recall_pct`: actionable predictions where `actual_move` matched the prediction direction — i.e., CALL predicted and CALL classified, or PUT predicted and PUT classified.
- `correctly_directed`: raw count behind `recall_pct`.

`by_prediction_column` contains the same metrics for every strategy column and for `aggregate_decision`.

### Running Manually

```powershell
python src/backtest/historical_underlying_backtest.py --underlying RELIANCE
```

With a single-date prediction file:

```powershell
python src/backtest/historical_underlying_backtest.py --underlying RELIANCE --prediction-file output/RELIANCE_prediction_2026-05-08.csv
```

## 4. Historical E2E Backtest

File:

```text
src/backtest/historical_e2e_backtest.py
```

Purpose:

- combine historical underlying predictions with option selection
- evaluate option-leg outcomes
- support older option-selection research workflows

This path is separate from the news signal journal.

## Design Rules

- Do not use future data to create a signal.
- Persist the signal before backtesting it.
- Keep `monitor_only` separate from approved trade metrics.
- Use fixed windows and explicit barriers.
- Do not choose exits after looking at the chart.
