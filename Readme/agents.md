# Prediction Agents (MVP Reference)

This document is the implementation reference for the prediction agents under `src/prediction/agents/`.

It explains:
- objective of each agent
- expected inputs
- suggested logic/extensions
- output schema
- how outputs are consumed downstream

## Where agents fit in the flow

Current runtime usage:

1. `api.py` -> prediction API (`/api/predictions/run`)
2. `src/prediction/prediction_service.py` -> loads OHLC window and calls aggregator
3. `src/prediction/aggregator/index_aggregator.py`
4. Aggregator calls:
   - `EventCalendarAgent.get_upcoming_events(...)`
   - `NewsAgent.fetch_news(...)`
   - `ImpactScoringAgent.score(...)`
5. Aggregator combines TA + agent signals into `PredictionOutput`

## Shared contracts (canonical schemas)

Defined in `src/prediction/contracts.py`.

### `EventItem`

Represents a calendar/risk event.

Fields:
- `event_id: str` (uuid4-generated)
- `name: str`
- `event_type: str` (`RBI`, `FED`, `CPI`, `BUDGET`, `EXPIRY`, `EARNINGS_CLUSTER`, `UNKNOWN`, etc.)
- `start_time: datetime | None`
- `end_time: datetime | None`
- `risk_level: str` (`LOW`, `MEDIUM`, `HIGH`)
- `expected_volatility: float | None`
- `notes: str | None`

### `NewsItem`

Represents a normalized news article/headline.

Fields:
- `news_id: str` (uuid4-generated)
- `title: str`
- `source: str`
- `published_at: datetime | None`
- `url: str | None`
- `category: str` (`MACRO_GLOBAL`, `INDIA_POLICY`, `SECTOR`, `STOCK`, `UNKNOWN`)
- `entities: list[str]` (e.g. `BANKS`, `IT`, `HDFCBANK`, `INFY`)
- `summary: str | None`
- `sentiment: str` (`POS`, `NEG`, `NEUTRAL`, `MIXED`, `UNKNOWN`)
- `confidence: float` (clamped to `0..1`)

### `Signal`

Represents a structured directional signal used by the aggregator.

Fields:
- `signal_id: str` (uuid4-generated)
- `source: str` (`TA`, `NEWS`, `EVENT`, etc.)
- `instrument: str` (e.g. `NIFTY`, `BANKNIFTY`)
- `scope: str` (`INDEX`, `SECTOR`, `STOCK`)
- `direction: str` (`CALL`, `PUT`, `NO_POSITION`)
- `strength: float` (`0..1`)
- `confidence: float` (`0..1`)
- `horizon: str` (`INTRADAY`, `1D`, `3D`, etc.)
- `reason: str`
- `metadata: dict[str, Any]`

## 1) Event Calendar Agent

Module: `src/prediction/agents/event_calendar_agent.py`

### Objective

Produce a deterministic list of upcoming market-impact events for the prediction window.

MVP behavior currently focuses on:
- weekly expiry proximity (`EXPIRY`)
- optional manual event injection via env (`MANUAL_EVENTS_JSON`)

### Current interface

`EventCalendarAgent.get_upcoming_events(instrument: str, as_of: datetime) -> list[EventItem]`

### Expected inputs

- `instrument`
  - Index identifier (`NIFTY`, `BANKNIFTY`, etc.)
  - Used mainly for event labeling/scoping
- `as_of`
  - Decision timestamp
  - Used to compute weekly expiry proximity and event window relevance

### Current logic (MVP)

- Approximates weekly expiry as next Thursday.
- Computes business days to expiry (Mon-Fri only; holidays not modeled).
- Emits `EXPIRY` event if within 3 business days.
- Risk mapping:
  - `<= 1` day -> `HIGH`
  - `<= 3` days -> `MEDIUM`
- Loads optional manual events from `MANUAL_EVENTS_JSON` (JSON list).

### Suggested logic to add (safe extensions)

- Exchange holiday calendar support (true expiry shift).
- Instrument-aware expiry cadence (weekly/monthly variants, index-specific rules).
- RBI/FED/CPI/Budget schedule provider (config or local file first, API later).
- Event deduplication and relevance filtering.
- Time-window logic (e.g. only emit `HIGH` if event is within next 24h).
- Event confidence and source provenance in `notes`/`metadata` (if contract extended later).

### Output schema

Returns `list[EventItem]`.

Minimum expectations for downstream compatibility:
- `event_type`
- `risk_level`
- `name`
- `start_time` / `end_time` (recommended)

### Downstream usage

Consumed by `ImpactScoringAgent.score(...)`:
- `HIGH` risk events can produce a strong `EVENT` `NO_POSITION` signal.
- `MEDIUM` risk events can reduce directional confidence via softer `EVENT` signal.

Aggregator (`index_aggregator.py`) then:
- applies higher weight to `EVENT` source
- can gate final decision to `NO_POSITION` if strong event-risk signal exists

## 2) News Agent

Module: `src/prediction/agents/news_agent.py`

### Objective

Normalize raw news headlines into structured `NewsItem` records for downstream impact scoring.

Current implementation is a non-network stub that reads a local JSON sample file.

### Current interface

`NewsAgent.fetch_news(as_of: datetime, lookback_hours: int = 24) -> list[NewsItem]`

### Expected inputs

- `as_of`
  - Decision timestamp used to filter lookback window
- `lookback_hours`
  - Maximum age of news to include (default `24`)

Configuration:
- `NEWS_SAMPLE_JSON_PATH` (optional)
  - If absent/invalid, returns empty list `[]`

### Current logic (MVP)

- Reads local JSON list from `NEWS_SAMPLE_JSON_PATH`.
- Filters out invalid rows and rows with empty titles.
- Parses `published_at` (ISO datetime if present).
- Applies `lookback_hours` cutoff.
- If row lacks fields, infers:
  - `category` via keyword classifier (`MACRO_GLOBAL`, `INDIA_POLICY`, `SECTOR`, `UNKNOWN`)
  - `entities` via simple title entity mapping
  - `sentiment` + confidence via keyword rules (`POS`, `NEG`, `NEUTRAL`)

### Suggested logic to add (safe extensions)

- Real providers (RSS, news APIs) behind same `fetch_news(...)` interface.
- Source reliability weighting (e.g. exchange filing > social headline).
- India macro vs global macro subcategorization expansion.
- Sector/stock mapping using instrument master / configurable dictionaries.
- Duplicate headline clustering and canonicalization.
- Multi-headline sentiment aggregation before returning.
- Language normalization and date timezone normalization (IST/UTC).

### Output schema

Returns `list[NewsItem]`.

Minimum downstream expectations:
- `title`
- `category`
- `entities`
- `sentiment`
- `confidence`

### Downstream usage

Consumed by `ImpactScoringAgent.score(...)`:
- maps `POS -> CALL`, `NEG -> PUT`, others -> `NO_POSITION`
- boosts strength for `MACRO_GLOBAL` / `INDIA_POLICY`
- boosts strength for bank-linked entities when instrument is `BANKNIFTY`
- boosts confidence when multiple news items agree on category + direction

Aggregator then combines generated `NEWS` signals with TA/EVENT signals.

## 3) Impact Scoring Agent

Module: `src/prediction/agents/impact_scoring_agent.py`

### Objective

Translate `EventItem` and `NewsItem` inputs into normalized directional `Signal` objects that the aggregator can weight and combine.

This is the main boundary between:
- unstructured/semi-structured context (`NewsItem`, `EventItem`)
- model-ready structured signals (`Signal`)

### Current interface

`ImpactScoringAgent.score(instrument: str, news: list[NewsItem], events: list[EventItem]) -> list[Signal]`

### Expected inputs

- `instrument`
  - Used for instrument-sensitive logic (e.g. `BANKNIFTY` + `BANKS` entity boost)
- `news`
  - Already normalized `NewsItem` records
- `events`
  - Already normalized `EventItem` records

### Current logic (MVP)

#### Event -> Signal

- If any `HIGH` risk event exists:
  - emits `EVENT` signal with:
    - `direction = NO_POSITION`
    - `strength ~ 0.7`
    - `confidence ~ 0.7`
- If any `MEDIUM` risk event exists:
  - emits softer `EVENT` `NO_POSITION` signal

#### News -> Signal

For each `NewsItem`:
- sentiment to direction mapping:
  - `POS -> CALL`
  - `NEG -> PUT`
  - otherwise `NO_POSITION`
- base strength starts at `0.3`
- strength boosts:
  - macro/policy category boost
  - `BANKNIFTY` + `BANKS` entity boost
- confidence starts from `news.confidence`
- confidence increases (capped) when multiple headlines agree by `(category, direction)`
- emits one `NEWS` signal per item

#### Aggregate news signal (optional summary)

- Computes a net directional score from news signals
- Emits one additional aggregate `NEWS` signal if positive/negative tilt exceeds threshold

### Suggested logic to add (safe extensions)

- Time decay by headline age (`published_at`).
- Event window sensitivity (`now` vs event start/end proximity).
- Sector-to-index mapping weights (`IT` stronger for NIFTY IT basket, etc.).
- Instrument-specific policy sensitivity (banks to RBI signals).
- Contradiction handling (mixed headlines lower confidence).
- Entity-level impact routing (`scope = STOCK` / `SECTOR`) before index aggregation.
- Explainability metadata:
  - source rows used
  - scoring rule ids
  - weight contributions

### Output schema

Returns `list[Signal]`.

Each emitted signal should include:
- `source` (`NEWS` or `EVENT`)
- `instrument`
- `scope` (currently `INDEX`)
- `direction`
- `strength`
- `confidence`
- `horizon`
- `reason`
- optional `metadata`

### Downstream usage

Consumed by `src/prediction/aggregator/index_aggregator.py`:
- concatenated with TA signals from `technical/strategies.py`
- weighted by source (`TA`, `NEWS`, `EVENT`)
- converted into final scalar score
- optionally gated to `NO_POSITION` if strong `EVENT` risk signal exists
- top signals ranked to produce `PredictionOutput.reasons`

## Downstream output (final consumer contract)

Agents do not emit final decisions directly. They feed the aggregator, which returns `PredictionOutput`.

`PredictionOutput` fields:
- `instrument: str`
- `timestamp: datetime`
- `final_decision: str` (`CALL`, `PUT`, `NO_POSITION`)
- `confidence: float`
- `regime: str` (from TA regime detection)
- `reasons: list[str]`
- `component_signals: list[Signal]` (includes TA + NEWS + EVENT signals)

## Implementation guidelines for team members

- Keep agent interfaces stable (`get_upcoming_events`, `fetch_news`, `score`).
- Avoid network dependency in unit tests; inject/test with local samples.
- Emit structured outputs even when data is sparse (empty lists are valid).
- Prefer deterministic fallbacks over exceptions in production path.
- Put cross-source weighting/gating in the aggregator, not inside individual source agents.

