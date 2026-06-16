# Technical Analysis

Current scope: NIFTY underlying prediction and NIFTY option selection.

## Workflow

```text
daily/backfill underlying OHLC
  -> UnderlyingSnapshot
  -> calculate_underlying_features.py
  -> SignalFeatureDaily

daily/backfill option snapshots
  -> OptionInstrument
  -> OptionSnapshot
  -> calculate_option_snapshot_calc.py
  -> OptionSnapshotCalc
```

Predictions and option selection are computed in-memory from `SignalFeatureDaily` + `OptionSnapshotCalc`. They are not persisted — strategy logic is still being finalised. Use `tests/` to exercise the pipeline.

## Regime Detection

Computed by `detect_regime()` (`src/technical_analysis/prediction/regime.py`) from a rolling OHLCV window (90 days). Detection is ordered — first match wins.

| Regime | Conditions |
|---|---|
| `UNKNOWN` | Fewer than 10 closes in window — insufficient data |
| `TREND_UP` | close > MA20 > MA50 · both slopes > 0 · ret_60d > 2% · trend_efficiency_60d > 0.25 |
| `TREND_DOWN` | close < MA20 < MA50 · both slopes < 0 · ret_60d < −2% · trend_efficiency_60d > 0.25 |
| `CHOPPY` | \|ret_60d\| ≤ 4% **and** trend_efficiency_60d ≤ 0.25 **and** volatility_20d ≥ 2.5% **and** ≥ 2 MA20/MA50 crossovers in last 20d |
| `RANGE` | \|ret_60d\| ≤ 4% **and** \|MA20 slope\| ≤ 0.01 **and** \|MA50 slope\| ≤ 0.01 **and** range_position_20d ∈ [15%, 85%] **and** volatility_20d ≤ 3% |
| `RANGE` | \|ret_60d\| ≤ 4% (partial — price moved little even if not oscillating cleanly) |
| `CHOPPY` | trend_efficiency_60d ≤ 0.25 **and** volatility_20d ≥ 2.5% |
| `RANGE` | default fallback — anything else |

**Key indicators:**
- `ret_60d` — 60-day price return (decimal)
- `trend_efficiency_60d` — directional efficiency: net move ÷ total path length (0–1; higher = cleaner trend)
- `range_position_20d` — where today's close sits within the 20-day high/low range (0 = at low, 1 = at high)
- `ma20_50_crossovers_20d` — number of times MA20 crossed MA50 in the last 20 trading days

The regime is pre-computed and stored in `SignalFeatureDaily.regime` by `calculate_underlying_features.py`. The prediction pipeline reads it from there; it only re-detects live from the window if the stored value is `UNKNOWN`.

---

## Direction Prediction

### Regime-to-strategy routing

Only the strategies matched to the detected regime run. Others are left blank in the output.

| Regime | Active strategies |
|---|---|
| `TREND_UP` | `MaTrend_001`, `trendUpRangeBreakout`, `BollingerMeanReversion` |
| `TREND_DOWN` | `MaTrend_001`, `trendDownRangeBreakout`, `BollingerMeanReversion` |
| `RANGE` | `rangeBollingerMeanReversion`, `rangeRsiMeanReversion_6535` |
| `CHOPPY` / `UNKNOWN` | (none — no trade) |

### Strategy signal logic

| Strategy | Signal rule |
|---|---|
| `MaTrend_001` | CALL if (MA10 − MA20) / MA20 > 0.1%; PUT if spread < −0.1%; else NO_POSITION |
| `trendUpRangeBreakout` | CALL if today's close > 20-day high (breakout); other outcomes → NO_POSITION |
| `trendDownRangeBreakout` | PUT if today's close < 20-day low (breakdown); other outcomes → NO_POSITION |
| `BollingerMeanReversion` | CALL if close < lower BB (MA20 − 2σ); PUT if close > upper BB (MA20 + 2σ) |
| `rangeBollingerMeanReversion` | Same as above; restricted to RANGE regime |
| `rangeRsiMeanReversion_6535` | CALL if RSI14 < 40; PUT if RSI14 > 60; else NO_POSITION *(thresholds relaxed from 35/65 to fire in moderate range conditions)* |

### Aggregation into `raw_signal`

Each strategy signal is scored individually (0–100). The pipeline then aggregates:

1. **Eligible** — only signals with score ≥ 30 and raw_signal ∈ {CALL, PUT} count
2. **Direction** — BULLISH if Σ(bullish scores) ≥ Σ(bearish scores) × 1.25 and Σ(bullish) ≥ 30; BEARISH if reverse; else NEUTRAL
3. **Conflict** — if both Σ(bullish) ≥ 40 and Σ(bearish) ≥ 40 → override to NEUTRAL
4. `raw_signal` = CALL / PUT from direction; NO_POSITION if NEUTRAL or no eligible signals

`is_option_eligible = True` only when `strength_score ≥ 65`. RANGE mean-reversion signals typically score 30–50 (the setup contradicts trend-based metrics) and are therefore backtest-only.

### Score components (`strength_score`)

| Component | Max pts | What drives it |
|---|---|---|
| `stock_technical_score` | 20 | **TREND:** MA alignment (close vs MA20/50), slope direction, RSI in [45, 70]. **RANGE:** RSI ≤ 40/30 (+10/+5) and close ≤ lower BB (+5); mirrored for PUT. |
| `volume_confirmation_score` | 10 | vol_ratio ≥ 1.5× → 10; ≥ 1.1× → 7; ≥ 0.8× → 4; else 0 |
| `risk_quality_score` | 10 | ATR/close ≤ 4% → +4; volatility_20d ≤ 3.5% → +3; reward/risk undefined → +3 |
| `regime_quality_score` | 15 | TREND with matching sector → +15; TREND alone → +8; RANGE + matching setup type → +10; CHOPPY → −15 |
| `sector_confirmation_score` | 15 | *Always 0 for NIFTY — no external sector regime in pipeline* |
| `benchmark_confirmation_score` | 7 | *Always 0 for NIFTY* |
| `relative_strength_score` | 15 | *Always 0 for NIFTY (no RS vs sector/benchmark)* |
| `penalty_score` | −20 max | −5 per missing critical feature: close, MA20, MA50, RSI14, ATR14, volatility_20d |

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/daily_NIFTY/daily_market_refresh.py` | Fetch current daily NIFTY OHLC and chain feature calculation. |
| `scripts/backfill_NIFTY/backfill_underlying.py` | Backfill NIFTY OHLC and chain feature calculation. |
| `scripts/daily_NIFTY/daily_optionInstrument_refresh.py` | Refresh active NIFTY option contracts from Kite. |
| `scripts/daily_NIFTY/daily_NIFTYoption_snapshot.py` | Capture live NIFTY option quotes and chain option calc. |
| `scripts/backfill_NIFTY/backfill_NIFTYoptions_from_historical.py` | Backfill option snapshots from historical Kite candles and chain option calc. |
| `scripts/Common/calculate_underlying_features.py` | Build `SignalFeatureDaily` from `UnderlyingSnapshot`. |
| `scripts/Common/calculate_option_snapshot_calc.py` | Build `OptionSnapshotCalc` from `OptionSnapshot`. |

## Editing Strategies and Backtesting

### Where to make changes

**Prediction strategy layer**

| What to change | File |
|---|---|
| Individual signal functions (MA trend, RSI MR, Bollinger MR, regime-gated variants) | `src/technical_analysis/prediction/strategies.py` |
| How multiple signals are aggregated into a single CALL / PUT / NO_POSITION | `src/technical_analysis/prediction/aggregator.py` |
| Strength score, confidence, and `option_bias` derivation | `src/technical_analysis/prediction/scoring.py` |
| Regime detection (TREND_UP / TREND_DOWN / RANGE / CHOPPY) | `src/technical_analysis/prediction/regime.py` |

**Option selection layer**

| What to change | File |
|---|---|
| Which strategy type to run (LONG_CALL, BULL_CALL_SPREAD, etc.) based on bias + IV rank | `src/technical_analysis/optionselection/strategy_rules.py` |
| Candidate filtering (delta range, liquidity, DTE) | `src/technical_analysis/optionselection/candidate_filter.py` |
| How candidates are scored | `src/technical_analysis/optionselection/scoring.py` |

### How to run a backtest

1. **Prediction backtest** — reads `SignalFeatureDaily` from DB, runs all strategies over a rolling OHLCV window, writes `output/backtest/NIFTY_prediction.csv`:
   ```
   python backtest/test_underlying_prediction.py --underlying NIFTY --start 2026-04-01
   ```

2. **Option selection + P&L backtest** — reads the prediction CSV, runs option selection at EOD chain, calculates next-day P&L and 5-day 2%-profit scan, writes `output/backtest/NIFTY_optionSelection.csv`:
   ```
   python backtest/test_optionselection_e2e.py
   ```

Run step 1 first whenever prediction strategies change. Run step 2 whenever option selection logic changes (it consumes step 1's output without re-running the prediction layer).

## Code Packages

| Package | Purpose |
|---|---|
| `src/technical_analysis/prediction/` | Underlying feature schema, regime, strategy signals, scoring, expected move, and final view construction. |
| `src/technical_analysis/optionselection/` | Current option-selection engine: chain repository, option features, filters, strategy builder, risk, and scoring. |
| `src/data_manager/` | Kite API wrapper and DB/Kite helpers used by ingestion/backfill jobs. |
| `src/common/` | Shared settings and dataclass models. |

## Flask

`flask_app.py` is NIFTY-only. It shows NIFTY data/trends from `output/backtest/NIFTY_prediction.csv` and exposes NIFTY Predict/Backtest actions for local inspection.
