# Requirements: Option Selection Engine From Underlying View

## 1. Context

The current underlying prediction engine is split into three main layers:

```text
underlying_prediction/
├── features.py      # Calculates technical indicators/features from OHLCV window
├── regime.py        # Detects TREND_UP / TREND_DOWN / RANGE / CHOPPY / UNKNOWN
├── strategies.py    # Returns CALL / PUT / NO_POSITION from strategy rules
```

This structure is directionally correct and should be retained.

The next step is to add a clean `optionselection/` module that consumes the output of the underlying engine and selects the most appropriate option trade structure.

Important design decision:

```text
Do not directly map CALL -> buy call or PUT -> buy put.
```

Instead, the option selection engine must consume a richer `UnderlyingView` object containing direction, score, confidence, regime, expected move, ATR, volatility, setup type and reasons.

The option selection layer should then decide whether to choose:

```text
LONG_CALL
LONG_PUT
BULL_CALL_SPREAD
BEAR_PUT_SPREAD
NO_TRADE
```

For V1, avoid naked short options and complex multi-leg strategies.

---

## 2. Important Constraint

Do **not** create a separate `pricing.py` module for Black-Scholes calculation in this phase.

Reason:

The system already stores IV and Greeks in the database table `OptionSnapshotCalc`.

Therefore, the option selection layer should **read existing calculated values** from the database instead of recalculating them.

Expected existing calculated fields from `OptionSnapshotCalc`:

```text
implied_volatility / iv
delta
gamma
theta
vega
rho, optional
calculation_timestamp
snapshot_id / option_snapshot_id
```

If a required IV/Greek value is missing for a contract, that contract should either be downgraded or filtered out depending on strategy requirements.

---

## 3. High-Level Flow

The final daily flow should be:

```text
1. Underlying engine computes stock/index features
2. Underlying engine detects regime
3. Underlying engine runs strategy rules
4. Underlying scoring/view layer creates UnderlyingView
5. Option selection engine receives UnderlyingView
6. Option selection engine loads latest option chain + OptionSnapshotCalc values
7. Option selection engine computes option-selection features
8. Option selection engine derives option bias from UnderlyingView
9. Option selection engine chooses possible option structure
10. Candidate filter removes illiquid/expensive/high-decay contracts
11. Strategy builder creates long-call/long-put/spread candidates
12. Risk module calculates max loss, breakeven, reward/risk, theta exposure
13. Scoring module ranks candidates
14. Selector returns best OptionSelectionResult or NO_TRADE
```

---

## 4. Add Underlying View Layer

Before implementing `optionselection/`, add a thin view/scoring layer to the underlying module.

Suggested folder structure:

```text
underlying_prediction/
├── features.py
├── regime.py
├── strategies.py
├── scoring.py              # NEW: converts raw signals/features into strength score
├── view.py                 # NEW: builds UnderlyingView object
├── underlying_prediction_common.py
```

### 4.1 Required `UnderlyingView` schema

Create this in `underlying_prediction/view.py` or in a shared schema file.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

UnderlyingSignal = Literal["CALL", "PUT", "NO_POSITION"]
UnderlyingDirection = Literal["BULLISH", "BEARISH", "NEUTRAL"]
SignalConfidence = Literal["LOW", "MEDIUM", "HIGH"]

@dataclass(frozen=True)
class UnderlyingView:
    symbol: str
    trade_date: str

    raw_signal: UnderlyingSignal
    direction: UnderlyingDirection

    stock_regime: str
    sector_regime: str | None
    benchmark_regime: str | None

    setup_type: str | None
    strength_score: float              # 0 to 100
    confidence: SignalConfidence

    last_close: float | None
    expected_move_pct: float | None
    expected_move_abs: float | None
    expected_holding_days: int

    atr14: float | None
    volatility_20d: float | None
    volume_ratio: float | None
    relative_strength_vs_sector: float | None
    relative_strength_vs_benchmark: float | None

    reasons: list[str]
```

### 4.2 Underlying score rules

Codex should implement a simple scoring function in `underlying_prediction/scoring.py`.

Suggested scoring components:

| Component | Max Points |
|---|---:|
| Stock regime alignment | 25 |
| Strategy signal strength | 20 |
| Sector regime confirmation | 15 |
| Benchmark regime confirmation | 10 |
| Relative strength | 15 |
| Volume confirmation | 10 |
| Volatility/ATR sanity | 5 |

Total = 100.

Suggested confidence thresholds:

```text
strength_score >= 80 -> HIGH
65 <= strength_score < 80 -> MEDIUM
< 65 -> LOW
```

Expected move fallback logic:

```text
If expected_move_pct is already computed by underlying engine:
    use it
Else if ATR and close are available:
    expected_move_abs = 0.75 * ATR14 for one-day view
    expected_move_pct = expected_move_abs / last_close
Else:
    expected_move_pct = None
```

Expected holding period:

```text
Default V1 = 1 to 3 trading days
Trend breakout = 2 to 5 trading days
Mean reversion = 1 to 2 trading days
```

---

## 5. New Option Selection Folder Structure

Create:

```text
optionselection/
├── __init__.py
├── schema.py
├── repository.py
├── option_features.py
├── underlying_view_strength.py
├── strategy_rules.py
├── candidate_filter.py
├── strategy_builder.py
├── risk.py
├── scoring.py
├── option_selector.py
└── optionselection_common.py
```

### File responsibilities

| File | Responsibility |
|---|---|
| `schema.py` | Dataclasses/enums for option contracts, features, legs, candidates, result |
| `repository.py` | Load latest option chain + IV/Greeks from DB tables |
| `option_features.py` | Compute derived selection features from existing IV/Greeks and quotes |
| `underlying_view_strength.py` | Convert `UnderlyingView` into option bias |
| `strategy_rules.py` | Decide strategy type: long call, long put, spread, no trade |
| `candidate_filter.py` | Filter contracts by delta, DTE, spread, liquidity, theta burn, IV rank |
| `strategy_builder.py` | Build valid single-leg and spread candidates |
| `risk.py` | Calculate max loss, max profit, breakeven, reward/risk, portfolio Greeks |
| `scoring.py` | Score and rank option candidates |
| `option_selector.py` | Main orchestrator |
| `optionselection_common.py` | Re-export public functions/classes |

---

## 6. `schema.py` Requirements

Create all option-selection dataclasses here.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]
OptionSide = Literal["BUY", "SELL"]
OptionStrategyType = Literal[
    "LONG_CALL",
    "LONG_PUT",
    "BULL_CALL_SPREAD",
    "BEAR_PUT_SPREAD",
    "NO_TRADE",
]
OptionBias = Literal[
    "BULLISH_STRONG",
    "BULLISH_MODERATE",
    "BEARISH_STRONG",
    "BEARISH_MODERATE",
    "NEUTRAL",
]
SelectionConfidence = Literal["LOW", "MEDIUM", "HIGH"]

@dataclass(frozen=True)
class OptionContract:
    instrument_token: int | None
    tradingsymbol: str
    underlying: str
    expiry: str
    strike: float
    option_type: OptionType

    last_price: float
    bid: float | None
    ask: float | None
    volume: int | None
    open_interest: int | None

    snapshot_time: str | None = None
    calc_time: str | None = None

    # Values loaded from OptionSnapshotCalc, not recalculated here.
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

@dataclass(frozen=True)
class OptionFeatures:
    tradingsymbol: str
    expiry: str
    strike: float
    option_type: OptionType

    days_to_expiry: int
    moneyness_pct: float | None
    distance_from_spot_pct: float | None

    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None

    spread_pct: float | None
    mid_price: float | None
    liquidity_score: float
    theta_burn_pct_per_day: float | None

    iv_rank_90d: float | None
    iv_percentile_90d: float | None
    iv_vs_atm_pct: float | None
    iv_vs_neighbor_median_pct: float | None

    is_iv_outlier: bool
    is_liquid: bool
    is_tradeable: bool
    rejection_reasons: list[str]

@dataclass(frozen=True)
class OptionLeg:
    side: OptionSide
    contract: OptionContract
    features: OptionFeatures
    quantity: int = 1

@dataclass(frozen=True)
class OptionStrategyCandidate:
    strategy_type: OptionStrategyType
    legs: list[OptionLeg]

    direction: str
    expected_underlying_move_pct: float | None
    expected_underlying_move_abs: float | None
    expected_holding_days: int

    entry_debit_or_credit: float | None
    max_profit: float | None
    max_loss: float | None
    breakeven: float | None
    reward_risk: float | None

    total_delta: float | None
    total_gamma: float | None
    total_theta: float | None
    total_vega: float | None

    score: float
    confidence: SelectionConfidence
    reasons: list[str]
    warnings: list[str]

@dataclass(frozen=True)
class OptionSelectionResult:
    underlying: str
    trade_date: str
    selected_strategy: OptionStrategyCandidate
    option_bias: OptionBias
    no_trade_reason: str | None
    evaluated_candidate_count: int
```

---

## 7. `repository.py` Requirements

This module should fetch option chain data from existing DB tables.

### 7.1 Purpose

Load latest option contracts and latest IV/Greeks for a given underlying and trade date.

### 7.2 Expected source tables

Adapt names to the actual schema if required:

```text
OptionInstrument
OptionSnapshot
OptionSnapshotCalc
```

Expected joins:

```text
OptionInstrument.instrument_token = OptionSnapshot.instrument_token
OptionSnapshot.snapshot_id = OptionSnapshotCalc.snapshot_id
```

If exact keys differ, Codex should adapt to the existing schema.

### 7.3 Required function

```python
def load_option_chain_with_calcs(
    db_client,
    underlying: str,
    as_of_time: str | None = None,
    min_expiry_date: str | None = None,
    max_expiry_date: str | None = None,
) -> list[OptionContract]:
    """
    Return latest option chain rows with IV and Greeks loaded from OptionSnapshotCalc.
    Do not calculate Black-Scholes values here.
    """
```

### 7.4 Query behavior

Rules:

```text
1. Filter by underlying.
2. Include only non-expired contracts.
3. Prefer the latest snapshot available at or before as_of_time.
4. Include both CE and PE.
5. Include bid/ask if available.
6. Include last_price, volume, OI.
7. Include IV, delta, gamma, theta, vega from OptionSnapshotCalc.
8. Exclude rows with last_price <= 0.
```

If bid/ask is missing, use last_price as fallback for mid-price but apply lower liquidity score.

---

## 8. `option_features.py` Requirements

This module must compute selection-level features from `OptionContract`.

Important:

```text
Do not compute IV/Greeks from scratch.
Use values loaded on OptionContract.
```

### 8.1 Required function

```python
def compute_option_features_for_chain(
    contracts: list[OptionContract],
    spot_price: float,
    trade_date: str,
    atm_iv_history_90d: list[float] | None = None,
) -> dict[str, OptionFeatures]:
    """
    Return mapping: tradingsymbol -> OptionFeatures.
    """
```

### 8.2 Derived fields

Implement:

```text
mid_price
spread_pct
moneyness_pct
distance_from_spot_pct
days_to_expiry
theta_burn_pct_per_day
liquidity_score
iv_rank_90d
iv_percentile_90d
iv_vs_atm_pct
iv_vs_neighbor_median_pct
is_iv_outlier
is_liquid
is_tradeable
rejection_reasons
```

### 8.3 Formula definitions

```python
def compute_mid_price(bid, ask, last_price):
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last_price


def compute_spread_pct(bid, ask):
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid


def compute_theta_burn_pct_per_day(theta, premium):
    if theta is None or premium is None or premium <= 0:
        return None
    return abs(theta) / premium


def compute_moneyness_pct(spot, strike):
    return strike / spot - 1
```

For IV rank:

```text
iv_rank_90d = (current_atm_iv - min_90d_atm_iv) / (max_90d_atm_iv - min_90d_atm_iv) * 100
```

For IV percentile:

```text
iv_percentile_90d = percent of past 90 daily ATM IV values below current ATM IV
```

If ATM IV history is unavailable, set both to `None` and do not reject solely because missing.

### 8.4 Liquidity score

Start with score = 100 and subtract penalties:

```text
spread_pct missing                 -20
spread_pct > 5%                    -30
spread_pct > 10%                   -50
volume missing or volume <= 0      -20
open_interest missing or <= 0      -20
last_price <= 0                    reject
iv missing                         -20
delta missing                      -20
```

Set:

```text
is_liquid = liquidity_score >= 60
```

### 8.5 Tradeability rules

Reject contract if:

```text
last_price <= 0
expired contract
days_to_expiry <= 0
spread_pct > 0.10, if spread is available
liquidity_score < 50
iv is None
```

For long options, also reject if:

```text
theta_burn_pct_per_day > 0.12
```

For spreads, allow slightly higher theta burn but lower score.

---

## 9. `underlying_view_strength.py` Requirements

This module converts `UnderlyingView` into option bias.

### 9.1 Required function

```python
def derive_option_bias(view: UnderlyingView) -> OptionBias:
    ...
```

### 9.2 Rules

```text
If raw_signal = NO_POSITION -> NEUTRAL
If direction = NEUTRAL -> NEUTRAL
If strength_score < 65 -> NEUTRAL

If raw_signal = CALL and strength_score >= 80 -> BULLISH_STRONG
If raw_signal = CALL and 65 <= strength_score < 80 -> BULLISH_MODERATE

If raw_signal = PUT and strength_score >= 80 -> BEARISH_STRONG
If raw_signal = PUT and 65 <= strength_score < 80 -> BEARISH_MODERATE
```

Additional downgrade rules:

```text
If stock_regime = CHOPPY -> NEUTRAL
If benchmark_regime = strongly opposite direction -> downgrade one level
If sector_regime = strongly opposite direction -> downgrade one level
If expected_move_pct is None and ATR is None -> downgrade one level
```

Example downgrades:

```text
BULLISH_STRONG -> BULLISH_MODERATE
BULLISH_MODERATE -> NEUTRAL
BEARISH_STRONG -> BEARISH_MODERATE
BEARISH_MODERATE -> NEUTRAL
```

---

## 10. `strategy_rules.py` Requirements

This module chooses the option strategy type from the underlying bias and volatility/liquidity context.

### 10.1 Required function

```python
def choose_option_strategy_type(
    option_bias: OptionBias,
    atm_iv_rank_90d: float | None,
    atm_iv_percentile_90d: float | None,
    expected_move_pct: float | None,
    expected_holding_days: int,
    min_days_to_expiry_available: int | None,
) -> OptionStrategyType:
    ...
```

### 10.2 V1 supported strategies

```text
LONG_CALL
LONG_PUT
BULL_CALL_SPREAD
BEAR_PUT_SPREAD
NO_TRADE
```

### 10.3 Strategy rules

```text
If option_bias = NEUTRAL:
    NO_TRADE

If option_bias = BULLISH_STRONG:
    If IV rank is missing or IV rank <= 70:
        LONG_CALL
    Else:
        BULL_CALL_SPREAD

If option_bias = BULLISH_MODERATE:
    BULL_CALL_SPREAD

If option_bias = BEARISH_STRONG:
    If IV rank is missing or IV rank <= 70:
        LONG_PUT
    Else:
        BEAR_PUT_SPREAD

If option_bias = BEARISH_MODERATE:
    BEAR_PUT_SPREAD
```

### 10.4 Expiry sanity rules

If only very near expiry contracts are available:

```text
If min DTE <= 1 and expected_holding_days >= 2:
    prefer NO_TRADE or only spread if liquidity is excellent
```

Preferred DTE:

```text
For long options: 5 to 21 calendar days
For debit spreads: 7 to 30 calendar days
Avoid contracts with DTE <= 1 in V1 unless explicitly allowed
```

### 10.5 IV behavior rules

```text
High IV rank >= 70:
    avoid naked long premium unless underlying view is very strong
    prefer debit spread

Very high IV rank >= 85:
    allow only spread or NO_TRADE

Low/normal IV rank < 70:
    long option is allowed if theta burn and spread are acceptable
```

---

## 11. `candidate_filter.py` Requirements

This module filters option contracts based on the strategy type.

### 11.1 Required functions

```python
def filter_long_call_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
) -> list[OptionContract]:
    ...


def filter_long_put_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
) -> list[OptionContract]:
    ...


def filter_spread_buy_leg_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
    option_type: str,
) -> list[OptionContract]:
    ...


def filter_spread_sell_leg_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
    option_type: str,
    buy_leg: OptionContract,
) -> list[OptionContract]:
    ...
```

### 11.2 Long call filters

```text
option_type = CE
delta between 0.35 and 0.65
DTE between 5 and 21 preferred
spread_pct <= 5%, if available
liquidity_score >= 60
theta_burn_pct_per_day <= 8%, preferred hard limit 12%
not IV outlier
last_price > 0
```

### 11.3 Long put filters

```text
option_type = PE
absolute delta between 0.35 and 0.65
DTE between 5 and 21 preferred
spread_pct <= 5%, if available
liquidity_score >= 60
theta_burn_pct_per_day <= 8%, preferred hard limit 12%
not IV outlier
last_price > 0
```

### 11.4 Bull call spread filters

Buy leg:

```text
option_type = CE
delta between 0.45 and 0.65
liquidity_score >= 60
not IV outlier
```

Sell leg:

```text
option_type = CE
same expiry as buy leg
strike > buy_leg.strike
delta between 0.20 and 0.40 preferred
liquidity_score >= 50
spread width controlled
```

### 11.5 Bear put spread filters

Buy leg:

```text
option_type = PE
absolute delta between 0.45 and 0.65
liquidity_score >= 60
not IV outlier
```

Sell leg:

```text
option_type = PE
same expiry as buy leg
strike < buy_leg.strike
absolute delta between 0.20 and 0.40 preferred
liquidity_score >= 50
spread width controlled
```

---

## 12. `strategy_builder.py` Requirements

This module builds actual strategy candidates.

### 12.1 Required function

```python
def build_strategy_candidates(
    strategy_type: OptionStrategyType,
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
    underlying_view: UnderlyingView,
    spot_price: float,
) -> list[OptionStrategyCandidate]:
    ...
```

### 12.2 Long call builder

Build one-leg candidates:

```text
BUY 1 CE
```

For each candidate, calculate:

```text
entry_debit = ask if available else last_price
max_loss = entry_debit
breakeven = strike + entry_debit
max_profit = None
reward_risk = estimated target gain / max_loss, if target can be estimated
```

### 12.3 Long put builder

Build one-leg candidates:

```text
BUY 1 PE
```

For each candidate:

```text
entry_debit = ask if available else last_price
max_loss = entry_debit
breakeven = strike - entry_debit
max_profit = strike - entry_debit, approximately
reward_risk = estimated target gain / max_loss, if target can be estimated
```

### 12.4 Bull call spread builder

Build two-leg candidates:

```text
BUY 1 lower-strike CE
SELL 1 higher-strike CE
same expiry
```

Calculations:

```text
net_debit = buy_leg_ask - sell_leg_bid
spread_width = sell_strike - buy_strike
max_loss = net_debit
max_profit = spread_width - net_debit
breakeven = buy_strike + net_debit
reward_risk = max_profit / max_loss
```

Reject if:

```text
net_debit <= 0
max_profit <= 0
reward_risk < 1.0
spread_width too wide relative to spot, e.g. > 5% of spot
```

### 12.5 Bear put spread builder

Build two-leg candidates:

```text
BUY 1 higher-strike PE
SELL 1 lower-strike PE
same expiry
```

Calculations:

```text
net_debit = buy_leg_ask - sell_leg_bid
spread_width = buy_strike - sell_strike
max_loss = net_debit
max_profit = spread_width - net_debit
breakeven = buy_strike - net_debit
reward_risk = max_profit / max_loss
```

Reject if:

```text
net_debit <= 0
max_profit <= 0
reward_risk < 1.0
spread_width too wide relative to spot, e.g. > 5% of spot
```

---

## 13. `risk.py` Requirements

This module calculates risk and aggregate Greeks for a strategy candidate.

### 13.1 Required function

```python
def calculate_strategy_risk(candidate: OptionStrategyCandidate) -> OptionStrategyCandidate:
    ...
```

### 13.2 Aggregate Greeks

For each leg:

```text
BUY leg contribution = + greek * quantity
SELL leg contribution = - greek * quantity
```

Compute:

```text
total_delta
total_gamma
total_theta
total_vega
```

### 13.3 Risk constraints

Reject or heavily penalize candidates where:

```text
max_loss is None or <= 0
reward_risk < 1.0 for spreads
absolute total theta too high relative to premium paid
spread_pct of buy leg too high
total delta is inconsistent with direction
```

Direction sanity:

```text
Bullish strategies should have total_delta > 0
Bearish strategies should have total_delta < 0
```

---

## 14. `scoring.py` Requirements

Score candidates out of 100.

### 14.1 Required function

```python
def score_option_candidate(
    candidate: OptionStrategyCandidate,
    underlying_view: UnderlyingView,
    features: dict[str, OptionFeatures],
) -> OptionStrategyCandidate:
    ...
```

### 14.2 Scoring components

| Component | Max Points |
|---|---:|
| Underlying view strength | 25 |
| Directional Greek alignment | 15 |
| Liquidity and spread | 15 |
| Theta burn acceptability | 15 |
| IV surface/IV rank quality | 10 |
| Reward/risk quality | 15 |
| Expiry fit with holding period | 5 |

Total = 100.

### 14.3 Score logic

Underlying view strength:

```text
Use underlying_view.strength_score scaled to 25 points.
```

Directional Greek alignment:

```text
Bullish: prefer total_delta > 0.30 for long call; > 0.15 for spread
Bearish: prefer total_delta < -0.30 for long put; < -0.15 for spread
```

Liquidity:

```text
Use average liquidity_score across legs.
Penalize if any buy leg spread_pct > 5%.
```

Theta:

```text
For long premium, lower theta burn is better.
For debit spread, theta burn penalty is smaller.
```

IV quality:

```text
Penalize IV outlier.
Penalize buying options when IV rank > 80.
Reward clean IV surface and IV rank between 20 and 70.
```

Reward/risk:

```text
For spreads:
    reward_risk >= 1.5 gets high score
    1.0 to 1.5 gets medium score
    < 1.0 reject

For long options:
    use expected option move from delta/gamma approximation if available
    otherwise keep neutral score
```

Expiry fit:

```text
DTE should be greater than expected_holding_days + 2 calendar days.
Prefer DTE 5-21 for long options, 7-30 for spreads.
```

Confidence:

```text
score >= 80 -> HIGH
65 <= score < 80 -> MEDIUM
< 65 -> LOW
```

Final selector should return `NO_TRADE` if best candidate score < 65.

---

## 15. Expected Option Move Approximation

Even though pricing is not recalculated, use stored Greeks to estimate whether expected move is meaningful.

For each single-leg buy candidate:

```text
estimated_option_change =
    delta * expected_underlying_move_abs
  + 0.5 * gamma * expected_underlying_move_abs^2
  + theta * expected_holding_days
```

If an IV change assumption is available, add:

```text
+ vega * expected_iv_change_points
```

For V1, set expected IV change to `0` unless explicitly provided.

For bullish calls:

```text
expected_underlying_move_abs should be positive
```

For bearish puts:

```text
Use absolute expected move and absolute delta impact carefully.
Alternatively calculate using signed delta:
    underlying_move_abs is negative for bearish view
    PE delta should be negative
```

Suggested implementation:

```python
signed_underlying_move = expected_move_abs if direction == "BULLISH" else -expected_move_abs
estimated_option_change = (
    delta * signed_underlying_move
    + 0.5 * gamma * signed_underlying_move * signed_underlying_move
    + theta * expected_holding_days
)
```

For spreads, estimate both legs and net the result.

This is only a local approximation and should be used for ranking, not final theoretical pricing.

---

## 16. `option_selector.py` Requirements

This is the main orchestrator.

### 16.1 Required function

```python
def select_option_strategy(
    db_client,
    underlying_view: UnderlyingView,
    spot_price: float,
    as_of_time: str | None = None,
    atm_iv_history_90d: list[float] | None = None,
) -> OptionSelectionResult:
    ...
```

### 16.2 Orchestration logic

```text
1. If underlying_view.raw_signal = NO_POSITION:
       return NO_TRADE with reason "Underlying signal is NO_POSITION"

2. If underlying_view.strength_score < 65:
       return NO_TRADE with reason "Underlying signal score below threshold"

3. Derive option_bias using derive_option_bias().

4. If option_bias = NEUTRAL:
       return NO_TRADE with reason "Option bias neutral after downgrades"

5. Load option chain using repository.load_option_chain_with_calcs().

6. If no option chain rows:
       return NO_TRADE with reason "No option chain rows available"

7. Compute option features using compute_option_features_for_chain().

8. Compute ATM IV rank/percentile from option features or provided history.

9. Choose strategy type using choose_option_strategy_type().

10. If strategy type = NO_TRADE:
        return NO_TRADE with reason from strategy rules

11. Build candidates using strategy_builder.build_strategy_candidates().

12. If no candidates:
        return NO_TRADE with reason "No candidates passed base filters"

13. Calculate risk for each candidate.

14. Score candidates.

15. Sort candidates by score descending.

16. If best score < 65:
        return NO_TRADE with reason "Best option candidate score below threshold"

17. Return OptionSelectionResult with best candidate.
```

### 16.3 NO_TRADE candidate

Implement helper:

```python
def no_trade_result(
    underlying: str,
    trade_date: str,
    reason: str,
    option_bias: OptionBias = "NEUTRAL",
    evaluated_candidate_count: int = 0,
) -> OptionSelectionResult:
    ...
```

The `selected_strategy.strategy_type` should be `NO_TRADE`.

---

## 17. `optionselection_common.py` Requirements

Re-export public classes/functions similar to existing `underlying_prediction_common.py`.

Expected exports:

```python
from .schema import (
    OptionContract,
    OptionFeatures,
    OptionLeg,
    OptionStrategyCandidate,
    OptionSelectionResult,
)
from .option_selector import select_option_strategy
from .underlying_view_strength import derive_option_bias
from .strategy_rules import choose_option_strategy_type
```

---

## 18. Integration With Existing Underlying Strategy Output

Current strategies return:

```text
CALL
PUT
NO_POSITION
```

Add a conversion layer:

```text
CALL -> BULLISH direction
PUT -> BEARISH direction
NO_POSITION -> NEUTRAL direction
```

But option selection should also require:

```text
strength_score >= 65
stock_regime not CHOPPY
expected move present or ATR available
reasonable liquidity in options chain
```

Example:

```text
Underlying raw signal = CALL
strength_score = 82
stock_regime = TREND_UP
sector_regime = TREND_UP
expected_move_pct = 1.2%
IV rank = 45

Option selection result:
LONG_CALL candidate if liquid contract exists
```

Another example:

```text
Underlying raw signal = CALL
strength_score = 70
stock_regime = TREND_UP
IV rank = 82

Option selection result:
BULL_CALL_SPREAD preferred over LONG_CALL
```

Another example:

```text
Underlying raw signal = PUT
strength_score = 61

Option selection result:
NO_TRADE because underlying view is too weak
```

---

## 19. Recommended Defaults for V1

Use these constants in a config file or top-level constants.

```python
MIN_UNDERLYING_SCORE = 65
HIGH_UNDERLYING_SCORE = 80

PREFERRED_LONG_MIN_DTE = 5
PREFERRED_LONG_MAX_DTE = 21
PREFERRED_SPREAD_MIN_DTE = 7
PREFERRED_SPREAD_MAX_DTE = 30

MAX_LONG_OPTION_SPREAD_PCT = 0.05
HARD_MAX_SPREAD_PCT = 0.10
MIN_LIQUIDITY_SCORE = 60
MIN_SELL_LEG_LIQUIDITY_SCORE = 50

LONG_OPTION_MIN_DELTA = 0.35
LONG_OPTION_MAX_DELTA = 0.65
SPREAD_BUY_LEG_MIN_DELTA = 0.45
SPREAD_BUY_LEG_MAX_DELTA = 0.65
SPREAD_SELL_LEG_MIN_DELTA = 0.20
SPREAD_SELL_LEG_MAX_DELTA = 0.40

MAX_THETA_BURN_PCT_PER_DAY_PREFERRED = 0.08
MAX_THETA_BURN_PCT_PER_DAY_HARD = 0.12

HIGH_IV_RANK = 70
VERY_HIGH_IV_RANK = 85
MIN_CANDIDATE_SCORE = 65
```

---

## 20. Testing Requirements

Add unit tests for each module.

### 20.1 `underlying_view_strength.py`

Test cases:

```text
CALL + score 85 -> BULLISH_STRONG
CALL + score 70 -> BULLISH_MODERATE
CALL + score 60 -> NEUTRAL
PUT + score 85 -> BEARISH_STRONG
PUT + score 70 -> BEARISH_MODERATE
NO_POSITION -> NEUTRAL
CHOPPY regime downgrades to NEUTRAL
```

### 20.2 `option_features.py`

Test:

```text
spread_pct calculation
mid_price fallback
theta_burn calculation
moneyness calculation
liquidity score penalties
missing IV/Greeks behavior
```

### 20.3 `strategy_rules.py`

Test:

```text
BULLISH_STRONG + low IV -> LONG_CALL
BULLISH_STRONG + high IV -> BULL_CALL_SPREAD
BULLISH_MODERATE -> BULL_CALL_SPREAD
BEARISH_STRONG + low IV -> LONG_PUT
BEARISH_STRONG + high IV -> BEAR_PUT_SPREAD
BEARISH_MODERATE -> BEAR_PUT_SPREAD
NEUTRAL -> NO_TRADE
```

### 20.4 `candidate_filter.py`

Test:

```text
Long call candidate passes delta/liquidity/theta filters
Long call rejected for spread > 10%
Long put candidate passes absolute delta logic
Spread buy/sell legs are selected with same expiry
Bull call spread sell strike > buy strike
Bear put spread sell strike < buy strike
```

### 20.5 `strategy_builder.py`

Test:

```text
LONG_CALL creates one BUY CE leg
LONG_PUT creates one BUY PE leg
BULL_CALL_SPREAD creates BUY lower CE + SELL higher CE
BEAR_PUT_SPREAD creates BUY higher PE + SELL lower PE
Reject invalid net debit
Reject reward/risk < 1
```

### 20.6 `option_selector.py`

Test:

```text
Weak underlying returns NO_TRADE
Missing option chain returns NO_TRADE
No liquid candidate returns NO_TRADE
Strong bullish + normal IV selects LONG_CALL
Moderate bullish + high IV selects BULL_CALL_SPREAD
Strong bearish + normal IV selects LONG_PUT
Moderate bearish + high IV selects BEAR_PUT_SPREAD
Best candidate score below 65 returns NO_TRADE
```

---

## 21. Out of Scope for V1

Do not implement these now:

```text
Naked short call
Naked short put
Iron condor
Iron butterfly
Calendar spread
Ratio spread
Straddle/strangle
Volatility forecasting model
Full Black-Scholes pricing module
Heston/SABR/local-vol model
Live order execution
Broker order placement
Position management after entry
```

These can be added later once the underlying and option-selection backtests are stable.

---

## 22. Backtesting Hook

The option selection result should be easy to backtest.

Ensure `OptionSelectionResult` contains:

```text
underlying
trade_date
selected strategy type
legs with tradingsymbol, expiry, strike, side, quantity, entry price
max loss
max profit
breakeven
score
confidence
reasons
```

A later backtest engine should be able to:

```text
1. Read historical UnderlyingView for date T
2. Read option chain snapshot at date T close or configured snapshot time
3. Run option selector
4. Enter selected option trade at next day open or configured execution price
5. Track exit by target/stop/time expiry
6. Store P&L and outcome
```

Avoid lookahead bias:

```text
Use only option chain, IV, Greeks, OHLCV and underlying features available at or before signal time.
```

---

## 23. Acceptance Criteria

Codex implementation is complete when:

```text
1. UnderlyingView exists and can be created from current CALL/PUT/NO_POSITION output.
2. optionselection/ folder exists with clean modules listed above.
3. Option selection does not recalculate IV/Greeks.
4. Option chain data is loaded with IV/Greeks from OptionSnapshotCalc.
5. LONG_CALL, LONG_PUT, BULL_CALL_SPREAD, BEAR_PUT_SPREAD, NO_TRADE are supported.
6. Candidate filtering applies delta, DTE, spread, liquidity, theta burn and IV sanity rules.
7. Risk calculations work for single-leg and debit-spread strategies.
8. Best candidate is selected by score.
9. Weak or poor-quality setups return NO_TRADE with clear reason.
10. Unit tests cover feature, bias, strategy rule, filtering, builder, risk and selector logic.
```

---

## 24. Final Design Principle

The option selection engine should answer:

```text
Given the underlying view, is there a liquid and risk-controlled option structure that expresses that view better than no trade?
```

It should not answer only:

```text
Which call or put should I buy?
```

The correct decision hierarchy is:

```text
Underlying view quality
↓
Option bias
↓
Volatility and IV context
↓
Strategy type
↓
Candidate contract filter
↓
Risk/reward calculation
↓
Final scored recommendation or NO_TRADE
```
