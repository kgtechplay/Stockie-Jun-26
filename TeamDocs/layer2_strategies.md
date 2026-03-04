# Layer 2 - Strategies (Current)

## What this layer does
- Defines strategy logic as one file per strategy.
- Strategy execution is registry-driven from `src/prediction/strategies`.

## Where code lives now
- Strategy package:
  - `src/prediction/strategies/`
- Index strategy files:
  - `index_prediction_<name>.py`
- Option strategy files:
  - `option_selection_<name>.py`
- Shared helpers:
  - `index_prediction_common.py`
  - `option_selection_common.py`
- Registries:
  - `index_registry.py`
  - `option_registry.py`
- Prediction data providers (current canonical location):
  - `src/prediction/underlying_data_provider.py`
  - `src/prediction/options_data_provider.py`

## Strategy file contract
- Index strategy file exports:
  - `STRATEGY_NAME` (string)
  - `predict(window) -> "CALL" | "PUT" | "NO_POSITION"`
- Option strategy file exports:
  - `STRATEGY_NAME` (string)
  - `select(chain_df, prediction, trade_date) -> dict | None`

## Where teammates should update
- Add a new index strategy: add one `index_prediction_*.py` file.
- Add a new option selector: add one `option_selection_*.py` file.
- Change shared signal math/regime logic: `index_prediction_common.py`.
- Change option selection helper behavior: `option_selection_common.py`.

## Quick checks
- `python -m py_compile src/prediction/strategies/*.py`
- Confirm strategy appears in `GET /api/predictions/strategies`.
