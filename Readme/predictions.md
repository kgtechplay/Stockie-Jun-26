# Predictions Module Documentation

## Quick Start - Usage Examples

```bash
# Run all prediction strategies for NIFTY
python predictions/index_predictor.py -u NIFTY

# Run all strategy combinations for NIFTY
python predictions/option_selector.py -u NIFTY

# Run all selection strategies for a specific prediction strategy
python predictions/option_selector.py -u NIFTY -ps trendFollowing

# Run all prediction strategies with a specific selection strategy
python predictions/option_selector.py -u NIFTY -ss nearestExpiryATM

# Run a specific combination (original behavior)
python predictions/option_selector.py -u NIFTY -ps trendFollowing -ss nearestExpiryATM

# Run backtest
python predictions/backtest.py -u NIFTY      # Backtest all NIFTY combinations
```

---

## Overview

The predictions module implements a complete workflow for:
1. **Generating index direction predictions** (CALL/PUT/NO_POSITION) for NIFTY/BANKNIFTY
2. **Selecting optimal option contracts** for each prediction
3. **Backtesting** both predictions and option trades

All output files are saved in `predictions/output/`:
- Prediction files: `{UNDERLYING}_{strategy}_predicted.csv`
- Combined files: `{UNDERLYING}_{predictor_strategy}_{selector_strategy}.csv`

---

## Scripts

### index_predictor.py

Generates daily index direction predictions using multiple strategies.

**Strategies**: `trendFollowing`, `momentum`, `meanReversion`

**Output**: `predictions/output/{UNDERLYING}_{strategy}_predicted.csv`

**Usage**:
```bash
python predictions/index_predictor.py -u NIFTY                    # Run all strategies
python predictions/index_predictor.py -u NIFTY -s trendFollowing  # Run specific strategy
python predictions/index_predictor.py --list-strategies          # List all strategies
```

---

### option_selector.py

Selects optimal option contracts for each CALL/PUT prediction.

**Prediction Strategies**: `trendFollowing`, `momentum`, `meanReversion`  
**Selection Strategies**: `nearestExpiryATM`, `nearestExpiryHighOI`, `highestDeltaPriceRatio`

**Output**: `predictions/output/{UNDERLYING}_{predictor_strategy}_{selector_strategy}.csv`

**Usage**:
```bash
python predictions/option_selector.py -u NIFTY                                    # Run all combinations
python predictions/option_selector.py -u NIFTY -ps trendFollowing                   # All selectors for one predictor
python predictions/option_selector.py -u NIFTY -ss nearestExpiryATM                 # All predictors for one selector
python predictions/option_selector.py -u NIFTY -ps trendFollowing -ss nearestExpiryATM  # Specific combination
python predictions/option_selector.py --list-strategies                             # List all strategies
```

**Prerequisites**: Run `index_predictor.py` first to create prediction files.

---

### backtest.py

Automatically backtests all strategy combination files for a given underlying.

**Usage**:
```bash
python predictions/backtest.py -u NIFTY      # Backtest all NIFTY combinations
python predictions/backtest.py -u BANKNIFTY   # Backtest all BANKNIFTY combinations
```

**Output**: Updates CSV files with backtest results and displays summary metrics:
- Index prediction accuracy
- Option selector accuracy (when index was correct)
- Net profit

**Prerequisites**: Run `index_predictor.py` and `option_selector.py` first.
