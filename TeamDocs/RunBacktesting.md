# Run Backtesting

Three distinct backtest types. Full details in [backtest/README.md](../backtest/README.md).

---

## Type 1 â€” Production Pipeline

Validates the cascade prediction â†’ option selection â†’ simulated PnL chain.

```powershell
# Step 1 â€” regenerate cascade predictions over all history
python -m src.technical_analysis.cascade.pipeline --underlying NIFTY

# Step 2 â€” run option selection on those predictions
python backtest/production/pipeline_backtest_optionselection.py --prediction-source db --model-version cascade_v1

# Step 3 â€” simulate PnL from production signals (date range optional)
python backtest/production/pipeline_backtest_pnl.py --start 2026-06-01
python backtest/production/pipeline_backtest_pnl.py --start 2026-06-01 --end 2026-06-30
```

Outputs under `output/backtest/NIFTY/production/`:
- `NIFTY_prediction.csv` â€” cascade signal per day + `global_gate_reason`
- `production_pnl_trades.csv` â€” trade-by-trade simulated PnL
- `production_pnl_summary.txt` â€” win rate, total PnL, exit breakdown

---

## Type 2 â€” Research Strategy Grid

Tests all cascade strategy variants (promoted + experimental) using ATM option snapshot replay.
Bypasses the precision floor â€” evaluates raw signal edge. See [backtest/vectorbt_research/README.md](../backtest/vectorbt_research/README.md).

```powershell
# All variants, full history from April 2025
python -m backtest.vectorbt_research.strategy_grid --start 2025-04-01

# Single month
python -m backtest.vectorbt_research.strategy_grid --start 2026-06-01 --end 2026-06-30

# Filter to specific strategies by name substring
python -m backtest.vectorbt_research.strategy_grid --variants Momentum,CalmTrend

# With stop-loss
python -m backtest.vectorbt_research.strategy_grid --start 2025-04-01 --stop-loss-pct 0.015
```

Outputs under `output/backtest/NIFTY/vectorbt_research/`:
- `strategy_grid_leaderboard.csv` â€” ranked by total PnL per unit
- `strategy_grid_trades.csv` â€” every trade with entry/exit/PnL
- `strategy_grid_summary.txt` â€” plain-text leaderboard

---

## Type 3 â€” Executed Trades (Paper/Live)

Evaluates actual fills from `PaperTradeResult`. No simulation â€” real entry/exit prices.
See [backtest/README.md â†’ Type 3](../backtest/README.md#type-3--executed-trades-backtesting).

```powershell
# All paper trades on record
python -m backtest.vectorbt_trades.cli

# Filter by execution date range
python -m backtest.vectorbt_trades.cli --start 2026-06-01 --end 2026-06-30

# With fees and slippage on top of fills
python -m backtest.vectorbt_trades.cli --start 2026-06-01 --fees 0.0003 --slippage 0.0005
```

Outputs under `output/backtest/NIFTY/vectorbt/`:
- `vectorbt_trades.csv` â€” trade-level PnL with exit reasons
- `vectorbt_summary.txt` â€” actual lot-based PnL + portfolio metrics

