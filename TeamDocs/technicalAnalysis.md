# Technical Analysis

Current scope: DB-backed NIFTY direction prediction, followed by DB-backed option selection for CALL/PUT predictions.

## Production Flow

```text
UnderlyingSnapshot + MacroFactorDaily + GlobalIndexOhlc
  -> scripts/Common/calculate_underlying_features.py
  -> SignalFeatureDaily
OptionInstrument + OptionSnapshot + OptionSnapshotCalc
OptionInstrument + OptionOhlc
  -> src/technical_analysis/cascade
  -> NiftyPrediction
  -> src/technical_analysis/optionselection
  -> NiftyOptionSelection
  -> flask_app.py June review table
```

Cron wrapper:

```powershell
python scripts/daily_NIFTY/daily_nifty_signal.py --model-version cascade_v1
```

It assumes market refresh, global index refresh, option instrument refresh, and
option snapshot/Greek calculation have already completed. `OptionOhlc` is a
separate daily-grain option OHLC store for research and diagnostics; it does not
replace `OptionSnapshot` or `OptionSnapshotCalc` in production option selection.

## Prediction Layer

Production prediction is owned by `src/technical_analysis/cascade/` and uses the promoted regime-aware strategy roster. The daily script forces DB feature input and persists rows into `NiftyPrediction`.

Important files:

| File | Purpose |
|---|---|
| `src/technical_analysis/cascade/dataset.py` | Builds the prediction frame from `SignalFeatureDaily`, India VIX, and global index features. |
| `src/technical_analysis/cascade/strategies.py` | Promoted production strategy roster. |
| `src/technical_analysis/cascade/pipeline.py` | Runs the cascade and writes prediction outputs. |
| `src/technical_analysis/cascade/option_signal_mapper.py` | Maps cascade outputs to option-facing metadata. |
| `scripts/daily_NIFTY/daily_nifty_prediction.py` | Production DB persistence entrypoint. |
| `backtest/research/build_experiment.py` | Research harness for non-promoted strategy variants. |

## Feature Inputs

Production prediction currently uses:

- `SignalFeatureDaily` technical features.
- `MacroFactorDaily.india_vix`.
- `GlobalIndexOhlc` features via `global_*` columns.

Global index features include regional return means, breadth, and risk-on/risk-off flags. US and Europe returns are lagged one available row before joining to avoid using closes that occur after the Indian session.

News sentiment is intentionally not wired into production prediction. The news pipeline can populate `NiftyMarketSentiment` for research, but promoted cascade strategies do not currently consume sentiment fields.

## Option Selection

Production option selection is owned by `src/technical_analysis/optionselection/` and persists one row per signal date into `NiftyOptionSelection`.

Current production rules:

- CALL predictions with strength >= 65 map to `LONG_CALL`.
- PUT predictions with strength >= 65 map to `LONG_PUT`.
- Calls must be ITM CE with delta 0.70 to 0.90 and 20 to 60 DTE.
- Puts must be ITM PE with delta -0.90 to -0.70 and 20 to 60 DTE.
- Base filters require acceptable spread, liquidity, theta burn, IV quality, and positive last price.
- IV outlier checks compare against same-expiry ATM IV first, with a similar-DTE fallback.

Daily option selection only:

```powershell
python scripts/daily_NIFTY/daily_option_selection.py --trade-date 2026-06-25 --model-version cascade_v1
```

The persisted row includes the selected buy-leg token/symbol, entry reference, two target prices, and optional stop-loss fields. Stop loss is disabled unless `--stop-loss-pct` is supplied.

## Option OHLC

Daily option OHLC is stored in `OptionOhlc`, separate from snapshot and Greek
tables. Use it when daily candle context is enough; use `OptionSnapshot` and
`OptionSnapshotCalc` when bid/ask spread, intraday labels, or IV/Greeks are
required.

Historical daily option OHLC backfill:

```powershell
python scripts/backfill_NIFTY/backfill_NIFTYoptions_OHLC.py --from-date 2026-04-01 --to-date 2026-06-26 --underlying NIFTY
```

Daily live quote OHLC capture, intended for cron after market close:

```powershell
python scripts/daily_NIFTY/daily_NIFTYoption_OHLC.py --underlying NIFTY
```

Recommended schedule for the daily job is after NSE close, around 15:40 to
15:45 IST. On Render cron, that is 10:10 to 10:15 UTC. Kite access token refresh
must run earlier in the day.

Kite historical option OHLC is not guaranteed for expired contracts. During the
April-June 2026 backfill, Kite returned usable daily OHLC only for a subset of
June dates/contracts, while many expired option tokens returned no candles or
`invalid token`.

## Flask Dashboard

`flask_app.py` is a DB-first review surface. It shows a stock dropdown and, for NIFTY 50, a June table joining:

- predicted direction from `NiftyPrediction`;
- realized `actual_trade_label`;
- selected option and target plan from `NiftyOptionSelection`;
- option snapshot-derived P&L/status for the next trade date.

## Research Boundaries

- `backtest/research/build_experiment.py` may test unpromoted strategy families.
- `src/news_sentiment/` produces sentiment data for residual research only.
- Generated files under `output/` are local artifacts and should not be committed.
- Legacy CSV backtest runners stay under `backtest/legacy/` and are not part of the production path.

## Validation

```powershell
python -m pytest backtest/test_optionselection_e2e.py backtest/test_cascade_option_signal_mapper.py backtest/test_underlying_prediction.py
python -m pytest backtest/test_news_sentiment.py
```
