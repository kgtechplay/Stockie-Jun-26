# Agent Orchestrator

This document explains the InstrumentWatcher agent flow and how it connects news analysis to stock data setup.

Current code location: `src/agents`
Main orchestrator: `src/services/orchestration_service.py`
Sector expansion service: `src/services/sector_watchlist_service.py`

Note: older conversations may refer to this area as `InstrumentWatcher`. In the current repo, the agent folders live under `src/agents`.

## Purpose

The agent orchestrator turns news into a prepared data universe.

The flow is:

```text
news articles
  -> dailyNews agent
  -> impactList agent
  -> reviewList agent
  -> sector_watchlist_service
  -> WatchedInstrument DB
  -> option instrument refresh
  -> 3-month backfill
```

The agents should decide what matters. The services should perform deterministic data work.

## Agent Folders

Each agent has its own folder under `src/agents`:

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

Each folder has the same basic responsibilities:

| File | Purpose |
|---|---|
| `agent.py` | Runtime agent implementation. Current versions are placeholders. |
| `output_schema.py` | Dataclasses defining structured output from that agent. |
| `config.yaml` | Agent config, intent, and expected checks. |

## Agent 1: `dailyNews`

Location: `src/agents/dailyNews`

Purpose:

- Read daily news from a predetermined source list.
- Identify macro or micro events.
- Extract the impacted industry, commodity, sector, or underlying theme.
- Preserve source headline/source information for downstream traceability.

Output schema:

| Object | Key Fields | Meaning |
|---|---|---|
| `DailyNewsOutput` | `as_of`, `sources`, `findings` | Full daily news scan result. |
| `DailyNewsFinding` | `headline`, `source`, `impacted_underlying`, `impact_type`, `rationale`, `confidence`, `related_symbols` | One extracted event or market-impact finding. |

Example output intent:

```text
Headline: Crude oil prices rise after supply disruption
Impacted underlying: Oil and Gas
Impact type: commodity_supply_shock
Related symbols: optional direct tickers if mentioned
```

## Agent 2: `impactList`

Location: `src/agents/impactList`

Purpose:

- Convert daily news findings into candidate impacted stocks or sectors.
- Rank potential impact across industries/stocks.
- Preserve rationale and source headlines.

Output schema:

| Object | Key Fields | Meaning |
|---|---|---|
| `ImpactListOutput` | `as_of`, `candidates` | Ranked candidate list. |
| `ImpactCandidate` | `rank`, `tradingsymbol`, `name`, `industry`, `impact_direction`, `impact_score`, `rationale`, `source_headlines` | One candidate stock/industry impact record. |

Current placeholder behavior:

- Creates placeholder candidates.
- Uses `finding.impacted_underlying` as the candidate `industry`.

Expected future behavior:

- Use news/event context plus sector/commodity exposure rules.
- Return direct stock candidates when known.
- Return sector/industry labels when the best action is to expand a sector through NSE.

## Agent 3: `reviewList`

Location: `src/agents/reviewList`

Purpose:

- Review and approve the ranked impact list.
- Add checks before any data work is triggered.
- Emit approved symbols and identified sectors for downstream setup.

Output schema:

| Object | Key Fields | Meaning |
|---|---|---|
| `ReviewListOutput` | `as_of`, `reviewed_candidates`, `identified_sectors` | Reviewed result from `impactList`. |
| `ReviewedImpactCandidate` | `tradingsymbol`, `approved`, `final_rank`, `final_score`, `sector`, `industry`, `checks`, `review_notes` | Final reviewed candidate. |

Important helper methods:

| Method | Purpose |
|---|---|
| `approved_symbols()` | Returns approved direct stock symbols. |
| `sectors()` | Returns de-duplicated sectors from `identified_sectors`, `sector`, and `industry`. |

## `OrchestrationService`

Location: `src/services/orchestration_service.py`

Main method:

```python
OrchestrationService.default().run()
```

Execution order:

1. Run `DailyNewsAgent`.
2. Pass output to `ImpactListAgent`.
3. Pass output to `ReviewListAgent`.
4. Backfill directly approved symbols, if any.
5. Pass reviewed sectors to `SectorWatchlistService`.

Return payload:

| Key | Meaning |
|---|---|
| `dailyNews` | Raw daily news findings. |
| `impactList` | Ranked candidate impacts. |
| `reviewList` | Reviewed/approved impact list. |
| `backfill` | Backfill result for directly approved symbols. |
| `sectorWatchlist` | Sector expansion, watchlist insertion, option-instrument refresh, and backfill result. |

## `SectorWatchlistService`

Location: `src/services/sector_watchlist_service.py`

Purpose:

- Take one or more sectors from `reviewList`.
- Fetch sector constituents from NSE, not from the local DB.
- Add missing stocks to `dbo.WatchedInstrument`.
- Trigger option instrument setup.
- Trigger last-3-month data backfill for newly added stocks.

This should remain code-based, not LLM-based.

Why:

- NSE is the source of truth for sector/index constituents.
- DB inserts and backfills must be deterministic and repeatable.
- LLMs can identify likely sectors from news, but should not invent constituent lists.

## Sector Expansion Flow

```text
reviewList.sectors()
  -> normalize sector name
  -> NSE equity-stockIndices API
  -> constituent symbols
  -> skip symbols already active in WatchedInstrument
  -> enrich from StockDB when available
  -> insert new WatchedInstrument rows
  -> daily_optionInstrument_refresh for new symbols
  -> BackfillService for last 90 days
```

## NSE Sector Name Handling

`SectorWatchlistService` normalizes common labels to NSE index names.

Examples:

| Agent Sector | NSE Index |
|---|---|
| `Auto` | `NIFTY AUTO` |
| `Oil and Gas` | `NIFTY OIL & GAS` |
| `IT` | `NIFTY IT` |
| `Pharma` | `NIFTY PHARMA` |
| `Financial Services` | `NIFTY FINANCIAL SERVICES` |

If no alias exists, the service assumes `NIFTY {SECTOR}`.

## WatchedInstrument Insert Behavior

For each new NSE constituent:

1. Check if the symbol already exists as an active `STOCK` in `WatchedInstrument`.
2. Look up the symbol in `StockDB` for Kite metadata.
3. Insert a `WatchedInstrument` row with:
   - `instrument_type = STOCK`
   - `exchange = NSE`
   - `is_fo_enabled = True`
   - `is_active = True`
   - `sector = NSE index name`
   - `industry = NSE industry when available, otherwise NSE index name`

If the stock is not present in `StockDB`, the row is still inserted with basic NSE symbol information. Token resolution can still happen later in backfill scripts.

## Backfill Trigger

When new stocks are inserted:

1. `daily_optionInstrument_refresh.run_load_option_instruments()` runs for the new symbols.
2. `BackfillService.run_backfill()` runs for the last `90` days by default.
3. For stocks, the backfill covers:
   - `UnderlyingSnapshot`
   - `UnderlyingCandle5m`
   - `OptionInstrument`
   - `OptionSnapshot`
   - `OptionSnapshotCalc`

## Morning Job Relationship

The daily morning job is still separate from the agent flow.

Recommended morning job:

```text
daily_get_kite_access_token.py
daily_optionInstrument_refresh.py
daily_market_refresh.py
```

Agent orchestration is event/news driven. The morning job is routine market-data maintenance.

## Design Rule

Use agents for judgment:

- What news matters?
- Which sector or commodity is impacted?
- Should the impact list be approved?

Use services for deterministic data work:

- Fetch NSE sector constituents.
- Insert rows into `WatchedInstrument`.
- Refresh option instruments.
- Backfill market data.
