# Supabase Database Schema

Current DB-backed NIFTY pipeline tables.

| Table | Contains |
|---|---|
| `WatchedInstrument` | Active tracked instruments and metadata. |
| `UnderlyingSnapshot` | Daily OHLCV rows per underlying and trade date. |
| `UnderlyingCandle5m` | Optional intraday underlying candles for backtests and diagnostics. |
| `SignalFeatureDaily` | Daily technical features generated from `UnderlyingSnapshot`. |
| `MacroFactorDaily` | Macro factors, currently including India VIX. |
| `GlobalIndexOhlc` | Global index OHLC rows used for cascade global-risk features. |
| `OptionInstrument` | NIFTY option contract master rows from Kite instruments. |
| `OptionSnapshot` | Raw option quote/candle snapshots by contract and snapshot label. |
| `OptionSnapshotCalc` | IV and Greeks calculated from option snapshots. |
| `OptionOhlc` | Daily-grain option OHLC rows, kept separate from quote snapshots and Greeks. |
| `NiftyPrediction` | Production cascade predictions keyed by symbol, trade date, and model version. |
| `NiftyOptionSelection` | Persisted option selection and trade-plan levels for each prediction row. |
| `NewsArticle` | Raw news articles for sentiment research. |
| `NewsArticleSentiment` | Per-article sentiment and sector-weighted rows. |
| `NiftyMarketSentiment` | Daily pre-market NIFTY sentiment composite for research. |
| `TradingCalendar` | NSE trading-day/expiry calendar when populated. |
| `KiteAccessToken` | Latest Kite access token for API jobs. |

## Production Tables

`NiftyPrediction` stores the direction contract used by downstream option
selection:

```text
trade_date, next_trade_date, final_prediction, direction, volatility_regime,
primary_strategy, strategy_precision, signal_style, strength_score,
strength_label, confidence_level, actual_trade_label
```

The latest unresolved row may have `next_trade_date` or outcome fields null until
the following trading session is available.

`NiftyOptionSelection` stores one selected option plan per signal date:

```text
primary_buy_token, primary_buy_symbol, primary_buy_strike, primary_buy_expiry,
primary_buy_option_type, primary_buy_entry_price, primary_buy_iv,
primary_buy_delta, target_1_pct, target_1_price, target_2_pct,
target_2_price, stop_loss_enabled, stop_loss_pct, stop_loss_price
```

The runtime Supabase client defensively creates or upgrades these production
tables, but migration files under `src/data_manager/db/migrations/` remain the
reproducible schema contract.

`OptionOhlc` stores option daily OHLC data independently from `OptionSnapshot`
and `OptionSnapshotCalc`. It is keyed by option instrument, trade date, candle
interval, and data source so historical daily candles and live quote OHLC can
coexist without overwriting snapshot rows:

```text
option_instrument_id, underlying, trade_date, candle_time, candle_interval,
open_price, high_price, low_price, close_price, volume, open_interest,
last_price, exchange_timestamp, data_source, loaded_at
```

Current sources:

- `KITE_HISTORICAL_DAY_OHLC` from Kite historical daily candles.
- `KITE_QUOTE_LIVE_OHLC` from live Kite quote OHLC for the current trading day.

Historical Kite option OHLC availability is limited for expired contracts. In
the June 2026 load, Kite returned rows only for a subset of active/near-active
contracts, so `OptionOhlc` should be treated as a best-available daily OHLC
store rather than a complete historical option universe.

## Research Tables

`GlobalIndexOhlc` is used by production cascade global-risk variants.
`NiftyMarketSentiment` is currently research-only and is not joined into
production NIFTY prediction.