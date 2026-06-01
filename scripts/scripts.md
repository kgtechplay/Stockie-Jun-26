# Scripts Reference
## Gist

Daily refresh order:

```text
Kite token -> stocks universe -> option instruments -> market refresh
```

Use the wrapper for the normal daily job:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_daily_watched_refresh.ps1
```

Schedule it at 8:30 AM IST after the Kite token is available.

All scripts live in `scripts/` and are run from the project root.

Required env vars in `.env`:

- `KITE_API_KEY`, `KITE_API_SECRET`
- One database connection: `SUPABASE_CONN_STR` with `DATABASE_PROVIDER=supabase`, or `AZURE_SQL_CONN_STR`
- Optional local cache path: `KITE_ACCESS_TOKEN_PATH`

Do not configure a rotating `KITE_ACCESS_TOKEN` env var for cron jobs. The shared
`KiteClient` reads the latest access token from the configured database table
`KiteAccessToken` and only falls back to `KITE_ACCESS_TOKEN_PATH` as a local cache.

## Daily Scripts

Run these every trading day, in order. This is the correct watched-instrument refresh flow:

1. Get/refresh Kite access token.
2. Refresh `stocks_universe.csv`.
3. Refresh `OptionInstrument`.
4. Refresh market data for N-1:
   - `UnderlyingSnapshot`
   - `UnderlyingCandle5m`
   - `OptionSnapshot`
   - `OptionSnapshotCalc`

At 8:30 AM IST, `daily_market_refresh.py` defaults to yesterday because market close has not happened yet. That means it updates N-1 by default.

### 1. `daily_get_kite_access_token.py`

Get a fresh Kite access token before market open.

```bash
python scripts/daily/daily_get_kite_access_token.py
python scripts/daily/daily_get_kite_access_token.py "http://127.0.0.1/?request_token=...&status=success"
```

Saves the token to `KITE_ACCESS_TOKEN_PATH` and persists it to the configured database
table `KiteAccessToken`. With `DATABASE_PROVIDER=supabase` or `SUPABASE_CONN_STR`,
this writes to Supabase.

Local token-file writes are best-effort only. On Render or another cron host,
the script can update the database even if `KITE_ACCESS_TOKEN_PATH` is absent or
the local filesystem is ephemeral.

### 2. `daily_fetch_stocks_universe.py`

Refresh `stocks_universe.csv` from Kite instrument data + NSE constituent lists. Run after the access token step so Kite credentials are available. yfinance is skipped by default (fast, ~30 s); pass `--yfinance` for a periodic deep refresh that also covers small-cap sectors.

```bash
python scripts/daily/daily_fetch_stocks_universe.py
python scripts/daily/daily_fetch_stocks_universe.py --yfinance    # also enrich via Yahoo Finance (slow)
python scripts/daily/daily_fetch_stocks_universe.py --fo-only     # only F&O-enabled stocks + indices
```

### 3. `daily_optionInstrument_refresh.py`

Sync `dbo.OptionInstrument` from the live Kite NFO instrument list for all active `WatchedInstrument` rows. Run once per day before the market data refresh.

```bash
python scripts/daily/daily_optionInstrument_refresh.py
python scripts/daily/daily_optionInstrument_refresh.py --type INDEX
python scripts/daily/daily_optionInstrument_refresh.py --type STOCK
python scripts/daily/daily_optionInstrument_refresh.py --dry-run
```

### 4. `daily_market_refresh.py`

Main daily data refresh. Delegates to `BackfillService` and covers:

- `dbo.UnderlyingSnapshot` - daily OHLC for all WatchedInstruments
- `dbo.UnderlyingCandle5m` - 5-minute candles for all WatchedInstruments
- `dbo.OptionSnapshot` + `dbo.OptionSnapshotCalc` - 2 snapshots/day at 09:15 and 15:15 for all option instruments

```bash
# Default: refreshes yesterday, or today if after 15:35 IST. Uses 1-day lookback.
python scripts/daily/daily_market_refresh.py

# Explicit date range
python scripts/daily/daily_market_refresh.py --start 2026-05-08 --end 2026-05-09

# Extend lookback to cover missed days
python scripts/daily/daily_market_refresh.py --lookback 5

# Specific underlyings only
python scripts/daily/daily_market_refresh.py --underlying NIFTY --underlying RELIANCE
```

## Render Cron Jobs

Render cron schedules are UTC. Use `30 1 * * *` for a 7:00 AM IST token
refresh.

Token refresh cron:

```bash
python scripts/daily/daily_get_kite_access_token.py
```

Build command:

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

If Chromium needs OS packages on the Render image, use:

```bash
pip install -r requirements.txt && python -m playwright install --with-deps chromium
```

Required Render env vars for the token cron:

```text
DATABASE_PROVIDER=supabase
SUPABASE_CONN_STR=...
KITE_API_KEY=...
KITE_API_SECRET=...
KITE_AUTO_LOGIN=1
KITE_LOGIN_HEADLESS=1
KITE_USER_ID=...
KITE_PASSWORD=...
KITE_TOTP_SECRET=...
```

`KITE_LOGIN_HEADLESS` must be `1` on Render. Render cron jobs do not have an
X server/display, so headed browser mode fails at launch.

Do not set `KITE_ACCESS_TOKEN` on Render cron jobs. The token cron writes the
new access token to Supabase, and the other cron jobs read it from
`KiteAccessToken`.

## Single Daily Job

Use this wrapper for the non-interactive daily refresh sequence:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_daily_watched_refresh.ps1
```

It runs:

```text
daily_fetch_stocks_universe.py
daily_optionInstrument_refresh.py
daily_market_refresh.py --lookback 1
```

Kite token refresh is special because Zerodha/Kite requires browser login and a daily `request_token`. Run this manually before the scheduled job:

```powershell
python scripts/daily/daily_get_kite_access_token.py
```

If you already have the full redirect URL, you can include it in the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_daily_watched_refresh.ps1 -TokenRedirectUrl "http://127.0.0.1/?request_token=...&status=success"
```

To backfill missed calendar days in the same job:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_daily_watched_refresh.ps1 -Lookback 3
```

## Schedule At 8:30 AM IST

On Windows, create one Task Scheduler job from PowerShell:

```powershell
schtasks /Create /TN "OT Daily Watched Refresh" /SC DAILY /ST 08:30 /TR "powershell -ExecutionPolicy Bypass -File C:\Cursor_Github\OT_v1\scripts\run_daily_watched_refresh.ps1" /F
```

Important:

- Windows Task Scheduler uses the machine's local timezone. If this machine is set to IST, `/ST 08:30` means 8:30 AM IST.
- If the machine is not set to IST, convert 8:30 AM IST to that machine's local time.
- The task assumes today's Kite token already exists. Do the token login manually before 8:30 unless you pass `-TokenRedirectUrl`.
- Keep the machine awake and connected to the network.

To test the scheduled command manually:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Cursor_Github\OT_v1\scripts\run_daily_watched_refresh.ps1
```

## Backfill

Historical data loading is handled by `BackfillService` (`src/services/backfill_service.py`).

`daily_market_refresh.py` is the CLI entry point for both daily and historical backfill. Pass a wider `--start` / `--end` range.

```bash
python scripts/daily/daily_market_refresh.py --start 2025-01-01 --end 2026-05-09
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

### `daily_fetch_stocks_universe.py`

Refresh `stocks_universe.csv` from Kite and NSE constituent data.

```bash
python scripts/daily/daily_fetch_stocks_universe.py
python scripts/daily/daily_fetch_stocks_universe.py --yfinance
python scripts/daily/daily_fetch_stocks_universe.py --fo-only
```

CSV columns: `tradingsymbol, exchange, name, instrument_token, segment, tick_size, lot_size, instrument_type, sector, industry, is_fo_enabled, is_active`.

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

## Flask App

Run the lightweight local app:

```powershell
python flask_app.py
```

The app has two paths:

- Technical Analysis: select an active watched stock/index from `WatchedInstrument`, predict today, or generate/backtest the last 60 days.
- News Signal Backtest: backtest existing `trade_signal_journal.csv` rows for a selected published date. News prediction is disabled for now.
