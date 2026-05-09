# Scripts Reference

All scripts live in `scripts/` and are run from the project root.

Required env vars in `.env`:

- `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN_PATH`
- `AZURE_SQL_CONN_STR`

## Daily Scripts

Run these every trading day, in order.

### 1. `daily_get_kite_access_token.py`

Get a fresh Kite access token before market open.

```bash
python scripts/daily_get_kite_access_token.py
python scripts/daily_get_kite_access_token.py "http://127.0.0.1/?request_token=...&status=success"
```

Saves the token to `KITE_ACCESS_TOKEN_PATH` and persists it to `dbo.KiteAccessToken`.

### 2. `daily_optionInstrument_refresh.py`

Sync `dbo.OptionInstrument` from the live Kite NFO instrument list for all active `WatchedInstrument` rows. Run once per day before the market data refresh.

```bash
python scripts/daily_optionInstrument_refresh.py
python scripts/daily_optionInstrument_refresh.py --type INDEX
python scripts/daily_optionInstrument_refresh.py --type STOCK
python scripts/daily_optionInstrument_refresh.py --dry-run
```

### 3. `daily_market_refresh.py`

Main daily data refresh. Delegates to `BackfillService` and covers:

- `dbo.UnderlyingSnapshot` - daily OHLC for all WatchedInstruments
- `dbo.UnderlyingCandle5m` - 5-minute candles for all WatchedInstruments
- `dbo.OptionSnapshot` + `dbo.OptionSnapshotCalc` - 2 snapshots/day at 09:15 and 15:15 for all option instruments

```bash
# Default: refreshes yesterday, or today if after 15:35 IST. Uses 1-day lookback.
python scripts/daily_market_refresh.py

# Explicit date range
python scripts/daily_market_refresh.py --start 2026-05-08 --end 2026-05-09

# Extend lookback to cover missed days
python scripts/daily_market_refresh.py --lookback 5

# Specific underlyings only
python scripts/daily_market_refresh.py --underlying NIFTY --underlying RELIANCE
```

## Backfill

Historical data loading is handled by `BackfillService` (`src/services/backfill_service.py`).

`daily_market_refresh.py` is the CLI entry point for both daily and historical backfill. Pass a wider `--start` / `--end` range.

```bash
python scripts/daily_market_refresh.py --start 2025-01-01 --end 2026-05-09
```

`BackfillService.run_backfill()` automatically classifies instruments into `INDEX` vs `STOCK` and runs the correct underlying and options pipelines for each.

## Setup Scripts

Run once during initial setup or DB migrations.

### `run_migration.py`

Run the SQL migration to create all tables and views. Safe to run multiple times.

```bash
python scripts/run_migration.py
```

Creates: `TradingCalendar`, `WatchedInstrument`, `SignalFeatureDaily`, `SignalPrediction`, `SignalBacktestLabel`, `OptionCandle5m`, `OptionTradePlan`, `OptionPaperTradeResult`, and all views.

### `fetch_stocks_universe.py`

Fetch all NSE equity stocks and indices from Kite, flag F&O eligibility from the live NFO dump, and optionally enrich sector/industry via Yahoo Finance. Writes `stocks_universe.csv` at the project root — same schema as `dbo.WatchedInstrument`.

```bash
# Full universe — sector from NSE constituent lists (fast, no auth, covers Nifty 500+)
python scripts/fetch_stocks_universe.py

# Only F&O-eligible stocks
python scripts/fetch_stocks_universe.py --fo-only

# Also enrich remaining stocks via Yahoo Finance (serial, rate-limited)
python scripts/fetch_stocks_universe.py --yfinance

# Custom output path
python scripts/fetch_stocks_universe.py --output data/my_stocks.csv
```

CSV columns: `tradingsymbol, exchange, name, instrument_token, segment, tick_size, lot_size, instrument_type, sector, industry, is_fo_enabled, is_active`

Sector data sources (in priority order):
1. NSE constituent lists — Nifty 500, Nifty Midcap 150, Nifty Smallcap 250, Nifty Microcap 250. Covers all FO-eligible stocks.
2. Yahoo Finance (`--yfinance`) — serial lookups for stocks not in NSE lists. Requires `pip install yfinance`.

### `populate_watched_instruments.py`

Seed `dbo.WatchedInstrument` with NIFTY/BANKNIFTY from `dbo.StockDB`, and register watched stocks. Run once after migration.

```bash
python scripts/populate_watched_instruments.py
python scripts/populate_watched_instruments.py --list
```

### `build_trading_calendar.py`

Populate `dbo.TradingCalendar` with NSE trading days, holidays, and expiry flags using the `exchange_calendars` library.

```bash
pip install exchange-calendars
python scripts/build_trading_calendar.py --start 2025-01-01 --end 2026-12-31
```

Marks:

- `is_trading_day` - from exchange_calendars XBOM
- `is_weekly_expiry` - every Wednesday for BANKNIFTY and Thursday for NIFTY that is a trading day
- `is_monthly_expiry` - last Thursday of each month that is a trading day

Re-run yearly when NSE publishes the next holiday list.
