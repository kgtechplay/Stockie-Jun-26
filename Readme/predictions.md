# Predictions Module Documentation

This document provides a quick overview of the prediction system, how `index_predictor.py` and `option_selector.py` work, and the sequence for running all scripts in the `predictions/` folder.

---

## Overview

The predictions module implements a complete workflow for:
1. **Generating NIFTY direction predictions** (CALL/PUT/NO_POSITION)
2. **Selecting optimal option contracts** for each prediction
3. **Backtesting** both predictions and option trades

All scripts work with CSV files in `predictions/output/` directory:
- Prediction files: `{UNDERLYING}_{strategy}_predicted.csv` (e.g., `NIFTY_trendFollowing_predicted.csv`)
- Combined files: `{UNDERLYING}_{predictor_strategy}_{selector_strategy}.csv` (e.g., `NIFTY_trendFollowing_nearestExpiryATM.csv`)

---

## Core Scripts

### index_predictor.py

**Purpose**: Generates daily index direction predictions (NIFTY/BANKNIFTY) based on trend analysis.

**How it works**:
- Fetches historical index daily data (09:15 open, 15:15 close) from `UnderlyingSnapshot` table
- Uses a **10-day rolling window** to analyze price trends
- For each date with sufficient history:
  - Calculates trend percentage: `(last_close - first_close) / first_close`
  - Compares last close to mean close
  - Generates prediction:
    - **CALL**: If trend > 0.3% AND last_close > mean_close (expecting upward move)
    - **PUT**: If trend < -0.3% AND last_close < mean_close (expecting downward move)
    - **NO_POSITION**: Otherwise (no clear trend)

**Output**: Adds/updates `predictions/{UNDERLYING}_predicted.csv` with columns:
- `date`: Prediction date (decision made at 15:15 close)
- `prediction`: "CALL", "PUT", or "NO_POSITION"

**Usage**:
```bash
# Generate predictions for NIFTY with default strategy (trendFollowing)
python predictions/index_predictor.py -u NIFTY

# Generate predictions for BANKNIFTY
python predictions/index_predictor.py -u BANKNIFTY

# Use different prediction strategies
python predictions/index_predictor.py -u NIFTY -s trendFollowing
python predictions/index_predictor.py -u NIFTY -s momentum
python predictions/index_predictor.py -u NIFTY -s meanReversion

# List all available prediction strategies
python predictions/index_predictor.py --list-strategies

# Automatically run option selection for all selector strategies after generating predictions
python predictions/index_predictor.py -u NIFTY -s trendFollowing --auto-option-selection
```

**Output**: Creates/overwrites `predictions/output/{UNDERLYING}_{strategy}_predicted.csv`
- Example: `predictions/output/NIFTY_trendFollowing_predicted.csv`
- Always completely overwrites the file with new predictions

---


### option_selector.py

**Purpose**: Selects the best option contract for each CALL/PUT prediction.

**How it works**:
- Reads predictions from `{UNDERLYING}_predicted.csv`
- For each CALL/PUT prediction without an assigned option:
  - Fetches the full options chain at 15:15 for that date from database
  - Applies selection criteria:
    1. **Filter by side**: Only CALL options for CALL predictions, PUT for PUT
    2. **Expiry check**: Exclude same-day expiry options
    3. **Price check**: Only options with positive price
    4. **Nearest expiry**: Select options expiring soonest
    5. **ATM selection**: Choose strike closest to current underlying price
    6. **Liquidity**: Prefer highest volume, then highest open interest
  - Assigns the selected option details to the prediction row

**Output**: Updates `{UNDERLYING}_predicted.csv` with option columns:
- `option_trade_date`, `option_instrument_token`, `option_tradingsymbol`
- `option_strike`, `option_expiry`, `option_type`
- `selection_option_price_1515`

**Usage**:
```bash
# Select options for NIFTY with default strategies
python predictions/option_selector.py -u NIFTY

# Specify both predictor and selector strategies
python predictions/option_selector.py -u NIFTY -ps trendFollowing -ss nearestExpiryATM
python predictions/option_selector.py -u NIFTY -ps momentum -ss nearestExpiryHighOI
python predictions/option_selector.py -u NIFTY -ps meanReversion -ss highestDeltaPriceRatio

# For BANKNIFTY with different strategy combinations
python predictions/option_selector.py -u BANKNIFTY -ps trendFollowing -ss nearestExpiryATM

# List all available selector strategies
python predictions/option_selector.py --list-strategies
```

**Output**: Creates/updates `predictions/output/{UNDERLYING}_{predictor_strategy}_{selector_strategy}.csv`
- Example: `predictions/output/NIFTY_trendFollowing_nearestExpiryATM.csv`
- Always recomputes option selections for all CALL/PUT predictions

**Prerequisites**: 
- `index_predictor.py` must be run first to create the prediction file
- Database must have option snapshot data at 15:15 for the prediction dates

---

### backtest.py (Combined Backtest)

**Purpose**: Automatically backtests all strategy combination files for a given underlying (NIFTY or BANKNIFTY).

**How it works**:
- Automatically finds all CSV files matching `{UNDERLYING}_{predictionStrategy}_{selectionStrategy}.csv` in `predictions/output/`
- **Only processes files with both prediction and selection strategies** (excludes files like `NIFTY_trendFollowing_predicted.csv`)
- For each file found:
  - Runs index backtest (computes gap moves, prediction accuracy)
    - Inserts index backtest columns between 'prediction' and 'option_trade_date'
  - Runs option backtest (computes P&L for selected option instruments)
    - Inserts option backtest columns after 'selection_option_price_1515'
  - Displays summary metrics for each file:
    - **Index prediction accuracy**: % of CORRECT predictions / total predictions
    - **Option selector accuracy**: % of PROFIT selections / total selections when index was CORRECT
    - **Net profit**: Sum of all `option_pnl_per_lot` values
- Processes all files in a single run

**Usage**:
```bash
# Backtest all NIFTY strategy combination files (both index and option backtest)
python predictions/backtest.py -u NIFTY

# Backtest all BANKNIFTY strategy combination files
python predictions/backtest.py -u BANKNIFTY
```

**Prerequisites**:
- `index_predictor.py` must be run first to create prediction files
- `option_selector.py` must be run to create strategy combination files
- Only files with both prediction and selection strategies will be processed
