# Supabase Database Schema

Current NIFTY pipeline tables only.

| Table | Contains |
|---|---|
| `WatchedInstrument` | Active underlyings/instruments to track, including NIFTY and metadata such as token, type, lot size, and active flag. |
| `UnderlyingSnapshot` | Daily OHLCV rows per underlying and trade date. |
| `UnderlyingCandle5m` | Optional 5-minute underlying candles used by legacy/backtest helpers and intraday context. |
| `OptionInstrument` | NIFTY option contract master rows from Kite NFO instruments. |
| `OptionSnapshot` | Raw option quote/candle snapshots by option instrument, trade date, and snapshot label. |
| `OptionSnapshotCalc` | Calculated IV, Greeks, spread, time value, and valuation fields for each option snapshot. |
| `SignalFeatureDaily` | Daily technical features computed from `UnderlyingSnapshot`, such as MA, RSI, ATR, Bollinger, returns, volatility, and regime. |
| `TradingCalendar` | NSE trading-day/expiry calendar, used for date-aware jobs when populated. |
| `KiteAccessToken` | Latest Kite access token used by API jobs. |

Predictions and option selection are computed in-memory from `SignalFeatureDaily` and are not persisted. Strategy logic is still being finalised; see `tests/` for the exercisable entry points.
