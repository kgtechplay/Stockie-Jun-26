# Requirements: Underlying Prediction Enhancements — Views, Scoring, and Option-Ready Output

## 1. Purpose

Enhance the existing underlying prediction module so that it no longer outputs only a raw `CALL` / `PUT` / `NO_POSITION` prediction. The module should generate a richer, explainable `UnderlyingView` object that captures:

- Directional view
- Regime context
- Strategy-level evidence
- Confidence and strength score
- Expected move estimate
- Risk/volatility context
- Explainable reasons
- Option-selection readiness

This document is intended for Codex implementation.

---

## 2. Current State

The current underlying prediction layer is structured around three core files:

```text
underlying_prediction/
├── features.py      # calculates technical indicators/features
├── regime.py        # detects regime such as TREND_UP / TREND_DOWN / RANGE / CHOPPY
├── strategies.py    # applies strategy rules and returns CALL / PUT / NO_POSITION
```

This is directionally correct and should be retained.

However, the current output is too thin for downstream option selection because option selection requires more context than simply `CALL`, `PUT`, or `NO_POSITION`.

For example, these two signals should not be treated equally:

```text
CALL with weak RSI-only evidence in a choppy regime
CALL with strong trend-up breakout, sector confirmation, volume confirmation, and strong relative strength
```

Both may currently look like `CALL`, but they should result in different option-selection behavior.

---

## 3. Target Architecture

Enhance the underlying prediction folder as follows:

```text
underlying_prediction/
│
├── __init__.py
├── features.py
├── regime.py
├── strategies.py
│
├── schema.py                 # NEW: shared dataclasses/enums
├── scoring.py                # NEW: score strategy outputs and final underlying view
├── view.py                   # NEW: construct final UnderlyingView
├── aggregator.py             # NEW: combine multiple strategy outputs into one final view
├── expected_move.py          # NEW: estimate expected move using ATR/volatility/regime
├── explanation.py            # NEW: build human-readable reasons
└── underlying_prediction_common.py
```

### Responsibility Split

| File | Responsibility |
|---|---|
| `features.py` | Calculate raw technical features from OHLCV and sector/index data |
| `regime.py` | Detect stock, sector, and benchmark regime |
| `strategies.py` | Produce raw strategy-level signals |
| `schema.py` | Define structured inputs/outputs/enums |
| `scoring.py` | Score stock technicals, regime alignment, sector confirmation, relative strength, volume, volatility, and risk quality |
| `aggregator.py` | Combine multiple strategy signals into one final directional view |
| `expected_move.py` | Estimate expected move over the intended holding period |
| `explanation.py` | Build reason strings for audit/debug/UI |
| `view.py` | Create final `UnderlyingView` object consumed by option selection or stock execution |

---

## 4. Design Principle

Do not directly pass this to option selection:

```text
CALL / PUT / NO_POSITION
```

Instead pass this:

```text
UnderlyingView
```

The `UnderlyingView` must contain enough information for the option-selection layer to decide whether to use:

- `LONG_CALL`
- `LONG_PUT`
- `BULL_CALL_SPREAD`
- `BEAR_PUT_SPREAD`
- `NO_TRADE`

The option-selection layer should not need to re-evaluate stock technicals.

---

## 5. Required Schema

Create `underlying_prediction/schema.py`.

### 5.1 Enums / Type Literals

```python
from typing import Literal

RawSignal = Literal["CALL", "PUT", "NO_POSITION"]
Direction = Literal["BULLISH", "BEARISH", "NEUTRAL"]
Regime = Literal["TREND_UP", "TREND_DOWN", "RANGE", "CHOPPY", "UNKNOWN"]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]
SetupType = Literal[
    "TREND_UP_PULLBACK_LONG",
    "TREND_UP_BREAKOUT_LONG",
    "TREND_DOWN_RALLY_SHORT",
    "TREND_DOWN_BREAKDOWN_SHORT",
    "RANGE_LOWER_BAND_LONG",
    "RANGE_UPPER_BAND_SHORT",
    "NO_SETUP",
]
```

---

### 5.2 Feature Snapshot

Create a normalized feature snapshot object so scoring and views do not depend on raw pandas row structures.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class UnderlyingFeatureSnapshot:
    symbol: str
    trade_date: str

    close: float | None
    volume: float | None

    ma10: float | None
    ma20: float | None
    ma50: float | None
    ma90: float | None

    ma20_slope: float | None
    ma50_slope: float | None

    rsi14: float | None
    atr14: float | None

    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    bb_width: float | None

    ret_5d: float | None
    ret_20d: float | None
    ret_60d: float | None
    volatility_20d: float | None

    volume_avg_20d: float | None
    volume_ratio: float | None

    trend_efficiency: float | None
    range_position: float | None

    relative_strength_vs_sector: float | None
    relative_strength_vs_benchmark: float | None

    distance_from_52w_high_pct: float | None = None
    distance_from_52w_low_pct: float | None = None
```

---

### 5.3 Regime Snapshot

```python
@dataclass(frozen=True)
class RegimeSnapshot:
    stock_regime: Regime
    sector_regime: Regime | None
    benchmark_regime: Regime | None

    stock_regime_confidence: float | None
    sector_regime_confidence: float | None
    benchmark_regime_confidence: float | None

    regime_reasons: list[str]
```

---

### 5.4 Strategy Signal

Each strategy in `strategies.py` should emit a structured strategy-level signal.

```python
@dataclass(frozen=True)
class StrategySignal:
    strategy_name: str
    raw_signal: RawSignal
    direction: Direction
    setup_type: SetupType

    score: float              # 0 to 100
    confidence: Confidence

    expected_holding_days: int
    expected_move_pct: float | None
    expected_move_abs: float | None

    stop_loss_pct: float | None
    target_pct: float | None
    reward_risk: float | None

    reasons: list[str]
    warnings: list[str]
```

---

### 5.5 Final Underlying View

This is the main object required by downstream modules.

```python
@dataclass(frozen=True)
class UnderlyingView:
    symbol: str
    trade_date: str

    raw_signal: RawSignal
    direction: Direction

    stock_regime: Regime
    sector_regime: Regime | None
    benchmark_regime: Regime | None

    primary_strategy: str | None
    setup_type: SetupType

    strength_score: float       # 0 to 100
    confidence: Confidence

    expected_move_pct: float | None
    expected_move_abs: float | None
    expected_holding_days: int

    atr14: float | None
    volatility_20d: float | None
    volume_ratio: float | None
    relative_strength_vs_sector: float | None
    relative_strength_vs_benchmark: float | None

    stock_technical_score: float
    sector_confirmation_score: float
    benchmark_confirmation_score: float
    relative_strength_score: float
    volume_confirmation_score: float
    risk_quality_score: float
    regime_quality_score: float

    strategy_signals: list[StrategySignal]

    reasons: list[str]
    warnings: list[str]

    is_option_eligible: bool
    option_bias: str            # BULLISH_STRONG / BULLISH_MODERATE / BEARISH_STRONG / BEARISH_MODERATE / NEUTRAL
```

---

## 6. Scoring Requirements

Create `underlying_prediction/scoring.py`.

The goal is to convert raw features and strategy outputs into an explainable `strength_score` out of 100.

### 6.1 Final Score Formula

Use the following initial scoring model:

```text
final_strength_score =
    stock_technical_score
  + sector_confirmation_score
  + benchmark_confirmation_score
  + relative_strength_score
  + volume_confirmation_score
  + risk_quality_score
  + regime_quality_score
  - penalty_score
```

Suggested maximums:

| Component | Max Points |
|---|---:|
| Stock technical score | 25 |
| Sector confirmation score | 15 |
| Benchmark confirmation score | 10 |
| Relative strength score | 15 |
| Volume confirmation score | 10 |
| Risk quality score | 10 |
| Regime quality score | 15 |
| Total before penalties | 100 |

---

### 6.2 Stock Technical Score

This score measures whether the stock itself has a tradable technical structure.

For bullish setup:

| Condition | Points |
|---|---:|
| `close > ma20` | +5 |
| `ma20 > ma50` | +5 |
| `ma20_slope > 0` | +5 |
| `ma50_slope > 0` | +5 |
| `rsi14 between 45 and 70` | +5 |

For bearish setup:

| Condition | Points |
|---|---:|
| `close < ma20` | +5 |
| `ma20 < ma50` | +5 |
| `ma20_slope < 0` | +5 |
| `ma50_slope < 0` | +5 |
| `rsi14 between 30 and 55` | +5 |

For range setup:

| Condition | Points |
|---|---:|
| Price near range boundary | +5 |
| RSI confirms mean-reversion zone | +5 |
| Bollinger position supports setup | +5 |
| ATR not expanding aggressively | +5 |
| Range efficiency acceptable | +5 |

---

### 6.3 Sector Confirmation Score

| Condition | Bullish Points | Bearish Points |
|---|---:|---:|
| Sector regime aligns with signal | +10 | +10 |
| Sector relative return supports signal | +5 | +5 |
| Sector regime conflicts with signal | -10 | -10 |

Examples:

```text
Bullish stock + sector TREND_UP = positive
Bullish stock + sector TREND_DOWN = penalty
Bearish stock + sector TREND_DOWN = positive
Bearish stock + sector TREND_UP = penalty
```

---

### 6.4 Benchmark Confirmation Score

Use benchmark index such as `NIFTY 200`, `NIFTY 50`, or whichever broad index is mapped to the stock.

| Condition | Points |
|---|---:|
| Benchmark supports direction | +7 |
| Benchmark neutral/range | +3 |
| Benchmark opposes direction | -7 |
| Benchmark choppy | -5 |

---

### 6.5 Relative Strength Score

For bullish signals:

| Condition | Points |
|---|---:|
| `relative_strength_vs_sector > 0` | +7 |
| `relative_strength_vs_benchmark > 0` | +5 |
| `ret_20d > 0` | +3 |

For bearish signals:

| Condition | Points |
|---|---:|
| `relative_strength_vs_sector < 0` | +7 |
| `relative_strength_vs_benchmark < 0` | +5 |
| `ret_20d < 0` | +3 |

---

### 6.6 Volume Confirmation Score

| Volume Ratio | Points |
|---|---:|
| `>= 1.5` | +10 |
| `1.1 to 1.5` | +7 |
| `0.8 to 1.1` | +4 |
| `< 0.8` | 0 or penalty if breakout setup |

For breakout setups, low volume should add warning:

```text
Breakout signal has weak volume confirmation
```

---

### 6.7 Risk Quality Score

Use ATR and volatility to judge whether the signal is tradable.

| Condition | Points |
|---|---:|
| ATR stop distance is reasonable | +4 |
| Volatility is not unusually high | +3 |
| Reward/risk >= 1.5 | +3 |

Add penalties for:

```text
ATR too high
volatility spike
expected move too small versus stop distance
reward/risk below threshold
```

---

### 6.8 Regime Quality Score

| Regime Context | Points |
|---|---:|
| Stock and sector trend aligned | +15 |
| Stock trend aligned, sector range | +10 |
| Range setup in range regime | +10 |
| Stock regime choppy | -15 |
| Sector regime choppy | -8 |
| Benchmark regime choppy | -5 |

---

## 7. Penalties

Create a penalty system rather than hardcoding everything into the positive score.

Suggested penalties:

| Penalty Condition | Penalty |
|---|---:|
| Stock regime = `CHOPPY` | -20 |
| Sector conflicts strongly | -15 |
| Benchmark conflicts strongly | -10 |
| Volume extremely weak for breakout | -10 |
| ATR/volatility too high | -10 |
| Multiple strategies conflict | -10 |
| Missing critical feature values | -5 to -20 |

The final score must be clipped between 0 and 100.

```python
final_score = max(0, min(100, raw_score))
```

---

## 8. Confidence Mapping

Map final score to confidence:

| Score | Confidence | Action |
|---:|---|---|
| `>= 80` | `HIGH` | Strong opportunity |
| `65 to 79.99` | `MEDIUM` | Watchlist / moderate opportunity |
| `50 to 64.99` | `LOW` | Weak opportunity |
| `< 50` | `LOW` | No trade |

Raw signal mapping:

```text
score >= 65 and direction BULLISH → CALL
score >= 65 and direction BEARISH → PUT
otherwise → NO_POSITION
```

---

## 9. Option Bias Mapping

The underlying module should produce an `option_bias` field so option selection can consume it directly.

| Underlying Direction | Score | Option Bias |
|---|---:|---|
| BULLISH | `>= 80` | `BULLISH_STRONG` |
| BULLISH | `65 to 79.99` | `BULLISH_MODERATE` |
| BEARISH | `>= 80` | `BEARISH_STRONG` |
| BEARISH | `65 to 79.99` | `BEARISH_MODERATE` |
| Any | `< 65` | `NEUTRAL` |
| NO_POSITION | Any | `NEUTRAL` |

---

## 10. Expected Move Requirements

Create `underlying_prediction/expected_move.py`.

The underlying view must include an estimated expected move because option selection needs to know whether the expected underlying move is large enough to overcome theta/spread/IV risk.

### 10.1 Expected Move Inputs

Use:

```text
ATR14
volatility_20d
regime
setup_type
historical backtest stats, if available later
```

### 10.2 V1 Formula

Use ATR-based estimate first:

```text
expected_move_abs = atr14 * regime_multiplier
expected_move_pct = expected_move_abs / close
```

Suggested regime multipliers:

| Setup / Regime | Multiplier |
|---|---:|
| Trend breakout | 1.25 |
| Trend pullback | 1.00 |
| Range mean reversion | 0.75 |
| Choppy | 0.00 |

For bearish direction, expected move can remain positive as magnitude; direction is handled separately.

### 10.3 Expected Holding Period

Use defaults:

| Setup Type | Holding Days |
|---|---:|
| Trend breakout | 2 to 5 |
| Trend pullback | 2 to 5 |
| Range mean reversion | 1 to 3 |
| Choppy / no setup | 0 |

For V1, set:

```text
Trend setups → 3 days
Range setups → 2 days
No setup → 0 days
```

---

## 11. Strategy Aggregation Requirements

Create `underlying_prediction/aggregator.py`.

Current strategies may independently return `CALL`, `PUT`, or `NO_POSITION`. Aggregator must combine them into one final view.

### 11.1 Aggregation Rules

1. Collect all `StrategySignal` outputs.
2. Remove signals with score below 50 unless they are useful as warnings.
3. Count bullish, bearish, and no-trade signals.
4. If strong conflict exists, reduce score or return `NO_POSITION`.
5. Prefer regime-compatible strategy over regime-incompatible strategy.
6. Select `primary_strategy` as the highest-scoring aligned strategy.

### 11.2 Conflict Examples

| Situation | Result |
|---|---|
| One high-score bullish trend signal, weak mean-reversion no-trade | Keep bullish |
| Bullish and bearish both high score | `NO_POSITION` or severe penalty |
| Range strategy bullish but stock regime choppy | Penalize / no trade |
| Trend-up breakout and pullback both bullish | Aggregate positively |

### 11.3 Weighted Direction Score

Suggested logic:

```text
bullish_score = sum(score of CALL strategy signals)
bearish_score = sum(score of PUT strategy signals)
neutral_score = sum(score of NO_POSITION signals)
```

Decision:

```text
if bullish_score >= bearish_score * 1.25 and bullish_score >= threshold:
    direction = BULLISH
elif bearish_score >= bullish_score * 1.25 and bearish_score >= threshold:
    direction = BEARISH
else:
    direction = NEUTRAL
```

---

## 12. Explanation Requirements

Create `underlying_prediction/explanation.py`.

Every `UnderlyingView` must have reasons and warnings.

### 12.1 Reason Examples

Bullish trend:

```text
Stock is in TREND_UP regime with close above MA20 and MA50
Sector regime supports bullish setup
Stock is outperforming sector over 20 days
RSI is in bullish momentum zone
Volume ratio confirms participation
```

Bearish trend:

```text
Stock is in TREND_DOWN regime with close below MA20 and MA50
Sector regime supports bearish setup
Stock is underperforming sector over 20 days
RSI is in bearish momentum zone
```

No trade:

```text
Stock regime is CHOPPY
Strategy signals are conflicting
Volume confirmation is weak
Expected move is too small versus ATR risk
```

### 12.2 Warning Examples

```text
Sector trend conflicts with stock signal
Benchmark regime is choppy
Breakout signal has weak volume confirmation
ATR is elevated, stop distance may be wide
Missing relative strength versus sector
```

---

## 13. Data and DB Requirements

If the system stores outputs, add or update the following table.

### 13.1 New Table: `UnderlyingViewDaily`

```sql
CREATE TABLE dbo.UnderlyingViewDaily (
    view_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    trade_date DATE NOT NULL,

    raw_signal VARCHAR(30) NOT NULL,
    direction VARCHAR(30) NOT NULL,

    stock_regime VARCHAR(30) NULL,
    sector_regime VARCHAR(30) NULL,
    benchmark_regime VARCHAR(30) NULL,

    primary_strategy VARCHAR(100) NULL,
    setup_type VARCHAR(100) NULL,

    strength_score DECIMAL(10,4) NULL,
    confidence VARCHAR(20) NULL,

    expected_move_pct DECIMAL(10,6) NULL,
    expected_move_abs DECIMAL(18,4) NULL,
    expected_holding_days INT NULL,

    atr14 DECIMAL(18,4) NULL,
    volatility_20d DECIMAL(10,6) NULL,
    volume_ratio DECIMAL(10,4) NULL,
    relative_strength_vs_sector DECIMAL(10,6) NULL,
    relative_strength_vs_benchmark DECIMAL(10,6) NULL,

    stock_technical_score DECIMAL(10,4) NULL,
    sector_confirmation_score DECIMAL(10,4) NULL,
    benchmark_confirmation_score DECIMAL(10,4) NULL,
    relative_strength_score DECIMAL(10,4) NULL,
    volume_confirmation_score DECIMAL(10,4) NULL,
    risk_quality_score DECIMAL(10,4) NULL,
    regime_quality_score DECIMAL(10,4) NULL,

    option_bias VARCHAR(50) NULL,
    is_option_eligible BIT NOT NULL DEFAULT 0,

    reasons_json NVARCHAR(MAX) NULL,
    warnings_json NVARCHAR(MAX) NULL,
    strategy_signals_json NVARCHAR(MAX) NULL,

    created_at DATETIME2 DEFAULT SYSUTCDATETIME(),

    CONSTRAINT UQ_UnderlyingViewDaily UNIQUE (symbol, trade_date)
);
```

### 13.2 Storage Notes

- Store raw strategy signals as JSON for auditability.
- Do not delete historical views when logic changes.
- Add a `model_version` or `ruleset_version` column if different scoring versions will be compared.

Recommended addition:

```sql
ALTER TABLE dbo.UnderlyingViewDaily
ADD ruleset_version VARCHAR(50) NULL;
```

---

## 14. Public API / Function Requirements

Create a single clean public function.

```python
def build_underlying_view(
    symbol: str,
    trade_date: str,
    stock_features: UnderlyingFeatureSnapshot,
    regime_snapshot: RegimeSnapshot,
    strategy_signals: list[StrategySignal],
    ruleset_version: str = "v1",
) -> UnderlyingView:
    ...
```

The existing prediction runner should call this after features, regime, and raw strategies are calculated.

---

## 15. Integration Flow

Target daily flow:

```text
1. Load daily candles for stock, sector index, benchmark index
2. Calculate features using features.py
3. Detect regimes using regime.py
4. Run strategies using strategies.py
5. Convert raw strategy outputs into StrategySignal objects
6. Score feature/regime/strategy quality using scoring.py
7. Aggregate strategy signals using aggregator.py
8. Estimate expected move using expected_move.py
9. Generate final UnderlyingView using view.py
10. Save UnderlyingViewDaily
11. Pass UnderlyingView to optionselection module, if option trading is enabled
```

---

## 16. Option-Selection Readiness

The final `UnderlyingView` must provide these fields for option selection:

```text
raw_signal
direction
strength_score
confidence
stock_regime
sector_regime
benchmark_regime
setup_type
expected_move_pct
expected_move_abs
expected_holding_days
atr14
volatility_20d
volume_ratio
relative_strength_vs_sector
option_bias
is_option_eligible
reasons
warnings
```

Option selection must not inspect raw stock candles directly.

---

## 17. Option Eligibility Logic

Set `is_option_eligible = True` only when:

```text
raw_signal IN ('CALL', 'PUT')
strength_score >= 65
confidence IN ('MEDIUM', 'HIGH')
expected_move_pct is not null and expected_move_pct > 0
stock_regime != CHOPPY
setup_type != NO_SETUP
```

Set `is_option_eligible = False` if:

```text
raw_signal = NO_POSITION
strength_score < 65
stock_regime = CHOPPY
strategy signals conflict strongly
expected move is missing or zero
critical features are missing
```

---

## 18. Backtesting Requirements

Backtest should persist the final `UnderlyingView`, not only raw strategy output.

For each historical date:

```text
1. Use only candles available up to that date
2. Generate features/regime/strategy signals
3. Generate UnderlyingView
4. Enter next day open if signal is eligible
5. Evaluate outcome over expected holding period
6. Store performance by:
   - setup_type
   - primary_strategy
   - confidence
   - strength_score bucket
   - stock_regime
   - sector_regime
   - option_bias
```

Performance reports should include:

```text
accuracy
precision
recall
average return
median return
max drawdown
win/loss ratio
performance by score bucket
performance by setup type
performance by regime
```

---

## 19. Unit Test Requirements

Create tests for:

### 19.1 Scoring

- Bullish trend-up features produce high stock technical score.
- Bearish trend-down features produce high bearish technical score.
- Choppy regime applies penalty.
- Sector conflict reduces score.
- Volume confirmation increases score.
- Missing values do not crash the scorer.

### 19.2 Aggregation

- Multiple bullish signals aggregate to bullish view.
- Multiple bearish signals aggregate to bearish view.
- Strong bullish/bearish conflict returns neutral or no position.
- Highest scoring aligned strategy becomes `primary_strategy`.

### 19.3 Expected Move

- Trend breakout uses higher ATR multiplier.
- Range setup uses lower ATR multiplier.
- Choppy/no setup returns zero or null expected move.

### 19.4 View Construction

- Score >= 80 bullish becomes `BULLISH_STRONG`.
- Score 65–79 bullish becomes `BULLISH_MODERATE`.
- Score below 65 becomes `NEUTRAL` and `NO_POSITION`.
- Option eligibility is false for choppy regime.
- Reasons and warnings are populated.

---

## 20. Acceptance Criteria

Implementation is complete when:

1. Existing `features.py`, `regime.py`, and `strategies.py` continue to work.
2. New `schema.py`, `scoring.py`, `aggregator.py`, `expected_move.py`, `explanation.py`, and `view.py` are added.
3. A final `UnderlyingView` is generated for every stock/date prediction.
4. `UnderlyingView` includes direction, strength score, confidence, expected move, reasons, warnings, and option bias.
5. `CALL` / `PUT` / `NO_POSITION` is still available for backward compatibility.
6. The option-selection module can consume `UnderlyingView` without recalculating stock indicators.
7. Unit tests cover scoring, aggregation, expected move, and view construction.
8. Backtesting can group results by setup type, score bucket, confidence, and regime.

---

## 21. V1 Implementation Scope

Implement only these setup types in V1:

```text
TREND_UP_PULLBACK_LONG
TREND_UP_BREAKOUT_LONG
TREND_DOWN_RALLY_SHORT
TREND_DOWN_BREAKDOWN_SHORT
NO_SETUP
```

Optional V1.1:

```text
RANGE_LOWER_BAND_LONG
RANGE_UPPER_BAND_SHORT
```

Keep choppy as:

```text
NO_POSITION
```

---

## 22. Non-Goals for This Requirement

Do not implement these in this enhancement:

```text
Option pricing
Option Greeks calculation
Option strategy construction
Order placement
Broker execution
Intraday entry timing
Machine learning model training
News/event signal integration
```

This requirement is only for improving the underlying prediction output so that it becomes score-based, explainable, and option-selection ready.

---

## 23. Final Desired Mental Model

Before enhancement:

```text
features + regime + strategy → CALL / PUT / NO_POSITION
```

After enhancement:

```text
features
+ regime
+ strategy signals
+ scoring
+ aggregation
+ expected move
+ explanation
= UnderlyingView
```

The `UnderlyingView` is the contract between the underlying prediction engine and all downstream modules.

