# Other Scripts

These scripts were moved out of the active NIFTY daily/backfill pipeline during the script cleanup.

Review before using:

- `backfill_nifty200_watched.py`, `backfill_nifty_volumeproxy.py`, `daily_fetch_stocks_universe.py`, `build_trading_calendar.py`, and `populate_watched_instruments.py` are broader universe/setup utilities.

Removed as redundant:

- `daily_underlying_prediction.py`
- `backfill_underlying_predictions.py`
- `run_underlying_prediction_analysis.py`
- `run_option_selection_analysis.py`
- `scripts/Common/analysis_common.py`
- `scripts/Common/underlying_prediction.py`
- `scripts/Common/option_selection.py`
- `tmp_db_check.py`
- `run_daily_watched_refresh.ps1`

Predictions and option selection are computed in-memory; use `tests/` to exercise the pipeline.
