# Technical Analysis Prediction Workflow

This document describes the high-level end-to-end workflow for the `src/technical_analysis/prediction` codebase.

The prediction package is responsible for converting an underlying instrument's historical OHLCV window into an explainable, option-ready `UnderlyingView`. It preserves the legacy `CALL` / `PUT` / `NO_POSITION` output, but the preferred downstream contract is now the richer view object.

## Purpose

The prediction layer answers this question:

```text
Given recent underlying price/volume behavior, what is the directional view, how strong is it, why was it produced, and is it ready for option selection?
```

The output should be useful for:

- technical-analysis dashboards
- historical prediction files
- backtests
- option-selection handoff
- future persistence in `dbo.UnderlyingViewDaily`

The option-selection layer should consume `UnderlyingView` rather than recalculating stock technicals.

## Package Layout

```text
src/technical_analysis/prediction/
|
|-- __init__.py
|-- underlying_prediction_common.py
|-- underlying_registry.py
|
|-- features.py
|-- regime.py
|-- strategies.py
|
|-- schema.py
|-- snapshot.py
|-- aggregator.py
|-- scoring.py
|-- expected_move.py
|-- explanation.py
|-- view.py
```

### Core Responsibilities

| File | Responsibility |
|---|---|
| `features.py` | Computes raw technical indicators from OHLCV windows. |
| `regime.py` | Classifies the underlying into `TREND_UP`, `TREND_DOWN`, `RANGE`, `CHOPPY`, or `UNKNOWN`. |
| `strategies.py` | Contains built-in rule strategies that still emit raw `CALL`, `PUT`, or `NO_POSITION`. |
| `schema.py` | Defines the dataclass contracts used by the enhanced prediction pipeline. |
| `snapshot.py` | Converts raw feature dictionaries and regime strings into normalized snapshots. |
| `aggregator.py` | Converts raw strategy predictions into `StrategySignal` objects and aggregates strategy direction. |
| `scoring.py` | Scores technical quality, confirmation, relative strength, volume, risk, regime quality, and penalties. |
| `expected_move.py` | Estimates expected underlying move and holding period from ATR, regime, and setup type. |
| `explanation.py` | Builds human-readable reasons for the final view. |
| `view.py` | Builds the final `UnderlyingView` object. |
| `underlying_registry.py` | Loads built-in and custom underlying prediction strategies. |
| `underlying_prediction_common.py` | Public re-export surface for callers. |

## Main Contracts

The enhanced prediction workflow is centered on these objects from `schema.py`.

### `UnderlyingFeatureSnapshot`

A normalized feature snapshot for one symbol/date. It contains:

- latest close and volume
- moving averages
- MA slopes
- RSI
- ATR
- Bollinger bands
- returns
- volatility
- volume ratio
- trend efficiency
- range position
- relative strength versus sector and benchmark

This keeps downstream scoring code independent from raw pandas rows.

### `RegimeSnapshot`

Captures stock, sector, and benchmark regime context:

```text
stock_regime
sector_regime
benchmark_regime
regime confidence fields
regime reasons
```

Today, stock regime is produced from local price history. Sector and benchmark regime are optional and can be supplied by future orchestration.

### `StrategySignal`

Represents one strategy's structured opinion:

```text
strategy_name
raw_signal
direction
setup_type
score
confidence
expected move
holding days
reasons
warnings
```

This is the bridge between legacy raw strategies and the enhanced scoring/view layer.

### `UnderlyingView`

The final contract for downstream modules:

```text
symbol
trade_date
raw_signal
direction
regime context
primary_strategy
setup_type
strength_score
confidence
expected_move_pct
expected_move_abs
expected_holding_days
risk/volume/relative-strength context
score components
strategy_signals
reasons
warnings
is_option_eligible
option_bias
```

This object is the handoff point to option selection.

## End-To-End Workflow

At a high level:

```text
OHLCV window
  -> feature calculation
  -> regime detection
  -> raw strategy predictions
  -> feature/regime snapshots
  -> structured strategy signals
  -> strategy aggregation
  -> score components and penalties
  -> expected move
  -> reasons/warnings
  -> UnderlyingView
```

## Detailed Flow

### 1. Load Historical Window

Callers usually come through `PredictionService`.

Typical flow:

```text
PredictionService
  -> get_underlying_window()
  -> fetch_index_daily(..., join_activity=True)
  -> last DEFAULT_LOOKBACK_DAYS rows
```

The default lookback is:

```text
DEFAULT_LOOKBACK_DAYS = 90
```

The window is expected to contain columns such as:

```text
trade_date
close_price
high_price
low_price
volume
```

### 2. Compute Features

`features.py` computes the technical feature dictionary using `compute_underlying_features()`.

Key feature families:

- moving averages: `ma10`, `ma20`, `ma50`, `ma90`
- momentum: `rsi14`, `ret_5d`, `ret_20d`, `ret_60d`
- volatility: `atr14`, `volatility_20d`
- Bollinger bands: `bb_upper`, `bb_middle`, `bb_lower`, `bb_width`
- volume: `volume_ratio`
- trend/range behavior: `trend_efficiency_60d`, `range_position_20d`
- relative strength: `relative_strength_vs_sector`

The exported `FEATURE_COLUMNS` list controls the stable feature output order used by CSV/service flows.

### 3. Detect Regime

`regime.py` classifies the stock using moving-average structure, returns, trend efficiency, volatility, and crossover/range behavior.

Possible values:

```text
TREND_UP
TREND_DOWN
RANGE
CHOPPY
UNKNOWN
```

Examples:

- strong close above MA20 and MA50 with positive slopes and good trend efficiency -> `TREND_UP`
- strong close below MA20 and MA50 with negative slopes and good trend efficiency -> `TREND_DOWN`
- flat or small-return behavior -> `RANGE`
- high volatility, low trend efficiency, frequent MA crossovers -> `CHOPPY`

### 4. Run Raw Strategies

`strategies.py` contains built-in strategy functions. These remain intentionally simple and backward compatible.

Current built-in strategies include:

- `BollingerMeanReversion`
- `MaTrend_001`
- `RsiMeanReversion_6535`
- `rangeBollingerMeanReversion`
- `rangeRsiMeanReversion_6535`
- `trendDownRangeBreakout`
- `trendUpRangeBreakout`
- `choppy`
- `unknown`

Each raw strategy returns:

```text
CALL
PUT
NO_POSITION
```

The registry in `underlying_registry.py` loads these built-ins and can also load custom modules named:

```text
underlying_prediction_*.py
```

Custom modules must expose:

```python
STRATEGY_NAME = "..."

def predict(window):
    ...
```

### 5. Build Snapshots

`snapshot.py` converts pandas/raw values into stable dataclasses:

```text
build_feature_snapshot()
build_regime_snapshot()
```

This is where the pipeline normalizes:

- symbol
- trade date
- latest close/volume
- computed feature dict
- optional sector and benchmark context
- regime strings

Missing values are represented as `None`, and scoring is expected to degrade gracefully.

### 6. Convert Raw Strategies To `StrategySignal`

`aggregator.py` converts each raw strategy prediction into a structured `StrategySignal`.

Important conversions:

```text
CALL        -> BULLISH
PUT         -> BEARISH
NO_POSITION -> NEUTRAL
```

The setup type is inferred from:

- raw signal
- stock regime
- strategy name
- breakout hints from Bollinger-band position

Supported setup types:

```text
TREND_UP_PULLBACK_LONG
TREND_UP_BREAKOUT_LONG
TREND_DOWN_RALLY_SHORT
TREND_DOWN_BREAKDOWN_SHORT
RANGE_LOWER_BAND_LONG
RANGE_UPPER_BAND_SHORT
NO_SETUP
```

Each `StrategySignal` receives:

- strategy-level score
- confidence
- expected move
- expected holding days
- reasons
- warnings

### 7. Aggregate Strategy Direction

`aggregate_strategy_signals()` evaluates the structured strategy signals.

High-level rules:

- ignore weak directional signals below score 50 for direction aggregation
- sum bullish scores
- sum bearish scores
- require one side to dominate the other by roughly 1.25x
- mark strong high-score two-sided disagreement as conflict
- select the highest-scoring aligned strategy as `primary_strategy`

This replaces simple majority vote for the enhanced view path.

The old majority-vote flow still exists for backward compatibility in `src/technical_analysis/aggregator/underlying_aggregator.py`.

### 8. Score The View

`scoring.py` creates a `ScoreBreakdown`.

Score components:

| Component | Max Intent |
|---|---:|
| Stock technical score | 25 |
| Sector confirmation score | 15 |
| Benchmark confirmation score | 10 |
| Relative strength score | 15 |
| Volume confirmation score | 10 |
| Risk quality score | 10 |
| Regime quality score | 15 |

Penalties are then applied for issues such as:

- choppy stock regime
- choppy sector or benchmark regime
- weak breakout volume
- missing critical features
- elevated volatility/ATR warnings

Final score is clipped:

```text
0 <= strength_score <= 100
```

Confidence mapping:

```text
score >= 80      -> HIGH
65 <= score < 80 -> MEDIUM
score < 65       -> LOW
```

Raw signal mapping:

```text
score >= 65 and direction BULLISH -> CALL
score >= 65 and direction BEARISH -> PUT
otherwise                         -> NO_POSITION
```

### 9. Estimate Expected Move

`expected_move.py` estimates the expected underlying move from ATR, regime, and setup type.

Core formula:

```text
expected_move_abs = atr14 * regime_multiplier
expected_move_pct = expected_move_abs / close
```

Multiplier intent:

| Setup / Regime | Multiplier |
|---|---:|
| Trend breakout | 1.25 |
| Trend pullback | 1.00 |
| Range mean reversion | 0.75 |
| Choppy / no setup | 0.00 |

Holding period defaults:

| Setup | Holding Days |
|---|---:|
| Trend setup | 3 |
| Range setup | 2 |
| No setup | 0 |

Expected move is always magnitude. Direction is represented separately by `direction`.

### 10. Generate Reasons And Warnings

`explanation.py` builds concise, human-readable reasons for the final view.

Examples:

```text
Stock is in TREND_UP regime for a bullish setup
Close is above MA20
Primary strategy is MaTrend_001
Sector regime supports the signal
Volume ratio confirms participation
```

Warnings come mostly from scoring and strategy conversion.

Examples:

```text
Missing sector regime confirmation
Missing benchmark regime confirmation
Stock regime is CHOPPY
Breakout signal has weak volume confirmation
Missing critical feature values
```

### 11. Build Final `UnderlyingView`

`view.py` is the final assembly point.

Primary function:

```python
build_underlying_view(
    symbol,
    trade_date,
    stock_features,
    regime_snapshot,
    strategy_signals,
    ruleset_version="v1",
)
```

The final view includes:

- direction
- raw signal
- setup type
- primary strategy
- strength score
- confidence
- expected move
- holding days
- score components
- reasons/warnings
- option bias
- option eligibility flag

### 12. Derive Option Bias

`UnderlyingView.option_bias` is intended for option selection.

Mapping:

```text
BULLISH + score >= 80      -> BULLISH_STRONG
BULLISH + score 65 to 79   -> BULLISH_MODERATE
BEARISH + score >= 80      -> BEARISH_STRONG
BEARISH + score 65 to 79   -> BEARISH_MODERATE
NO_POSITION / weak / neutral -> NEUTRAL
```

Option eligibility is true only when:

```text
raw_signal in CALL/PUT
strength_score >= 65
confidence is MEDIUM or HIGH
expected_move_pct > 0
stock_regime != CHOPPY
setup_type != NO_SETUP
no strong strategy conflict
```

## Public Entry Points

### Legacy Prediction

Use this when a caller still needs the older `PredictionOutput` shape:

```python
from src.technical_analysis.aggregator.underlying_aggregator import run_underlying_prediction
```

Output:

```text
PredictionOutput(
    instrument,
    timestamp,
    final_decision,
    confidence,
    regime,
    reasons,
    component_signals,
)
```

This path still performs a regime-aware majority vote over raw strategy outputs.

### Enhanced Prediction View

Use this for the new option-ready workflow:

```python
from src.technical_analysis.aggregator.underlying_aggregator import run_underlying_view_prediction
```

High-level behavior:

```text
detect stock regime
run raw strategies
build feature snapshot
build regime snapshot
build structured strategy signals
build final UnderlyingView
```

### Prediction Service

`src/services/prediction_service.py` now exposes:

```text
run_prediction()
run_underlying_view()
generate_reference_date_prediction()
generate_consolidated_predictions()
run_reference_date_predictions_for_symbols()
```

Reference and consolidated prediction outputs include the legacy strategy columns plus enhanced fields such as:

```text
underlying_raw_signal
underlying_direction
underlying_strength_score
underlying_confidence
underlying_setup_type
underlying_primary_strategy
underlying_expected_move_pct
underlying_expected_move_abs
underlying_expected_holding_days
underlying_option_bias
underlying_is_option_eligible
```

## Persistence

The enhanced prediction output is designed to be stored in:

```text
dbo.UnderlyingViewDaily
```

Migration:

```text
src/data_manager/db/migrations/003_create_underlying_view_daily.sql
```

The table stores:

- symbol/date
- raw signal and direction
- regime context
- primary strategy/setup type
- strength score/confidence
- expected move/holding days
- score components
- option bias and eligibility
- reasons JSON
- warnings JSON
- strategy signals JSON
- ruleset version

The current code creates the object and exposes fields through service outputs. A repository/upsert function can be added when the daily job is ready to persist the view.

## Backward Compatibility

Backward compatibility is intentionally preserved:

- raw strategies still return `CALL`, `PUT`, or `NO_POSITION`
- existing registry loading still works
- existing strategy-column CSV outputs still work
- legacy majority-vote aggregation still exists
- `underlying_prediction_common.py` re-exports both old and new public APIs

The enhanced workflow sits beside the existing path and should become the preferred contract for option selection.

## Testing

Focused unit tests live in:

```text
tests/test_underlying_prediction_view.py
```

Current coverage includes:

- bullish trend scoring
- bearish trend scoring
- choppy-regime option eligibility blocking
- expected move multiplier behavior
- high-score option-bias mapping
- high-score strategy conflict handling

Run:

```powershell
python -m unittest tests.test_underlying_prediction_view
```

Compile check:

```powershell
python -m compileall src\technical_analysis\prediction src\technical_analysis\aggregator src\services\prediction_service.py tests
```

## Current E2E Mental Model

Before:

```text
features + regime + strategy vote -> CALL / PUT / NO_POSITION
```

Now:

```text
features
+ regime
+ raw strategies
+ normalized snapshots
+ strategy signals
+ scoring
+ aggregation
+ expected move
+ explanation
= UnderlyingView
```

The important design boundary is:

```text
technical_analysis/prediction owns the underlying view.
technical_analysis/optionselection should consume that view.
optionselection should not re-evaluate raw stock technicals.
```

## Known Next Steps

Recommended follow-ups:

1. Add DB upsert support for `UnderlyingViewDaily`.
2. Pass real sector and benchmark windows into `run_underlying_view_prediction()`.
3. Persist `reasons`, `warnings`, and `strategy_signals` as JSON.
4. Update backtests to group results by setup type, strength-score bucket, confidence, regime, and option bias.
5. Make option selection consume `UnderlyingView` directly.
