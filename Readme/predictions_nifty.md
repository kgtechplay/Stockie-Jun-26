# Index Predictions Module Documentation

## Quick Start - Usage Examples

```bash
# Run all prediction strategies for NIFTY
python predictions/index_predictor.py -u NIFTY

# Run a specific prediction strategy
python predictions/index_predictor.py -u NIFTY -s trendFollowing

# List all available strategies
python predictions/index_predictor.py --list-strategies

# Backtest index predictions
python predictions/backtest_index_prediction.py -u NIFTY      # Backtest all NIFTY predictions
python predictions/backtest_index_prediction.py -u BANKNIFTY   # Backtest all BANKNIFTY predictions
```

---

## Overview

The index predictions module implements a workflow for:
1. **Generating index direction predictions** (CALL/PUT/NO_POSITION) for NIFTY/BANKNIFTY
2. **Backtesting** index predictions to measure accuracy

All output files are saved in `predictions/output/`:
- Prediction files: `{UNDERLYING}_{strategy}_predicted.csv`
- Comparison Excel: `{UNDERLYING}_index_comparison.xlsx`

---

## Scripts

### index_predictor.py

Generates daily index direction predictions using multiple strategies.

**Strategies**: `trendFollowing`, `momentum`, `meanReversion`

**Output**: `predictions/output/{UNDERLYING}_{strategy}_predicted.csv`

Each prediction file contains:
- `date`: Trading date for the prediction
- `prediction`: Direction prediction (CALL/PUT/NO_POSITION)

**Usage**:
```bash
python predictions/index_predictor.py -u NIFTY                    # Run all strategies
python predictions/index_predictor.py -u NIFTY -s trendFollowing  # Run specific strategy
python predictions/index_predictor.py -u BANKNIFTY                 # Run all strategies for BANKNIFTY
python predictions/index_predictor.py --list-strategies            # List all strategies
```

**How it works**:
- Fetches historical index data from the database
- Applies prediction strategy logic to generate direction signals
- Creates one prediction per trading day
- Saves results to CSV files

---

### backtest_index_prediction.py

Backtests index prediction logic by comparing predictions against actual market movements.

**Usage**:
```bash
python predictions/backtest_index_prediction.py -u NIFTY      # Backtest all NIFTY predictions
python predictions/backtest_index_prediction.py -u BANKNIFTY   # Backtest all BANKNIFTY predictions
```

**Output**: 
- Updates prediction CSV files with backtest columns
- Generates comparison Excel file: `{UNDERLYING}_index_comparison.xlsx`

**Backtest Columns Added**:
- `today_close_1515`: Close price on prediction date
- `next_date`: Next trading day date
- `next_open_0915`: Open price on next trading day
- `next_close_1515`: Close price on next trading day
- `next_day_move_pct`: Percentage gap move (next_open - today_close) / today_close
- `predicted_max_delta`: Maximum potential profit percentage based on intraday high/low
  - For CALL: `(highest_next_day_price - today_close) / today_close`
  - For PUT: `(today_close - lowest_next_day_price) / today_close`
- `result`: CORRECT, INCORRECT, MISSED_CALL, MISSED_PUT, OK_NO_TRADE, or N/A

**Correctness Logic**:
- **CALL**: CORRECT if `predicted_max_delta > 0` (price went higher than today's close at some point)
- **PUT**: CORRECT if `predicted_max_delta > 0` (price went lower than today's close at some point)
- **NO_POSITION**: 
  - `MISSED_CALL` if gap move > 1% upward
  - `MISSED_PUT` if gap move < -1% downward
  - `OK_NO_TRADE` if gap move < 1%

**Comparison Excel File**:
The script generates an Excel file with two sheets:
1. **Summary**: Comparison table showing accuracy metrics for each strategy
   - Index Prediction Accuracy (%)
   - Total Predictions, Correct Predictions
   - CALL/PUT breakdown with individual accuracies
   - NO_POSITION count
2. **Detailed Comparison**: Date-by-date comparison showing predictions and results for all strategies side-by-side

**Prerequisites**: Run `index_predictor.py` first to create prediction files.

---

## Workflow

1. **Generate Predictions**:
   ```bash
   python predictions/index_predictor.py -u NIFTY
   ```
   This creates files like:
   - `NIFTY_trendFollowing_predicted.csv`
   - `NIFTY_momentum_predicted.csv`
   - `NIFTY_meanReversion_predicted.csv`

2. **Backtest Predictions**:
   ```bash
   python predictions/backtest_index_prediction.py -u NIFTY
   ```
   This:
   - Updates each prediction CSV with backtest results
   - Generates `NIFTY_index_comparison.xlsx` with summary and detailed comparison

3. **Review Results**:
   - Open the CSV files to see individual prediction results
   - Open the Excel comparison file to compare strategies side-by-side

---

## Data Requirements

The scripts require the following database tables:
- `dbo.UnderlyingSnapshot`: Daily OHLC data for NIFTY/BANKNIFTY
- `dbo.UnderlyingCandle5m`: 5-minute candle data for calculating `predicted_max_delta`

Ensure these tables are populated with historical data before running predictions and backtests.

