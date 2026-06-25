# Legacy Backtest Runners

Old CSV-based historical backtest scripts live here to keep them out of the
production `src` package tree. They are retained only for historical comparison
work and are not used by the production cron or Flask dashboard.

Run manually with:

```powershell
python backtest/legacy/historical_underlying_backtest.py --underlying NIFTY
```
