# Requirement: Modify reviewList Output, Add Signal Normalizer, Trade Scoring, and Backtesting Logic

> **Status: Implemented.** This document is a historical requirements spec. For current implementation details see `TeamDocs/`. For running the app see `run_local.md`.

## Gist

This requirement describes the news-analysis signal pipeline:

```text
news -> dailyNews -> impactList -> reviewList -> signal_normalizer -> signal journal -> backtest
```

Current implementation locations:

| Concern | Location |
|---|---|
| News analysis | `src/news_analysis/` |
| Orchestration | `src/services/orchestration_service.py` |
| Signal journal | `output/trade_signal_journal.csv` |
| News backtest | `src/backtest/news_underlying_backtest.py` |
| Backtest docs | `TeamDocs/backtestStrategy.md` |

Read this file for original requirements; read `TeamDocs/` for the current implementation summary.

## 1. Context

We are building a commodity-news-to-stock-impact pipeline.

Current agent flow:

```text
dailyNews
  â†’ extracts impacted commodities from a news article

impactList
  â†’ maps commodities to impacted sectors, example companies/stocks, sensitivity, timeline, reasoning

reviewList
  â†’ reviews impactList output
```

The system now needs to evolve so that `reviewList` produces a flat, backtest-ready prediction output called:

```json
"approved_trade_signals": []
```

Then deterministic orchestration logic should normalize these signals, calculate trade scores, calculate entry timestamps, persist signals into a signal journal, and pass the finalized signals into a backtesting module.

The key design principle is:

```text
Agents reason and classify.
Python orchestration normalizes, scores, timestamps, persists, and backtests.
```

---

## 2. Objective

Implement the following:

1. Modify `reviewList` agent definition and output schema so that it always returns `approved_trade_signals`.
2. Add deterministic orchestration logic to:
   - parse `reviewList.approved_trade_signals`
   - validate each signal
   - calculate `final_trade_score`
   - calculate `entry_allowed_from`
   - calculate `suggested_max_holding_days`
   - assign `signal_id`
   - persist finalized signals into a signal journal
3. Add backtesting logic using:
   - fixed event-study windows
   - triple-barrier labeling
   - MFE / MAE analysis
   - optional portfolio simulation
4. Ensure the system avoids look-ahead bias:
   - no future prices should influence signal generation
   - market data should only be used after the signal is finalized and stored

---

## 3. Target Pipeline

New pipeline flow:

```text
news article
  â†’ dailyNews
  â†’ impactList
  â†’ reviewList
  â†’ signal_normalizer
  â†’ TradeSignalJournal
  â†’ backtest engine
```

---

## 4. Part 1: Modify reviewList Agent Output

### 4.1 Files to Inspect or Modify

```text
news_analysis/reviewList/agent_definition.md
news_analysis/reviewList/output_schema.md
news_analysis/reviewList/config.yaml
```

If these files do not exist, create them.

---

## 5. Modify `news_analysis/reviewList/agent_definition.md`

Add a clear responsibility that `reviewList` must not only review sector impacts, but also produce flat prediction rows.

Add the following section:

```md
## Secondary Task: Generate Approved Trade Signals

After reviewing the sector impacts from `impactList`, generate a flat list called `approved_trade_signals`.

Each item in `approved_trade_signals` must represent one tradable or monitorable stock-level prediction.

The purpose of `approved_trade_signals` is to create a clean signal journal for downstream scoring and backtesting.

Each signal must map:

- one news event
- one commodity
- one commodity direction
- one sector
- one company / stock
- one expected stock direction
- one directness level
- one sensitivity level
- one timeline bucket
- one confidence profile
- one signal status

Only include signals where:

- review action is `keep` or `modify`
- expected stock direction is `up` or `down`
- reviewer confidence is above the configured minimum threshold
- stock/company mapping is reasonably clear
- timeline is not `uncertain`
- causal chain is not too speculative

Do not include signals where:

- direction is `mixed`
- direction is `uncertain`
- timeline is `uncertain`
- company mapping is weak
- causal chain is too long or speculative
- sector impact is theoretical but unlikely to affect stock price

Use `approved` when the signal is strong enough for trading simulation.

Use `monitor_only` when the thesis is directionally valid but not strong enough for trade simulation.

Do not output rejected signals in `approved_trade_signals`.

Important:
- `reviewList` may leave `signal_id`, `final_trade_score`, `entry_allowed_from`, and `suggested_max_holding_days` as null.
- These fields should be calculated deterministically by downstream orchestration logic.
- The agent must not use future market data or post-news price movement while generating signals.
```

Also add this to the output rules:

```md
The output must always include:

```json
"approved_trade_signals": []
```

If no valid signals are found, return an empty array.
```

---

## 6. Modify `news_analysis/reviewList/output_schema.md`

Update the top-level schema so it contains the following structure:

```json
{
  "event_id": "string",
  "review_summary": "string",
  "overall_quality_score": 0.0,

  "reviewed_sector_impacts": [],

  "missing_sector_impacts": [],

  "removed_or_flagged_items": [],

  "approved_trade_signals": [],

  "final_recommendation": "approve | approve_with_changes | reject_and_rerun"
}
```

Add this full schema for `approved_trade_signals`:

```json
"approved_trade_signals": [
  {
    "signal_id": "string | null",

    "news_event_id": "string",

    "published_at": "ISO-8601 timestamp",

    "processed_at": "ISO-8601 timestamp",

    "commodity": "string",

    "commodity_direction": "up | down | mixed | uncertain",

    "commodity_confidence": 0.0,

    "sector": "string",

    "sub_sector": "string | null",

    "stock": {
      "company_name": "string",
      "ticker": "string | null",
      "exchange": "string | null"
    },

    "expected_stock_direction": "up | down",

    "directness": "direct | indirect | second_order",

    "sensitivity": "very_high | high | medium | low",

    "timeline_bucket": "same_day | 1_3_days | 1_4_weeks | 1_6_months | 6_months_plus",

    "sector_confidence": 0.0,

    "company_confidence": 0.0,

    "reviewer_confidence": 0.0,

    "final_trade_score": "number | null",

    "entry_allowed_from": "ISO-8601 timestamp | null",

    "suggested_max_holding_days": "number | null",

    "signal_status": "approved | monitor_only",

    "impact_channel": "revenue | input_cost | inventory_gain_loss | working_capital | demand | regulation | logistics | sentiment | other",

    "reasoning": "string",

    "risks_to_thesis": [
      "string"
    ],

    "invalidation_triggers": [
      "string"
    ]
  }
]
```

### 6.1 Validation Rules

The output is invalid if:

- `approved_trade_signals` is missing
- confidence values are outside 0 to 1
- `expected_stock_direction` inside `approved_trade_signals` is not `up` or `down`
- `timeline_bucket` inside `approved_trade_signals` is `uncertain`
- `signal_status` is not `approved` or `monitor_only`
- future market movement is used as reasoning

---

## 7. Modify `news_analysis/reviewList/config.yaml`

Add this configuration block:

```yaml
trade_signal_generation:
  enabled: true

  minimum_confidence_for_signal: 0.50

  minimum_trade_score_for_approved: 0.60

  minimum_trade_score_for_monitor_only: 0.40

  allowed_review_actions_for_signal:
    - keep
    - modify

  disallowed_review_actions_for_signal:
    - downgrade
    - remove

  allowed_signal_statuses:
    - approved
    - monitor_only

  excluded_signal_directions:
    - mixed
    - uncertain

  excluded_timeline_buckets:
    - uncertain

score_weights:
  directness:
    direct: 1.0
    indirect: 0.65
    second_order: 0.35

  sensitivity:
    very_high: 1.0
    high: 0.8
    medium: 0.5
    low: 0.25

  timeline:
    same_day: 1.0
    1_3_days: 0.85
    1_4_weeks: 0.55
    1_6_months: 0.30
    6_months_plus: 0.15
    uncertain: 0.0

suggested_max_holding_days:
  same_day: 1
  1_3_days: 3
  1_4_weeks: 20
  1_6_months: 90
  6_months_plus: 120
  uncertain: null

entry_rules:
  market_timezone: Asia/Kolkata
  market_open_time: "09:15"
  market_close_time: "15:30"
  default_entry: next_tradable_open_after_processed_at
  allow_intraday_entry: true
  intraday_entry_delay_minutes: 15
```

---

## 8. Part 2: Add Signal Normalizer / Orchestration Logic

### 8.1 Goal

The LLM should not be responsible for final deterministic calculations.

Create a deterministic Python module that takes `reviewList.approved_trade_signals` and produces finalized signal records.

Suggested file:

```text
src/news_analysis/signal_normalizer.py
```

If the project has a different structure, place it wherever orchestration logic currently lives.

---

## 9. Signal Normalizer Responsibilities

The normalizer should:

1. Read `approved_trade_signals` from `reviewList` output.
2. Validate each signal.
3. Drop invalid signals.
4. Calculate:
   - `signal_id`
   - `final_trade_score`
   - `entry_allowed_from`
   - `suggested_max_holding_days`
5. Set final `signal_status`.
6. Persist the finalized signal into a Signal Journal table or JSON/CSV store.

---

## 10. Final Trade Score Formula

Implement this deterministic formula:

```text
final_trade_score =
commodity_confidence
Ã— sector_confidence
Ã— company_confidence
Ã— reviewer_confidence
Ã— directness_weight
Ã— sensitivity_weight
Ã— timeline_weight
```

Weights should come from `news_analysis/reviewList/config.yaml`.

Example:

```python
DIRECTNESS_WEIGHT = {
    "direct": 1.0,
    "indirect": 0.65,
    "second_order": 0.35,
}

SENSITIVITY_WEIGHT = {
    "very_high": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.25,
}

TIMELINE_WEIGHT = {
    "same_day": 1.0,
    "1_3_days": 0.85,
    "1_4_weeks": 0.55,
    "1_6_months": 0.30,
    "6_months_plus": 0.15,
    "uncertain": 0.0,
}
```

---

## 11. Signal ID Generation

Generate stable deterministic signal IDs.

Suggested format:

```text
SIG_{news_event_id}_{published_date}_{ticker}_{expected_direction}_{hash}
```

Example:

```text
SIG_EVT20260511001_20260511_INDIGO_DOWN_4E2802
```

Use a short hash from:

```text
news_event_id + published_at + ticker + expected_stock_direction
```

`commodity` and `sector` remain normal journal columns, but they are not part of the signal identity.

---

## 12. Entry Timestamp Logic

Calculate `entry_allowed_from` based on `processed_at`.

Rules:

1. If `processed_at` is before market open:
   - entry = same trading day 09:15

2. If `processed_at` is during market hours:
   - entry = processed_at + 15 minutes
   - round to next valid candle if using 5-minute candles

3. If `processed_at` is after market close:
   - entry = next trading day 09:15

4. If date is weekend/market holiday:
   - entry = next trading day 09:15

Initial implementation can handle weekends first. Later add NSE holiday calendar.

Market config:

```yaml
market_timezone: Asia/Kolkata
market_open_time: "09:15"
market_close_time: "15:30"
intraday_entry_delay_minutes: 15
```

---

## 13. Suggested Max Holding Days

Map from `timeline_bucket`:

```python
MAX_HOLDING_DAYS = {
    "same_day": 1,
    "1_3_days": 3,
    "1_4_weeks": 20,
    "1_6_months": 90,
    "6_months_plus": 120,
}
```

---

## 14. Signal Status Calculation

After calculating `final_trade_score`:

```python
if final_trade_score >= 0.60:
    signal_status = "approved"
elif final_trade_score >= 0.40:
    signal_status = "monitor_only"
else:
    drop signal or mark as rejected internally
```

Do not persist rejected signals into the main approved signal journal unless there is an audit table.

---

## 15. Suggested Signal Journal Schema

Create a DB table or storage model equivalent to:

```sql
CREATE TABLE dbo.TradeSignalJournal (
    signal_id NVARCHAR(200) NOT NULL PRIMARY KEY,

    news_event_id NVARCHAR(100) NOT NULL,
    published_at DATETIME2 NOT NULL,
    processed_at DATETIME2 NOT NULL,

    commodity NVARCHAR(100) NOT NULL,
    commodity_direction NVARCHAR(20) NOT NULL,
    commodity_confidence FLOAT NOT NULL,

    sector NVARCHAR(150) NOT NULL,
    sub_sector NVARCHAR(150) NULL,

    company_name NVARCHAR(255) NULL,
    ticker NVARCHAR(50) NULL,
    exchange NVARCHAR(50) NULL,

    expected_stock_direction NVARCHAR(10) NOT NULL,

    directness NVARCHAR(50) NOT NULL,
    sensitivity NVARCHAR(50) NOT NULL,
    timeline_bucket NVARCHAR(50) NOT NULL,

    sector_confidence FLOAT NOT NULL,
    company_confidence FLOAT NOT NULL,
    reviewer_confidence FLOAT NOT NULL,

    final_trade_score FLOAT NOT NULL,

    entry_allowed_from DATETIME2 NOT NULL,
    suggested_max_holding_days INT NOT NULL,

    signal_status NVARCHAR(30) NOT NULL,

    impact_channel NVARCHAR(100) NULL,
    reasoning NVARCHAR(MAX) NULL,
    risks_to_thesis NVARCHAR(MAX) NULL,
    invalidation_triggers NVARCHAR(MAX) NULL,

    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
```

If using SQLite/Postgres instead of SQL Server, adapt types accordingly.

---

## 16. Part 3: Modify Orchestration Flow

### 16.1 Current Flow

```text
news article
  â†’ dailyNews
  â†’ impactList
  â†’ reviewList
```

### 16.2 New Flow

```text
news article
  â†’ dailyNews
  â†’ impactList
  â†’ reviewList
  â†’ signal_normalizer
  â†’ TradeSignalJournal
  â†’ backtest engine
```

---

## 17. Required Orchestration Changes

Wherever the current agent pipeline is implemented, update it to:

```python
daily_news_output = run_daily_news_agent(article)

impact_list_output = run_impact_list_agent(
    article=article,
    daily_news_output=daily_news_output,
)

review_output = run_review_list_agent(
    article=article,
    daily_news_output=daily_news_output,
    impact_list_output=impact_list_output,
    processed_at=current_timestamp,
)

final_signals = normalize_review_signals(
    review_output=review_output,
    config_path="news_analysis/reviewList/config.yaml",
)

persist_trade_signals(final_signals)
```

Important:

- `processed_at` must be captured before or at the time `reviewList` runs.
- This timestamp should not be generated later during backtesting.
- Backtesting should only read from persisted signals.

---

## 18. Part 4: Introduce Backtesting Logic

Current implementation keeps the news signal backtest in the existing backtest folder.

Implemented files:

```text
src/backtest/news_underlying_backtest.py
src/backtest/news_e2e_backtest.py
```

Technical-analysis historical backtesting is separate:

```text
src/backtest/historical_underlying_backtest.py
src/backtest/historical_e2e_backtest.py
```

---

## 19. Backtest Input

The backtester should consume finalized records from:

```text
TradeSignalJournal
```

Required fields:

```text
signal_id
ticker
exchange
entry_allowed_from
expected_stock_direction
timeline_bucket
suggested_max_holding_days
final_trade_score
signal_status
```

Only backtest:

```text
signal_status = approved
```

Optionally track:

```text
monitor_only
```

separately for research but not trading P&L.

---

## 20. Market Data Required

For every ticker, need OHLCV data from `entry_allowed_from` until max holding period.

Minimum required columns:

```text
ticker
timestamp / trade_date
open
high
low
close
volume
```

For event study, also need benchmark index data:

```text
NIFTY 50
NIFTY 500
sector index if available
```

---

## 21. Part 5: Event Study Backtest

### 21.1 Purpose

Measure whether the prediction was directionally correct over fixed horizons.

Use horizons:

```python
EVENT_WINDOWS = [1, 3, 5, 10, 20, 60, 120]
```

For each signal:

1. Get entry price from `entry_allowed_from`.
2. Calculate stock return over each window.
3. Calculate benchmark return over same window.
4. Calculate abnormal return:

```text
abnormal_return = stock_return - benchmark_return
```

5. For positive signal:
   - success if abnormal_return > 0

6. For negative signal:
   - success if abnormal_return < 0

Store result per signal per horizon.

---

## 22. Suggested Event Study Result Table

```sql
CREATE TABLE dbo.SignalEventStudyResult (
    result_id BIGINT IDENTITY(1,1) PRIMARY KEY,

    signal_id NVARCHAR(200) NOT NULL,

    horizon_days INT NOT NULL,

    entry_price FLOAT NULL,
    exit_price FLOAT NULL,

    stock_return FLOAT NULL,
    benchmark_return FLOAT NULL,
    abnormal_return FLOAT NULL,

    expected_stock_direction NVARCHAR(10) NOT NULL,

    directionally_correct BIT NULL,

    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
```

---

## 23. Part 6: Triple-Barrier Backtest

### 23.1 Purpose

Measure whether the signal would have worked as a trade before thesis expiry.

For each signal, define:

```text
profit barrier
stop-loss barrier
vertical time barrier
```

Recommended initial rules:

```python
PROFIT_TARGET_PCT = 0.03
STOP_LOSS_PCT = 0.02
```

Better later version:

```text
profit barrier = +2 Ã— ATR
stop barrier = -1 Ã— ATR
```

Start with percentage barriers if ATR is not yet available.

---

## 24. Triple-Barrier Rules

### 24.1 For Long Signal

Expected direction = `up`.

```text
profit barrier = entry_price Ã— 1.03
stop barrier = entry_price Ã— 0.98
```

### 24.2 For Short / Negative Signal

Expected direction = `down`.

```text
profit barrier = entry_price Ã— 0.97
stop barrier = entry_price Ã— 1.02
```

### 24.3 Vertical Barrier

```text
entry date + suggested_max_holding_days
```

Whichever barrier is hit first determines the label.

Output labels:

```text
profit_hit
stop_hit
vertical_timeout_positive
vertical_timeout_negative
vertical_timeout_flat
insufficient_data
```

---

## 25. Suggested Triple-Barrier Result Table

```sql
CREATE TABLE dbo.SignalTripleBarrierResult (
    result_id BIGINT IDENTITY(1,1) PRIMARY KEY,

    signal_id NVARCHAR(200) NOT NULL,

    entry_time DATETIME2 NOT NULL,
    exit_time DATETIME2 NULL,

    entry_price FLOAT NULL,
    exit_price FLOAT NULL,

    expected_stock_direction NVARCHAR(10) NOT NULL,

    profit_target_price FLOAT NULL,
    stop_loss_price FLOAT NULL,

    max_holding_days INT NOT NULL,

    barrier_hit NVARCHAR(50) NULL,

    realized_return FLOAT NULL,

    directionally_correct BIT NULL,

    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
```

---

## 26. Part 7: MFE / MAE Analysis

### 26.1 Purpose

Measure whether the idea worked at any point, even if the final exit missed it.

For each signal:

```text
MFE = maximum favorable excursion
MAE = maximum adverse excursion
days_to_peak = number of days until best favorable move
```

For expected direction `up`:

```text
MFE = max(high / entry_price - 1)
MAE = min(low / entry_price - 1)
```

For expected direction `down`:

```text
MFE = max(entry_price / low - 1)
MAE = min(entry_price / high - 1)
```

Store:

```text
signal_id
max_favorable_return
max_adverse_return
days_to_peak
days_to_worst
```

---

## 27. Suggested MFE / MAE Result Table

```sql
CREATE TABLE dbo.SignalMfeMaeResult (
    result_id BIGINT IDENTITY(1,1) PRIMARY KEY,

    signal_id NVARCHAR(200) NOT NULL,

    max_favorable_return FLOAT NULL,
    max_adverse_return FLOAT NULL,
    days_to_peak INT NULL,
    days_to_worst INT NULL,

    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
```

---

## 28. Part 8: Portfolio Simulation

### 28.1 Purpose

Check whether the full strategy makes money after combining signals.

Initial portfolio simulation rules:

```text
Only trade signal_status = approved
Ignore monitor_only for P&L
Position size proportional to final_trade_score
Max position size per stock = 5%
Max exposure per sector = 25%
Max exposure per news event = 15%
Use entry_allowed_from as earliest entry
Use triple-barrier exit
Include transaction cost and slippage
```

Initial cost assumption:

```text
round_trip_cost_pct = 0.20%
```

This can later be replaced with actual brokerage/STT/slippage logic.

---

## 29. Part 9: Backtest Summary Metrics

The backtest runner should output the following metrics.

### 29.1 Signal Quality Metrics

```text
number_of_signals
number_of_approved_signals
number_of_monitor_only_signals
average_final_trade_score
```

### 29.2 Event-Study Metrics

```text
accuracy_T1
accuracy_T3
accuracy_T5
accuracy_T10
accuracy_T20
accuracy_T60
accuracy_T120
average_abnormal_return_by_horizon
```

### 29.3 Triple-Barrier Metrics

```text
profit_hit_rate
stop_hit_rate
timeout_rate
average_realized_return
median_realized_return
```

### 29.4 MFE / MAE Metrics

```text
average_MFE
average_MAE
median_days_to_peak
```

### 29.5 Portfolio Metrics

```text
total_return
win_rate
average_win
average_loss
max_drawdown
sharpe_if_available
profit_factor
```

---

## 30. Part 10: Important Backtesting Rules

Do not use next-day return as the only measure.

Do not wait indefinitely until price reversal.

Do not choose exit date after looking at future chart.

Use three layers:

```text
1. Event study
   â†’ Did the stock move in the predicted direction over fixed windows?

2. Triple barrier
   â†’ Would a realistic trade have hit profit target before stop-loss or expiry?

3. MFE / MAE
   â†’ Did the thesis work at any point and how much adverse movement occurred?
```

---

## 31. Acceptance Criteria

Implementation is complete when:

1. `reviewList` output always includes `approved_trade_signals`.
2. Invalid signals are excluded:
   - mixed direction
   - uncertain direction
   - uncertain timeline
   - weak company mapping
3. Signal normalizer calculates:
   - `signal_id`
   - `final_trade_score`
   - `entry_allowed_from`
   - `suggested_max_holding_days`
4. Finalized signals are persisted into `TradeSignalJournal`.
5. Backtester can read from `TradeSignalJournal`.
6. Backtester produces:
   - event-study results
   - triple-barrier results
   - MFE / MAE results
7. Backtester stores results in DB tables or exports CSV files.
8. No future price data is used before signal persistence.
9. The pipeline can run as:

```text
news article
  â†’ dailyNews
  â†’ impactList
  â†’ reviewList
  â†’ signal_normalizer
  â†’ signal journal
  â†’ backtester
```

---

## 32. Implementation Priority

### Phase 1

Modify `reviewList` schema and prompt files.

### Phase 2

Add `signal_normalizer.py`.

### Phase 3

Persist finalized signals to DB or CSV.

### Phase 4

Add event-study backtest.

### Phase 5

Add triple-barrier backtest.

### Phase 6

Add MFE / MAE analysis.

### Phase 7

Add portfolio simulation.

---

## 33. Do Not Do

Do not:

- let the LLM calculate trade score loosely
- use future stock price data inside agents
- use actual post-news price movement to revise the signal
- use post-news price action inside `dailyNews`, `impactList`, or `reviewList`
- backtest only next-day price movement
- use unlimited â€œwait until reversalâ€ exits
- create buy/sell/hold recommendations in agent output
- create signals for uncertain or mixed directions
- create signals without a company/ticker unless explicitly configured for sector-index backtesting
- choose exit windows after looking at future chart data

---

## 34. Expected Final Design

```text
src/
  news_analysis/
    dailyNews/
    impactList/
    reviewList/
    signal_normalizer.py

  services/
    orchestration_service.py
    sector_expansion_service.py
    backfill_service.py

  backtest/
    news_underlying_backtest.py
    news_e2e_backtest.py
    historical_underlying_backtest.py
    historical_e2e_backtest.py

output/
  trade_signal_journal.csv
```

---

## 35. Summary

The final system should work as follows:

1. `dailyNews` identifies commodity impact from news.
2. `impactList` identifies impacted sectors and example stocks.
3. `reviewList` validates the reasoning and emits `approved_trade_signals`.
4. `signal_normalizer.py` deterministically calculates trade score, entry time, max holding period, and signal ID.
5. Finalized signals are persisted in `TradeSignalJournal`.
6. Backtesting reads only from persisted signals.
7. Backtesting evaluates signals through:
   - event-study windows
   - triple-barrier outcomes
   - MFE / MAE analysis
   - portfolio simulation

This ensures the pipeline remains auditable, avoids look-ahead bias, and does not rely only on next-day stock movement for validation.


