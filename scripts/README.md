# Scripts

Active NIFTY pipeline scripts are split into daily jobs, backfill jobs, and shared common jobs.

## Daily NIFTY

- `daily_NIFTY/daily_get_kite_access_token.py` - refresh Kite access token.
  - `python scripts/daily_NIFTY/daily_get_kite_access_token.py`
- `daily_NIFTY/daily_market_refresh.py` - fetch daily underlying OHLC and update `SignalFeatureDaily`.
  - `python scripts/daily_NIFTY/daily_market_refresh.py --underlying NIFTY`
  - `python scripts/daily_NIFTY/daily_market_refresh.py --underlying ALL`
- `daily_NIFTY/daily_optionInstrument_refresh.py` - refresh active NIFTY option instruments.
  - `python scripts/daily_NIFTY/daily_optionInstrument_refresh.py --underlying NIFTY`
- `daily_NIFTY/daily_NIFTYoption_snapshot.py` - fetch live NIFTY option snapshots and calculate greeks.
  - `python scripts/daily_NIFTY/daily_NIFTYoption_snapshot.py`

## Backfill NIFTY

- `backfill_NIFTY/backfill_underlying.py` - backfill underlying OHLC and update `SignalFeatureDaily`.
  - `python scripts/backfill_NIFTY/backfill_underlying.py --underlying NIFTY --start 2026-01-01 --end 2026-06-16`
- `backfill_NIFTY/backfill_NIFTYoptions_from_historical.py` - backfill NIFTY option snapshots from Kite historical candles and calculate greeks.
  - `python scripts/backfill_NIFTY/backfill_NIFTYoptions_from_historical.py --underlying NIFTY --start 2026-01-01 --end 2026-06-16`
- `backfill_NIFTY/backfill_nifty_volume.py` - backfill NIFTY near-month futures volume from NSE FO bhavcopy into `UnderlyingSnapshot` and recompute `SignalFeatureDaily` volume windows.
  - `python scripts/backfill_NIFTY/backfill_nifty_volume.py --start 2026-01-01 --end 2026-06-17`

## Common

- `Common/calculate_underlying_features.py` - write underlying technical features to `SignalFeatureDaily`.
  - `python scripts/Common/calculate_underlying_features.py --underlying NIFTY --start 2026-01-01 --end 2026-06-16`
- `Common/calculate_option_snapshot_calc.py` - calculate IV/greeks into `OptionSnapshotCalc`.
  - `python scripts/Common/calculate_option_snapshot_calc.py --from-date 2026-01-01 --to-date 2026-06-16`
- `export_db_to_excel.py` - export NIFTY underlying (OHLCV + features) and option snapshot/greeks to two separate Excel files.
  - `python scripts/export_db_to_excel.py` — defaults to 2026-04-01 to today, output in `output/db/`
  - `python scripts/export_db_to_excel.py --start 2026-04-01 --end 2026-06-17 --snapshot-label close`

## Legacy

`legacy/` contains setup, broader-universe, and ad-hoc utilities. It is not required for the NIFTY pipeline, except `populate_watched_instruments.py` may be useful if `WatchedInstrument` needs reseeding.
