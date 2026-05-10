# Agent Orchestrator

This document explains the end-to-end flow from news articles to stock data setup and prediction output.

Main service: `src/services/orchestration_service.py`  
Agents: `src/agents`  
Sector expansion: `src/services/sector_watchlist_service.py`  
Backfill: `src/services/backfill_service.py`  
Predictions: `src/services/prediction_service.py`
Watched backtest: `src/backtest/watched_underlying_backtest.py`

## Simple Flow

```text
news articles for reference_date
  -> dailyNews agent
  -> impactList agent
  -> reviewList agent
  -> SectorWatchlistService
  -> BackfillService for newly added symbols
  -> PredictionService for all identified symbols
  -> output/<reference_date>.csv
  -> watched underlying backtest
```

The agents make judgement calls. The services do deterministic data work.

## What The Orchestrator Does

`OrchestrationService` is the main coordinator. Call it with:

```python
OrchestrationService.default().run(reference_date=...)
```

It runs these steps:

1. `dailyNews` reads news for the reference date and identifies affected sectors, commodities, industries, and symbols mentioned in news.
2. `impactList` turns those findings into a ranked impact list.
3. `reviewList` reviews the ranked impact list and approves the sectors that should move forward.
4. `SectorWatchlistService` expands approved sectors into NSE stocks, inserts missing stocks into `WatchedInstrument`, and skips duplicates.
5. `BackfillService` runs only for `new_symbols`, because existing watched symbols should already have historical data.
6. `PredictionService` runs for all identified `symbols`, not only new symbols.
7. Prediction output is saved as one CSV file for the reference date.
8. `watched_underlying_backtest` evaluates each `instrument x strategy` prediction against the next trading day.

## Agent Roles

Each agent has its own folder:

```text
src/agents/
  dailyNews/
    agent.py
    output_schema.py
    config.yaml
  impactList/
    agent.py
    output_schema.py
    config.yaml
  reviewList/
    agent.py
    output_schema.py
    config.yaml
```

### dailyNews

Reads configured news sources for a `reference_date`.

It should identify:

- the news item
- impacted sector, industry, commodity, or theme
- any raw company or symbol mentions
- confidence and rationale

### impactList

Consumes `dailyNews` output and creates an ordered impact list.

It should identify:

- impacted sectors
- impact direction, such as positive, negative, or neutral
- impact score
- source headlines and rationale

### reviewList

Reviews the impact list before data work starts.

It should:

- approve or reject impacted sectors
- preserve the `reference_date`
- return approved sectors in priority order

## SectorWatchlistService

Location: `src/services/sector_watchlist_service.py`

This service converts reviewed sectors into stock symbols.

It does four things:

1. Takes approved sectors from `reviewList`.
2. Finds relevant NSE stocks for those sectors.
3. Inserts only missing stocks into `dbo.WatchedInstrument`.
4. Returns both all identified symbols and newly added symbols.

Important return fields:

| Field | Meaning |
|---|---|
| `symbols` | All resolved stocks for the approved sectors. Predictions run for this list. |
| `new_symbols` | Stocks newly inserted into `WatchedInstrument`. Backfill runs for this list. |
| `sector_results` | Per-sector expansion details and diagnostics. |
| `option_instruments` | Option-instrument refresh result for newly inserted symbols. |

## Backfill Logic

The orchestrator calls `BackfillService` only when `new_symbols` is not empty.

Backfill window:

```text
start_date = reference_date - 90 days
end_date   = reference_date - 1 day
```

Backfill prepares:

- `UnderlyingSnapshot`
- `UnderlyingCandle5m`
- `OptionInstrument`
- `OptionSnapshot`
- `OptionSnapshotCalc`

This means a newly discovered stock gets enough recent history before prediction and later option workflows.

## Prediction Logic

The orchestrator calls:

```python
PredictionService.run_reference_date_predictions_for_symbols(
    instruments=symbols,
    reference_date=review_reference_date,
    strategies=prediction_strategies,
)
```

Important detail:

- Backfill runs for `new_symbols`.
- Predictions run for `symbols`.

That distinction matters because a stock can already exist in the watchlist and still be relevant to today's reviewed news.

## Prediction Output

Predictions are saved as one CSV file per reference date:

```text
output/<reference_date>.csv
```

Example:

```text
output/2026-05-08.csv
```

The CSV has one row per stock/index and one column per prediction strategy.

Example shape:

```text
reference_date,instrument,status,error,MaTrend_001,RsiMeanReversion_6535,aggregate_decision
2026-05-08,INFY,ok,,CALL,PUT,NO_POSITION
2026-05-08,TCS,ok,,CALL,CALL,CALL
```

The final `aggregate_decision` column is produced by majority vote across the strategy columns.

## Watched Backtest

After prediction, the orchestrator calls the watched underlying backtest.

Input:

```text
output/<reference_date>.csv
```

The watched backtest expects CSV input.

The backtest explodes the prediction matrix into one row per:

```text
instrument x strategy
```

It compares each prediction with the next trading day's underlying move and marks results such as:

- `CORRECT`
- `INCORRECT`
- `OK_NO_TRADE`
- `MISSED_CALL`
- `MISSED_PUT`
- `N/A`

Those results are added back to the same prediction file as columns such as:

```text
MaTrend_001_backtest_result
aggregate_decision_backtest_result
```

## Design Rule

Use agents for judgement:

- Which news matters?
- Which sector or commodity is impacted?
- Which reviewed sectors should move forward?

Use services for deterministic work:

- Expand sectors into NSE stocks.
- Insert watched instruments.
- Run backfill.
- Run technical-analysis predictions.
- Save the prediction output file.
