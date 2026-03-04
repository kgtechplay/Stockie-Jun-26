# Layer 4 - Backtesting (Current)

## What this layer does
- Computes backtest results for:
  - each index strategy
  - combined index prediction file
  - option strategy combinations (e2e)
- Produces detailed CSV/XLSX outputs and structured summary payloads for API/UI.

## Where code lives now
- Index backtest:
  - `src/backtest/index/index_backtest.py`
  - summary function: `run_index_backtest_and_collect(...)`
- E2E backtest:
  - `src/backtest/e2e_backtest.py`
  - summary function: `run_e2e_backtest_and_collect(...)`
- API integration:
  - `api.py`
    - `POST /api/predictions/backtest`
    - `POST /api/predictions/backtest/e2e`

## Where teammates should update
- Change index metric formulas: `src/backtest/index/index_backtest.py`
- Change option/e2e metrics: `src/backtest/e2e_backtest.py`
- Change what UI receives from backtest: API handlers in `api.py`

## Quick checks
- Run both backtest APIs and verify `summary` arrays are returned.
- Verify comparison Excel files are still generated in `output/`.
