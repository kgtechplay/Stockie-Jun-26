# Interpreting Backtests

Backtest artifacts are generated under `output/` for local analysis only and are ignored by git.

## Production Prediction Backtest

Run:

```powershell
python -m src.technical_analysis.cascade.pipeline --underlying NIFTY
```

Main local artifacts:

```text
output/backtest/NIFTY/production/NIFTY_prediction.csv
output/backtest/NIFTY/production/NIFTY_prediction_summary.txt
```

Key columns:

| Column | Meaning |
|---|---|
| `trade_date` | Signal date. |
| `next_trade_date` | Next trading row used for grading when available. |
| `final_prediction` / `direction` | `CALL`, `PUT`, or `NO_POSITION`. |
| `regime` / `volatility_regime` | Calm/stress router used by the cascade. |
| `primary_strategy` | Winning promoted strategy when a side is selected. |
| `strategy_precision` | Historical precision for the winning side in that regime. |
| `strength_score` / `strength_label` | Option-facing strength from YAML configuration. |
| `confidence_level` | Winning strategy precision exposed to option selection. |
| `actual_trade_label` | Realized label once the next-day outcome exists. |

## Option Selection Backtest

Run the test/backtest surface:

```powershell
python backtest/test_optionselection_e2e.py --prediction-source db --model-version cascade_v1
```

For daily-grain option OHLC, use `OptionOhlc` instead of adding rows to
`OptionSnapshot`. The OHLC table is populated by the historical and daily option
OHLC scripts and is separate from IV/Greek calculations.

Current production option-selection rules choose long ITM calls/puts only:

- Bullish CALL: ITM CE, delta 0.70 to 0.90, 20 to 60 DTE.
- Bearish PUT: ITM PE, delta -0.90 to -0.70, 20 to 60 DTE.
- Base filters check spread, liquidity, theta burn, IV outliers, and positive last price.

For live cron output, `NiftyOptionSelection` is the durable source of truth. It contains the selected option token/symbol, entry reference price, target levels, and optional stop-loss fields.

## Target-Only P&L Convention

The current target-only analysis uses +2% and +3% option-premium targets. Stop loss is disabled unless explicitly configured. Rows without enough future snapshot data should be treated as open/incomplete, not final losses.

## News Sentiment Research

Sentiment has a separate residual research surface under `src/news_sentiment/`. It is not currently wired into production prediction or the option selector.
