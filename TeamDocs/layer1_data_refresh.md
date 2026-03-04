# Layer 1 - Data Refresh (Current)

## What this layer does
- Runs data refresh/backfill for `NIFTY` and `BANKNIFTY`.
- Executes all three backfill components in one API call:
  - underlying daily + 5m candles
  - option snapshots
  - volume proxy (MarketActivityDaily)
- Exposes available data date ranges for UI/API checks.

## Where code lives now
- API routes: `api.py`
  - `POST /api/backfill/nifty`
  - `POST /api/backfill/banknifty`
  - `GET /api/backfill/range/underlying?underlying=NIFTY|BANKNIFTY`
  - `GET /api/backfill/range/options?underlying=NIFTY|BANKNIFTY`
- Orchestration service:
  - `src/backfill/index_backfill_service.py`
- Backfill executors:
  - `scripts/backfill_nifty_underlying.py` (`run_backfill_underlying`)
  - `scripts/backfill_nifty_options.py` (`run_backfill_options`)
  - `scripts/backfill_nifty_volumeproxy.py` (`run_backfill_volumeproxy`)

## Where teammates should update
- Add/change API contract: `api.py`
- Change flow order or component wiring: `src/backfill/index_backfill_service.py`
- Change fetch/upsert logic: corresponding script file above

## Input contract
- Backfill body:
  - `start_date` (YYYY-MM-DD)
  - `end_date` (YYYY-MM-DD)

## Quick checks
- `python -m py_compile api.py src/backfill/index_backfill_service.py`
- Hit one backfill endpoint with a short date range and verify `result.components` has all 3 sections.
