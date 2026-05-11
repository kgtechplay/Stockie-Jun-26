# Agent Orchestrator

## Gist

This workflow turns news into stock-level trade signals.

```text
news -> dailyNews -> impactList -> reviewList -> signal_normalizer
     -> output/trade_signal_journal.csv
     -> sector expansion + backfill + news backtest
```

Read this doc when you need to understand how the news-analysis pieces connect. For backtest math, go to `TeamDocs/backtestStrategy.md`.

## Key Files

Main service: `src/services/orchestration_service.py`  
News-analysis folders: `src/news_analysis/dailyNews`, `src/news_analysis/impactList`, `src/news_analysis/reviewList`  
Signal normalizer: `src/news_analysis/signal_normalizer.py`  
Sector expansion: `src/services/sector_expansion_service.py`  
News signal backtest: `src/backtest/news_underlying_backtest.py`

## Flow

```text
news/article context
  -> dailyNews
  -> impactList
  -> reviewList
  -> signal_normalizer
  -> output/trade_signal_journal.csv
  -> SectorExpansionService
  -> BackfillService
  -> news_underlying_backtest
```

The news-analysis components reason. Python services normalize, persist, backfill, and backtest.

## What Each Piece Owns

| Piece | Owns |
|---|---|
| `dailyNews` | Finds impacted commodities from news. |
| `impactList` | Maps commodities to sectors/stocks. |
| `reviewList` | Emits approved stock-level signals. |
| `signal_normalizer` | Calculates ID, score, entry time, holding period. |
| `SectorExpansionService` | Expands sectors into NSE stocks and watched instruments. |
| `BackfillService` | Ensures required underlying/option history exists. |
| `news_underlying_backtest` | Backtests finalized news signals. |

## Folder Contract

Each news-analysis agent folder follows the same structure:

```text
agent_definition.md   prompt and responsibility description
config.yaml           thresholds, rules, and guardrails
output_schema.py      Python dataclass contract used by code
agent.py              Azure-backed agent runner
```

The `agent_definition.md` file is documentation/prompt text. The `output_schema.py` file stays as Python because orchestration imports its dataclasses.

## Azure Setup

The agent runners use `src/news_analysis/azure_agent_client.py`.

Supported environment variables:

```text
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_DEPLOYMENT
AZURE_OPENAI_API_VERSION
```

Aliases are also supported:

```text
AZURE_AI_ENDPOINT
AZURE_AI_API_KEY
AZURE_AI_DEPLOYMENT
AZURE_AI_API_VERSION
```

If Azure is not configured, the agents return placeholder outputs so local import and dry-run checks still work.

## Agent Roles

### dailyNews

Location: `src/news_analysis/dailyNews`

Purpose:

- read a news item or daily news context
- identify impacted commodities
- classify commodity direction, mechanism, timeline, and confidence
- avoid stock recommendations

Output class: `DailyNewsOutput`

### impactList

Location: `src/news_analysis/impactList`

Purpose:

- map commodity impacts into sectors
- identify relevant stocks where the mapping is clear
- rank sector impact by directness, sensitivity, confidence, and timeline

Output class: `ImpactListOutput`

### reviewList

Location: `src/news_analysis/reviewList`

Purpose:

- review and filter impactList output
- remove weak or speculative causal chains
- emit flat `approved_trade_signals`

Output class: `ReviewListOutput`

Important: `reviewList` should not calculate `signal_id`, `final_trade_score`, `entry_allowed_from`, or `suggested_max_holding_days`. Those are deterministic fields calculated by `signal_normalizer.py`.

## Signal Normalizer

Location: `src/news_analysis/signal_normalizer.py`

Responsibilities:

- validate `reviewList.approved_trade_signals`
- drop invalid signals
- calculate `final_trade_score`
- calculate `entry_allowed_from`
- calculate `suggested_max_holding_days`
- assign deterministic `signal_id`
- persist finalized rows to `output/trade_signal_journal.csv`

Weights come from `src/news_analysis/reviewList/config.yaml`.

`signal_id` identifies one ticker-level signal from one news event and is generated from:

```text
news_event_id + published_at + ticker + expected_stock_direction
```

Example:

```text
SIG_EVT20260511001_20260511_INDIGO_DOWN_4E2802
```

## OrchestrationService

Call:

```python
from src.services.orchestration_service import OrchestrationService

result = OrchestrationService.default().run(reference_date=...)
```

What it does:

1. Runs `dailyNews`.
2. Runs `impactList`.
3. Runs `reviewList`.
4. Calls `normalize_review_signals`.
5. Persists finalized signals to the signal journal.
6. Expands finalized signal sectors using `SectorExpansionService`.
7. Backfills finalized signal tickers for the last 90 days.
8. Runs `news_underlying_backtest` against the signal journal.

## SectorExpansionService

Location: `src/services/sector_expansion_service.py`

This service expands sectors into NSE stocks and inserts missing records into `WatchedInstrument`.

Current orchestration uses finalized signal sectors for sector expansion, while backfill runs on finalized signal tickers. If the business rule changes to backfill every newly added sector constituent, orchestration should use `sectorExpansion.new_symbols` for backfill too.

## Output

Finalized signals are stored in:

```text
output/trade_signal_journal.csv
```

News backtest result columns are added back to the same journal file.

Backtest thresholds, result columns, and summary dictionaries are documented in:

```text
TeamDocs/backtestStrategy.md
```

## Local App

Run:

```powershell
python flask_app.py
```

The app exposes a technical-analysis path and a news-signal backtest path. News prediction is disabled in the UI for now. See `run_local.md` at the repo root for full step-by-step instructions.
