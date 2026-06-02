# Requirements: Optimized NIFTY Option Selection Engine

## 1. Purpose

Build an optimized option-selection module for daily NIFTY option trading.

The underlying prediction engine already determines the directional view on NIFTY.  
This module should decide the best option instrument or spread to trade based on:

- Directional view
- View strength
- Expected move
- Expected holding period
- IV regime
- Term structure
- Candidate IV richness
- Greeks
- Theta risk
- Spread cost
- Liquidity
- Historical backtest performance

The selector must avoid brute-force generation of all strikes, all expiries, and all strategies.  
Instead, it should use a narrow, explainable candidate-generation approach based on templates.

---

## 2. Core Principle

The option selector should not simply do:

```text
BULLISH = buy CE
BEARISH = buy PE
```

It should decide:

```text
Given the NIFTY view, expected move, IV regime, theta drag, spread cost, liquidity, and historical evidence,
which option or spread gives the best risk-adjusted payoff?
```

The key trading question is:

```text
Will NIFTY move enough, fast enough, to overcome theta decay, IV risk, spread cost, and execution drag?
```

---

## 3. High-Level Workflow

The final optimized workflow should be:

```text
1. Get NIFTY directional view from the underlying engine
2. Calculate market IV and term IV regimes
3. Choose allowed strategy family
4. Choose 1–2 expiry buckets
5. Choose 2–4 target delta templates
6. Fetch only nearest liquid strikes
7. Build a small candidate shortlist
8. Calculate candidate IV richness
9. Calculate implied move and expected move fit
10. Calculate theta safety
11. Calculate spread cost
12. Estimate expected P&L using Greeks
13. Apply hard rejection filters
14. Lookup precomputed historical bucket score
15. Score shortlisted candidates
16. Rank and return best trade, backup trade, and rejected candidates with reasons
```

---

## 4. Non-Goal

The module should not generate every possible option candidate.

Avoid this:

```text
all strikes × all expiries × CE/PE × all strategies
```

Instead, use this:

```text
underlying view → strategy templates → target expiry buckets → target delta buckets → nearest liquid strikes → score shortlist
```

The system should normally evaluate only 5–15 candidates per signal.

---

## 5. Input From Underlying Prediction Engine

The option selector expects the underlying engine to provide the following input:

```json
{
  "underlying": "NIFTY",
  "direction": "BULLISH",
  "view_strength": "MODERATE",
  "expected_move_points": 70,
  "expected_holding_days": 1,
  "confidence": 0.68,
  "market_regime": "TREND_UP"
}
```

### 5.1 Required Fields

| Field | Type | Description |
|---|---|---|
| `underlying` | string | Example: `NIFTY` |
| `direction` | enum | `BULLISH`, `BEARISH`, `NEUTRAL` |
| `view_strength` | enum | `STRONG`, `MODERATE`, `WEAK` |
| `expected_move_points` | number | Expected NIFTY move in points |
| `expected_holding_days` | number | Expected holding period in trading days |
| `confidence` | number | Model confidence from 0 to 1 |
| `market_regime` | enum | `TREND_UP`, `TREND_DOWN`, `RANGE`, `CHOPPY` |

### 5.2 Direction Handling

If `direction = NEUTRAL` or `view_strength = WEAK`, the module should generally return `NO_TRADE`, unless a separate premium-selling strategy is explicitly enabled.

---

## 6. Required Market Data

The option selector needs access to:

### 6.1 Current Option Chain

For each option instrument:

| Field | Description |
|---|---|
| `instrument_token` | Unique option instrument token |
| `tradingsymbol` | Option symbol |
| `underlying` | NIFTY |
| `option_type` | `CE` or `PE` |
| `strike` | Strike price |
| `expiry_date` | Expiry date |
| `dte` | Days to expiry |
| `bid` | Best bid |
| `ask` | Best ask |
| `last_price` | Last traded price |
| `volume` | Traded volume |
| `open_interest` | Open interest |
| `iv` | Implied volatility |
| `delta` | Delta |
| `gamma` | Gamma |
| `theta` | Theta per day |
| `vega` | Vega per one IV percentage-point move |

### 6.2 Historical Option Data

Required for:

- Candidate IV percentile
- Historical bucket performance
- Backtesting
- Profit factor
- Hit rate
- MFE / MAE
- Theta loss
- IV change effect

### 6.3 Historical Market IV Data

Required for:

- Market IV regime
- Term IV regime

Can use:

- India VIX
- ATM 1W NIFTY IV
- ATM 2W NIFTY IV
- ATM 1M NIFTY IV
- ATM 2M NIFTY IV

---

## 7. Price Conventions

### 7.1 Mid Price

Use mid price for theoretical calculation:

```text
mid_price = (bid + ask) / 2
```

### 7.2 Realistic Long Entry / Exit

For realistic execution:

```text
long_entry_price = ask
long_exit_price = bid
```

### 7.3 Debit Spread Execution

For a debit spread:

```text
spread_debit = long_leg_ask - short_leg_bid
```

This represents a conservative executable debit.

---

## 8. Option Bucketing

The system should bucket options using DTE and delta, not raw strikes alone.

---

## 9. DTE Bucket

Classify every option into a DTE bucket:

| Bucket | DTE Range |
|---|---:|
| `EXPIRY_NEAR` | 0–3 days |
| `WEEKLY` | 4–7 days |
| `TWO_WEEK` | 8–14 days |
| `MONTHLY` | 15–35 days |
| `TWO_MONTH` | 36–65 days |

---

## 10. Delta / Moneyness Bucket

Use absolute delta.

For calls:

```text
abs_delta = delta
```

For puts:

```text
abs_delta = abs(delta)
```

Classify as:

| Bucket | Absolute Delta |
|---|---:|
| `FAR_ITM` | `>= 0.85` |
| `ITM` | `0.65 – 0.85` |
| `ATM` | `0.45 – 0.65` |
| `OTM` | `0.15 – 0.45` |
| `FAR_OTM` | `< 0.15` |

---

## 11. IV Framework

The system must classify IV at three levels:

```text
1. Market IV regime
2. Term IV regime
3. Candidate IV richness
```

Do not use one global `HIGH_IV` or `LOW_IV` label for all options.

---

## 12. Market IV Regime

Market IV regime describes whether the overall NIFTY option market is cheap or expensive.

Use one or more of:

```text
India VIX
ATM 1M NIFTY IV
ATM 2W NIFTY IV
ATM 1W NIFTY IV
```

Calculate:

```text
market_iv_percentile =
count(historical_market_iv <= current_market_iv) / total_observations
```

Recommended lookback:

```text
252 trading days
```

### 12.1 Market IV Classification

| Percentile | Market IV Regime |
|---:|---|
| `0–20` | `LOW_IV` |
| `20–60` | `NORMAL_IV` |
| `60–85` | `HIGH_IV` |
| `85–100` | `EXTREME_IV` |

### 12.2 Usage

Market IV regime is used to decide whether the environment favors:

- Naked long options
- Debit spreads
- Credit spreads
- No trade

General rule:

```text
LOW / NORMAL IV = naked option buying is more acceptable
HIGH / EXTREME IV = prefer spreads or avoid naked buying
```

---

## 13. Term IV Regime

Term IV regime describes whether a specific expiry bucket is cheap or expensive.

Calculate ATM IV percentile separately for:

```text
WEEKLY
TWO_WEEK
MONTHLY
TWO_MONTH
```

Example:

| Expiry Bucket | Current ATM IV | Percentile | Regime |
|---|---:|---:|---|
| `WEEKLY` | 20% | 88 | `EXTREME_IV` |
| `TWO_WEEK` | 17% | 70 | `HIGH_IV` |
| `MONTHLY` | 15% | 48 | `NORMAL_IV` |
| `TWO_MONTH` | 14.5% | 42 | `NORMAL_IV` |

### 13.1 Usage

Term IV regime helps decide which expiry to prefer.

Example:

```text
If WEEKLY IV is EXTREME_IV and TWO_WEEK IV is NORMAL_IV,
avoid naked weekly options and prefer TWO_WEEK candidates.
```

---

## 14. Candidate IV Richness

Candidate IV richness describes whether a specific candidate option is cheap or expensive relative to similar historical options.

For each shortlisted candidate, compare current IV with historical IV of options having the same:

```text
option_type
DTE bucket
delta bucket
```

Formula:

```text
candidate_iv_percentile =
count(historical_similar_option_iv <= current_candidate_iv) / total_similar_observations
```

### 14.1 Candidate IV Classification

| Percentile | Candidate IV Status |
|---:|---|
| `0–20` | `CHEAP_IV` |
| `20–60` | `FAIR_IV` |
| `60–85` | `EXPENSIVE_IV` |
| `85–100` | `VERY_EXPENSIVE_IV` |

### 14.2 Usage

For naked long options:

```text
lower candidate_iv_percentile = better
```

For debit spreads:

```text
high IV is less harmful because the short leg offsets part of the premium
```

For credit spreads:

```text
higher IV can be useful, but only with strict risk controls
```

---

## 15. Strategy Family Selection

Use the underlying view and IV regime to choose allowed strategy families.

---

## 16. Bullish Strategy Rules

| View Strength | IV Condition | Allowed Strategies |
|---|---|---|
| `STRONG` | `LOW_IV` / `NORMAL_IV` | Long CE |
| `STRONG` | `HIGH_IV` / `EXTREME_IV` | Bull call spread |
| `MODERATE` | `LOW_IV` / `NORMAL_IV` | ITM CE / 2W CE |
| `MODERATE` | `HIGH_IV` / `EXTREME_IV` | Bull call spread |
| `WEAK` | Any IV | No trade |

---

## 17. Bearish Strategy Rules

| View Strength | IV Condition | Allowed Strategies |
|---|---|---|
| `STRONG` | `LOW_IV` / `NORMAL_IV` | Long PE |
| `STRONG` | `HIGH_IV` / `EXTREME_IV` | Bear put spread |
| `MODERATE` | `LOW_IV` / `NORMAL_IV` | ITM PE / 2W PE |
| `MODERATE` | `HIGH_IV` / `EXTREME_IV` | Bear put spread |
| `WEAK` | Any IV | No trade |

---

## 18. Expiry Bucket Selection

Do not check all expiries.

Map expected holding period to preferred expiry buckets.

| Expected Holding Period | Preferred Expiry Buckets |
|---|---|
| Intraday / 1 day | `WEEKLY`, `TWO_WEEK` |
| 2–3 days | `TWO_WEEK`, `MONTHLY` |
| 4–7 days | `TWO_WEEK`, `MONTHLY` |
| More than 1 week | `MONTHLY`, `TWO_MONTH` |

### 18.1 Expiry Adjustment Rules

```text
If view is STRONG and expected move is fast:
    allow WEEKLY and TWO_WEEK

If view is MODERATE:
    prefer TWO_WEEK or MONTHLY

If WEEKLY IV is EXTREME_IV:
    avoid naked WEEKLY options
    allow WEEKLY only for spreads

If DTE <= 2 and view is not STRONG:
    avoid naked long options
```

---

## 19. Target Delta Templates

The selector should use target deltas, not all strikes.

---

## 20. Long CE / PE Delta Targets

| View Type | Target Delta |
|---|---:|
| Strong directional | `0.45 – 0.60` |
| Moderate directional | `0.60 – 0.75` |
| Slow directional | `0.65 – 0.80` |
| Breakout / explosive | `0.30 – 0.45` |
| Usually avoid | `< 0.20` |

For puts, use absolute delta.

---

## 21. Bull Call Spread Template

For bullish debit spreads:

| Leg | Target Delta |
|---|---:|
| Long CE | `0.50 – 0.65` |
| Short CE | `0.25 – 0.40` |

Alternative short-leg selection:

```text
short CE strike near spot + expected_move_points
```

Example:

```text
Spot = 22,000
Expected move = +120

Short CE strike ≈ 22,100 or 22,150
```

---

## 22. Bear Put Spread Template

For bearish debit spreads:

| Leg | Target Abs Delta |
|---|---:|
| Long PE | `0.50 – 0.65` |
| Short PE | `0.25 – 0.40` |

Alternative short-leg selection:

```text
short PE strike near spot - expected_move_points
```

---

## 23. Candidate Template Examples

### 23.1 Strong Bullish + Low/Normal IV

Generate:

```text
1. WEEKLY ATM CE, delta ~0.50
2. WEEKLY slightly ITM CE, delta ~0.60
3. TWO_WEEK ATM CE, delta ~0.50
4. TWO_WEEK slightly ITM CE, delta ~0.60
```

### 23.2 Strong Bullish + High/Extreme IV

Generate:

```text
1. WEEKLY bull call spread: long CE delta ~0.55, short CE delta ~0.30
2. TWO_WEEK bull call spread: long CE delta ~0.55, short CE delta ~0.30
3. TWO_WEEK ITM CE, delta ~0.65
```

### 23.3 Moderate Bullish + Low/Normal IV

Generate:

```text
1. TWO_WEEK ITM CE, delta ~0.65
2. TWO_WEEK ATM/ITM CE, delta ~0.55
3. MONTHLY ITM CE, delta ~0.65
```

### 23.4 Moderate Bullish + High/Extreme IV

Generate:

```text
1. TWO_WEEK bull call spread
2. MONTHLY bull call spread
3. TWO_WEEK ITM CE only if theta and IV are acceptable
```

### 23.5 Strong Bearish + Low/Normal IV

Generate:

```text
1. WEEKLY ATM PE, abs(delta) ~0.50
2. WEEKLY slightly ITM PE, abs(delta) ~0.60
3. TWO_WEEK ATM PE, abs(delta) ~0.50
```

### 23.6 Strong Bearish + High/Extreme IV

Generate:

```text
1. WEEKLY bear put spread
2. TWO_WEEK bear put spread
3. TWO_WEEK ITM PE, abs(delta) ~0.65
```

### 23.7 Moderate Bearish + Low/Normal IV

Generate:

```text
1. TWO_WEEK ITM PE, abs(delta) ~0.65
2. MONTHLY ITM PE, abs(delta) ~0.65
```

### 23.8 Moderate Bearish + High/Extreme IV

Generate:

```text
1. TWO_WEEK bear put spread
2. MONTHLY bear put spread
3. TWO_WEEK ITM PE only if theta and IV are acceptable
```

---

## 24. Finding Actual Strikes

For each candidate template:

### 24.1 Single Option

Find the option closest to target delta:

```text
selected_option = option where abs(option_delta - target_delta) is minimum
```

For puts:

```text
selected_option = option where abs(abs(option_delta) - target_abs_delta) is minimum
```

### 24.2 Spread

Find both legs:

```text
long_leg = option closest to long_leg_target_delta
short_leg = option closest to short_leg_target_delta
```

For bull call spread:

```text
long_leg = CE with delta ~0.50–0.65
short_leg = CE with delta ~0.25–0.40
```

For bear put spread:

```text
long_leg = PE with abs(delta) ~0.50–0.65
short_leg = PE with abs(delta) ~0.25–0.40
```

### 24.3 Tie-Breakers

If multiple strikes are close to the target delta, choose the one with:

```text
1. Higher liquidity
2. Lower spread
3. Better theta safety
4. Better candidate IV score
```

---

## 25. Liquidity Filters

Apply liquidity filters before scoring.

Recommended configurable thresholds:

```json
{
  "minimum_volume": 1000,
  "minimum_open_interest": 5000,
  "max_bid_ask_spread_pct": 0.08
}
```

Use stricter thresholds for near-ATM options if desired.

Reject candidates if:

```text
volume < minimum_volume
open_interest < minimum_open_interest
bid_ask_spread_pct > max_bid_ask_spread_pct
```

---

## 26. Implied Move Calculation

For each candidate expiry:

```text
implied_move_to_expiry =
spot_price × IV × sqrt(DTE / 365)
```

Use IV in decimal form.

For the expected holding period:

```text
implied_move_for_holding =
spot_price × IV × sqrt(expected_holding_days / 365)
```

Then:

```text
expected_move_fit =
expected_move_points / implied_move_for_holding
```

### 26.1 Interpretation

| Expected Move Fit | Meaning |
|---:|---|
| `< 0.5` | Weak for naked option buying |
| `0.5 – 1.0` | Needs strong view |
| `1.0 – 1.5` | Good |
| `> 1.5` | Very strong |

---

## 27. Theta Safety

For a single long option:

```text
theta_pct_per_day =
abs(theta) / option_price
```

```text
theta_break_even_points =
abs(theta) / abs(delta)
```

Interpretation:

```text
theta_break_even_points = NIFTY points required to offset one day of theta decay
```

Example:

```text
Theta = -8
Delta = 0.40

theta_break_even_points = 8 / 0.40 = 20 points
```

---

## 28. Net Theta for Spreads

For a debit spread, calculate net theta.

Assume Greeks are stored from the perspective of a long option.

For a bull call spread:

```text
long CE theta = -8
short CE theta = -4

Since the second leg is short:
short leg theta contribution = +4

net_theta = -8 + 4 = -4
```

General formula:

```text
net_theta = long_leg_theta - short_leg_theta
```

This works if both theta values are stored as long-option theta values.

---

## 29. Spread Cost

### 29.1 Single Long Option

```text
bid_ask_spread = ask - bid
mid_price = (bid + ask) / 2
bid_ask_spread_pct = bid_ask_spread / mid_price
```

Approximate round-trip spread cost:

```text
spread_cost_pct ≈ bid_ask_spread_pct
```

### 29.2 Debit Spread

```text
spread_debit = long_leg_ask - short_leg_bid
```

```text
spread_cost =
long_leg_half_spread + short_leg_half_spread
```

Where:

```text
half_spread = (ask - bid) / 2
```

```text
spread_cost_pct =
spread_cost / spread_debit
```

Reject if:

```text
spread_cost_pct > allowed threshold
```

Recommended threshold:

```text
5% to 8%
```

---

## 30. Expected IV Change Estimate

The module needs a practical estimate of expected IV change.

Start with simple rules.

| Situation | Expected IV Change |
|---|---:|
| Low IV + breakout setup | `+0.5 to +2 IV points` |
| Normal IV + trend setup | `0 to +1 IV point` |
| High IV + post-panic setup | `-1 to -3 IV points` |
| Extreme IV | `-2 to -5 IV points`, unless event risk remains |

For daily NIFTY trading, be conservative.

If IV is high and the directional view is only moderate:

```text
expected_iv_change = -1 or -2
```

---

## 31. Expected P&L for Naked Long Option

For a naked long CE or PE:

```text
expected_option_change =
delta × expected_spot_move
+ 0.5 × gamma × expected_spot_move²
+ vega × expected_iv_change
+ theta × expected_holding_days
- spread_cost
```

Then:

```text
expected_return_pct =
expected_option_change / option_price
```

### 31.1 Direction Sign

For bullish CE:

```text
expected_spot_move = positive
```

For bearish PE:

```text
expected_spot_move = negative
```

Since put delta is negative:

```text
negative delta × negative expected move = positive option gain
```

---

## 32. Expected P&L for Debit Spread

For debit spreads, calculate expected change for both legs.

### 32.1 Long Leg Change

```text
long_leg_change =
long_delta × expected_spot_move
+ 0.5 × long_gamma × expected_spot_move²
+ long_vega × expected_iv_change
+ long_theta × expected_holding_days
```

### 32.2 Short Leg Change

```text
short_leg_change =
short_delta × expected_spot_move
+ 0.5 × short_gamma × expected_spot_move²
+ short_vega × expected_iv_change
+ short_theta × expected_holding_days
```

Because the second leg is short:

```text
expected_spread_change =
long_leg_change - short_leg_change - spread_cost
```

Expected return:

```text
expected_return_pct =
expected_spread_change / spread_debit
```

---

## 33. Hard Rejection Filters

Apply these before final scoring.

### 33.1 Reject Naked Long Option If Expected P&L Is Not Positive

```text
expected_option_change <= 0
```

### 33.2 Reject If Expected Move Cannot Beat Theta + Spread

```text
expected_move_points < theta_break_even_points + spread_cost_points
```

### 33.3 Reject Expensive IV for Weak/Moderate View

```text
candidate_iv_percentile > 85
and view_strength != STRONG
```

### 33.4 Reject Very Near Expiry for Non-Strong Views

```text
DTE <= 2
and view_strength != STRONG
```

### 33.5 Reject Very Low Delta

```text
abs(delta) < 0.15
```

Exception:

```text
view_strength = STRONG
and expected_move_fit > 1.5
```

### 33.6 Reject Wide Spread

```text
bid_ask_spread_pct > allowed threshold
```

### 33.7 Reject Illiquid Options

```text
volume < minimum_volume
open_interest < minimum_open_interest
```

### 33.8 Reject Naked Long Option in Choppy Market

```text
market_regime = CHOPPY
and strategy_type = naked long option
and expected_move_fit < 1.0
```

---

## 34. Historical Backtest Metrics

The system should calculate trade-level backtest metrics for each historical candidate trade.

Required trade-level inputs:

```text
entry_price
exit_price
highest_price_during_trade
lowest_price_during_trade
entry_iv
exit_iv
entry_theta
entry_vega
spread_cost
holding_days
```

---

## 35. Trade-Level Metric Calculations

### 35.1 Option Return

```text
option_return_pct =
(exit_price - entry_price - spread_cost) / entry_price
```

Meaning:

```text
Actual profitability of the option trade
```

### 35.2 Max Favourable Excursion

```text
MFE =
(highest_price_during_trade - entry_price) / entry_price
```

Meaning:

```text
Maximum profit available during the trade
```

### 35.3 Max Adverse Excursion

```text
MAE =
(lowest_price_during_trade - entry_price) / entry_price
```

Meaning:

```text
Worst drawdown during the trade
```

### 35.4 Theta Loss Realized

Simple version:

```text
theta_loss_realized = entry_theta × holding_days
theta_loss_pct = theta_loss_realized / entry_price
```

Better intraday version:

```text
theta_loss_realized =
sum(theta_t × time_fraction_t)
```

Meaning:

```text
How much time decay hurt the trade
```

### 35.5 IV Change Effect

```text
iv_change = exit_iv - entry_iv
iv_change_effect = entry_vega × iv_change
iv_change_effect_pct = iv_change_effect / entry_price
```

Meaning:

```text
How much P&L came from IV rising or falling
```

### 35.6 Spread Cost

```text
spread_cost_pct =
spread_cost / entry_price
```

Meaning:

```text
Execution drag
```

---

## 36. Historical Bucket Aggregation

Historical trades should be grouped by:

```text
direction
view_strength
market_regime
market_iv_regime
term_iv_regime
candidate_iv_regime
strategy_type
DTE bucket
delta bucket
```

For each bucket, calculate:

| Metric | Calculation |
|---|---|
| `sample_size` | Number of trades |
| `avg_return` | Average option return |
| `hit_rate` | Profitable trades / total trades |
| `avg_win` | Average return of winning trades |
| `avg_loss` | Average return of losing trades |
| `profit_factor` | Sum winning returns / absolute sum losing returns |
| `avg_mfe` | Average MFE |
| `avg_mae` | Average MAE |
| `avg_theta_loss_pct` | Average theta loss percentage |
| `avg_iv_effect_pct` | Average IV effect percentage |
| `avg_spread_cost_pct` | Average spread cost percentage |

---

## 37. Profit Factor

Formula:

```text
profit_factor =
sum(winning_returns) / abs(sum(losing_returns))
```

Interpretation:

| Profit Factor | Meaning |
|---:|---|
| `< 1.0` | Losing setup |
| `1.0 – 1.2` | Weak |
| `1.2 – 1.5` | Decent |
| `1.5 – 2.0` | Good |
| `> 2.0` | Strong, but check sample size |

---

## 38. Historical Bucket Score

Precompute historical bucket scores offline.

Formula:

```text
historical_bucket_score =
0.25 × return_score
+ 0.20 × profit_factor_score
+ 0.15 × hit_rate_score
+ 0.15 × drawdown_score
+ 0.10 × theta_score
+ 0.10 × spread_score
+ 0.05 × sample_size_score
```

### 38.1 Score Components

| Component | Higher Is Better? |
|---|---|
| `return_score` | Yes |
| `profit_factor_score` | Yes |
| `hit_rate_score` | Yes |
| `drawdown_score` | Yes, lower MAE gets higher score |
| `theta_score` | Yes, lower theta drag gets higher score |
| `spread_score` | Yes, lower spread cost gets higher score |
| `sample_size_score` | Yes |

### 38.2 Percentile Scoring

Use percentile ranks instead of raw min-max.

For positive metrics:

```text
score = percentile_rank(value)
```

For risk/cost metrics:

```text
score = 100 - percentile_rank(cost_value)
```

Examples:

```text
theta_score = 100 - percentile_rank(abs(avg_theta_loss_pct))
spread_score = 100 - percentile_rank(avg_spread_cost_pct)
drawdown_score = 100 - percentile_rank(abs(avg_mae))
```

---

## 39. Live Candidate Scoring

After rejection filters, score remaining shortlisted candidates.

Formula:

```text
final_candidate_score =
0.30 × expected_return_score
+ 0.20 × historical_bucket_score
+ 0.15 × theta_safety_score
+ 0.15 × iv_score
+ 0.10 × liquidity_score
+ 0.05 × drawdown_risk_score
+ 0.05 × spread_score
```

---

## 40. IV Score

### 40.1 Naked Long CE / PE

```text
iv_score = 100 - candidate_iv_percentile
```

Example:

| Candidate IV Percentile | IV Score |
|---:|---:|
| 15 | 85 |
| 40 | 60 |
| 75 | 25 |
| 90 | 10 |

### 40.2 Debit Spreads

```text
iv_score = 100 - abs(candidate_iv_percentile - 60)
```

Rationale:

- Very low IV is not always ideal for spreads because the short leg gives less premium.
- Very high IV is risky.
- Mid-to-high IV can be acceptable.

### 40.3 Credit Spreads

```text
iv_score = candidate_iv_percentile
```

Credit spreads should require stricter risk controls.

---

## 41. Theta Safety Score

Use:

```text
theta_pct_per_day = abs(theta) / option_price
theta_break_even_points = abs(theta) / abs(delta)
```

Suggested composite:

```text
theta_safety_score =
50 × score_from_theta_pct
+ 50 × score_from_theta_break_even_points
```

Lower theta percentage and lower theta break-even points should get higher scores.

For spreads, use net theta.

---

## 42. Liquidity Score

Use:

```text
liquidity_score =
0.40 × volume_score
+ 0.40 × open_interest_score
+ 0.20 × spread_score
```

Where each component is normalized to 0–100.

---

## 43. Drawdown Risk Score

Use historical MAE for the matching bucket:

```text
drawdown_risk_score =
100 - percentile_rank(abs(avg_mae))
```

Lower historical drawdown gets a higher score.

---

## 44. Spread Score

```text
spread_score =
100 - percentile_rank(spread_cost_pct)
```

Lower spread cost gets higher score.

---

## 45. Expected Return Score

Use normalized or percentile score based on:

```text
expected_return_pct
```

Higher expected return gets higher score.

---

## 46. Final Output Schema

The option selector should return:

```json
{
  "underlying_view": {
    "underlying": "NIFTY",
    "direction": "BULLISH",
    "view_strength": "MODERATE",
    "expected_move_points": 70,
    "expected_holding_days": 1,
    "confidence": 0.68,
    "market_regime": "TREND_UP"
  },
  "iv_context": {
    "market_iv_percentile": 76,
    "market_iv_regime": "HIGH_IV",
    "term_iv_regime": {
      "WEEKLY": "EXTREME_IV",
      "TWO_WEEK": "HIGH_IV",
      "MONTHLY": "NORMAL_IV"
    }
  },
  "recommended_trade": {
    "strategy_type": "BULL_CALL_SPREAD",
    "expiry_bucket": "TWO_WEEK",
    "score": 78,
    "reason": "Moderate bullish view with high IV. Spread reduces theta and IV risk."
  },
  "backup_trade": {
    "strategy_type": "ITM_CE",
    "expiry_bucket": "TWO_WEEK",
    "score": 70,
    "reason": "Cleaner delta exposure but higher premium at risk."
  },
  "ranked_candidates": [
    {
      "instrument_or_strategy": "BULL_CALL_SPREAD",
      "score": 78,
      "expected_return_pct": 0.11,
      "candidate_iv_percentile": 80,
      "candidate_iv_regime": "EXPENSIVE_IV",
      "theta_safety_score": 72,
      "historical_bucket_score": 74,
      "liquidity_score": 85,
      "reason": "Best risk-adjusted structure for moderate bullish view in high IV."
    },
    {
      "instrument_or_strategy": "ITM_2W_CE",
      "score": 70,
      "expected_return_pct": 0.08,
      "candidate_iv_percentile": 58,
      "candidate_iv_regime": "FAIR_IV",
      "theta_safety_score": 81,
      "historical_bucket_score": 68,
      "liquidity_score": 88,
      "reason": "Good delta exposure with manageable theta."
    }
  ],
  "rejected_candidates": [
    {
      "candidate": "OTM_WEEKLY_CE",
      "reason": "Very expensive IV, low delta, high theta percentage, and weak expected return."
    }
  ],
  "decision": "TRADE",
  "decision_reason": "At least one candidate passed filters and achieved sufficient score."
}
```

---

## 47. No-Trade Output Schema

If all candidates fail:

```json
{
  "underlying_view": {
    "underlying": "NIFTY",
    "direction": "BULLISH",
    "view_strength": "MODERATE",
    "expected_move_points": 45,
    "expected_holding_days": 1,
    "confidence": 0.58,
    "market_regime": "CHOPPY"
  },
  "iv_context": {
    "market_iv_percentile": 88,
    "market_iv_regime": "EXTREME_IV"
  },
  "recommended_trade": null,
  "ranked_candidates": [],
  "rejected_candidates": [
    {
      "candidate": "ATM_WEEKLY_CE",
      "reason": "Expected move insufficient to overcome theta and spread cost."
    },
    {
      "candidate": "BULL_CALL_SPREAD",
      "reason": "Expected return negative after IV and spread adjustment."
    }
  ],
  "decision": "NO_TRADE",
  "decision_reason": "No candidate passed hard rejection filters."
}
```

---

## 48. Suggested Module Structure

Recommended package:

```text
optionselection/
    __init__.py
    models.py
    iv_context.py
    candidate_templates.py
    candidate_builder.py
    option_features.py
    pnl_estimator.py
    filters.py
    historical_score.py
    scoring.py
    selector.py
```

---

## 49. Module Responsibilities

### 49.1 `models.py`

Define dataclasses / Pydantic models:

- `UnderlyingView`
- `OptionQuote`
- `OptionCandidate`
- `SpreadCandidate`
- `IVContext`
- `CandidateScore`
- `SelectionResult`
- `RejectedCandidate`

### 49.2 `iv_context.py`

Responsibilities:

- Calculate market IV percentile
- Classify market IV regime
- Calculate term IV percentile
- Classify term IV regime
- Calculate candidate IV percentile
- Classify candidate IV richness

### 49.3 `candidate_templates.py`

Responsibilities:

- Map underlying view + IV regime to allowed strategies
- Generate target expiry buckets
- Generate target delta templates
- Return candidate template definitions

### 49.4 `candidate_builder.py`

Responsibilities:

- Take candidate templates
- Find nearest liquid strikes
- Build single-leg and spread candidates
- Apply initial liquidity filters

### 49.5 `option_features.py`

Responsibilities:

- Calculate DTE bucket
- Calculate delta bucket
- Calculate theta percentage
- Calculate theta break-even points
- Calculate spread cost
- Calculate implied move
- Calculate expected move fit

### 49.6 `pnl_estimator.py`

Responsibilities:

- Estimate expected P&L for naked long options
- Estimate expected P&L for debit spreads
- Estimate expected return percentage
- Estimate net Greeks for spreads

### 49.7 `filters.py`

Responsibilities:

- Apply hard rejection filters
- Return rejection reasons

### 49.8 `historical_score.py`

Responsibilities:

- Lookup precomputed historical bucket score
- Return bucket-level metrics
- Handle fallback if sample size is low

### 49.9 `scoring.py`

Responsibilities:

- Normalize feature scores
- Calculate IV score
- Calculate theta safety score
- Calculate liquidity score
- Calculate final candidate score

### 49.10 `selector.py`

Responsibilities:

- Orchestrate full selection flow
- Return final ranked result
- Produce explainable reasons

---

## 50. Historical Score Fallback Logic

If the exact historical bucket has insufficient sample size:

```text
sample_size < minimum_required_sample_size
```

Use fallback hierarchy:

```text
1. Exact bucket
2. Remove candidate_iv_regime
3. Remove term_iv_regime
4. Keep only direction + view_strength + strategy_type + DTE bucket + delta bucket
5. If still insufficient, mark historical score as low-confidence
```

Add output field:

```json
{
  "historical_score_confidence": "HIGH"
}
```

Allowed values:

```text
HIGH
MEDIUM
LOW
MISSING
```

---

## 51. Configurable Parameters

Create a config file or object:

```json
{
  "lookback_days_for_iv_percentile": 252,
  "minimum_volume": 1000,
  "minimum_open_interest": 5000,
  "max_bid_ask_spread_pct": 0.08,
  "minimum_historical_sample_size": 30,
  "allow_credit_spreads": false,
  "allow_far_otm": false,
  "max_candidates_to_score": 15,
  "default_expected_iv_change_low_iv": 1.0,
  "default_expected_iv_change_normal_iv": 0.0,
  "default_expected_iv_change_high_iv": -1.0,
  "default_expected_iv_change_extreme_iv": -2.5
}
```

---

## 52. Acceptance Criteria

The implementation is complete when:

1. The module accepts an `UnderlyingView` input.
2. It calculates market IV and term IV regimes.
3. It chooses strategy templates instead of scanning all options.
4. It selects only nearest strikes based on target deltas.
5. It calculates candidate IV richness for shortlisted candidates.
6. It calculates implied move and expected move fit.
7. It calculates theta safety.
8. It calculates spread cost.
9. It estimates expected P&L for naked options and debit spreads.
10. It applies hard rejection filters.
11. It looks up historical bucket score.
12. It scores candidates using weighted scoring.
13. It returns best trade, backup trade, ranked candidates, and rejected candidates.
14. It returns `NO_TRADE` if no candidate passes filters.
15. Every recommendation includes an explainable reason.

---

## 53. Key Trading Rules to Preserve

The implementation must preserve these trading principles:

```text
Do not buy options just because direction is bullish or bearish.
```

```text
Avoid naked option buying when IV is expensive and the view is only moderate.
```

```text
Avoid far OTM options unless the expected move is explosive.
```

```text
Prefer target delta selection over raw strike selection.
```

```text
Prefer expiry buckets aligned with expected holding period.
```

```text
Use spreads when IV is high or theta risk is large.
```

```text
Reject candidates where expected move cannot beat theta + spread cost.
```

```text
Always return reasons for recommendation and rejection.
```

---

## 54. Final Summary

The optimized selector should work as follows:

```text
Underlying engine gives the view.
IV context decides the broad strategy preference.
Expected holding period decides expiry buckets.
View strength decides delta targets.
Liquidity filters choose tradable strikes.
Greeks estimate expected P&L.
Hard filters remove bad trades.
Historical bucket scores add evidence.
Final score ranks the shortlist.
```

The system should recommend the best option only when:

```text
Expected option gain > theta drag + IV risk + spread cost
```

Otherwise, return:

```text
NO_TRADE
```
