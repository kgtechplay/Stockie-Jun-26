# Database Schema

Supabase is the durable source for the NIFTY pipeline.

## Core Tables

| Table | Contains |
|---|---|
| `WatchedInstrument` | Active instruments to track. |
| `TradingCalendar` | Valid NSE sessions and expiry flags. |
| `KiteAccessToken` | Latest Kite access token for cron jobs. |
| `UnderlyingSnapshot` | Daily underlying OHLCV. |
| `UnderlyingCandle5m` | Optional 5-minute underlying candles. |
| `SignalFeatureDaily` | Daily NIFTY technical features. |
| `MacroFactorDaily` | Macro factors, currently India VIX. |
| `GlobalIndexOhlc` | Global index OHLC for risk context. |

## Options

| Table | Contains |
|---|---|
| `OptionInstrument` | Active option contract master rows. |
| `OptionSnapshot` | Raw option quote/snapshot prices. |
| `OptionSnapshotCalc` | IV and Greeks from snapshots. |
| `OptionOhlc` | Daily-grain option OHLC rows. |

## Production

| Table | Contains |
|---|---|
| `NiftyPrediction` | Daily production direction: `CALL`, `PUT`, `NO_POSITION`. |
| `NiftyOptionSelection` | Selected option contract, entry reference, target/stop levels. |

## News Sentiment

| Table | Contains |
|---|---|
| `NewsArticle` | Raw fetched articles. |
| `NewsArticleSentiment` | Per-article sentiment and sector weights. |
| `NiftyMarketSentiment` | Daily pre-market sentiment composite. |

## Paper Trading

| Table | Contains |
|---|---|
| `PaperExecutionSignal` | Option-selection row prepared for paper execution. |
| `PaperOrder` | Simulated entry/exit order records. |
| `PaperTradeResult` | Open/closed paper trade state and P&L. |
| `PaperTradeEvent` | Append-only paper lifecycle events. |

## Migrations

```powershell
Get-ChildItem src/data_manager/db/migrations
```

Most daily jobs defensively create/upgrade required tables through
`src/data_manager/db/supabase_client.py`, but migrations are the schema contract.
