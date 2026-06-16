# Run Locally

## Prerequisites

- Python virtual environment activated (`.venv`)
- `.env` file present at repo root (copy from `.env.example` and fill in values)
- Supabase or Azure SQL database reachable
- Kite access token refreshed for today in `KiteAccessToken` (see step below if needed)

## 1. Refresh Kite Access Token (once per trading day)

```powershell
python scripts/daily_NIFTY/daily_get_kite_access_token.py
```

If you already have the redirect URL from the Kite login flow:

```powershell
python scripts/daily_NIFTY/daily_get_kite_access_token.py "http://127.0.0.1/?request_token=...&status=success"
```

The token helper writes the access token to the configured database table
`KiteAccessToken`. Local `kite_access_token.txt` writes are best-effort cache
writes only, so Render/cron runs do not depend on local files.

## 2. Start the Flask App

```powershell
python flask_app.py
```

Open your browser at `http://127.0.0.1:5000`.

---

## NIFTY Technical Dashboard

The Flask app is intentionally NIFTY-only. It shows NIFTY data/trends from
`output/backtest/NIFTY_prediction.csv` and exposes only NIFTY Predict and
Backtest actions.

Click **Predict** to run today's NIFTY direction prediction using all registered
strategies. Click **Backtest** to regenerate the legacy NIFTY CSV backtest.

To generate predictions from the CLI directly:

```powershell
python src/services/historical_prediction.py --underlying NIFTY
```

This writes `output/backtest/NIFTY_prediction.csv` covering the last 60 days.

---

## Backtest NIFTY

Click **Backtest**. The app will:
   - Generate 60 days of historical predictions if the file does not exist.
   - Enrich each prediction row with next-day market data (`next_open`, `max_high_price`, `min_low_price`).
   - Classify `actual_move` (CALL / PUT / NO_POSITION) and compute `max_delta_pct`.
   - Evaluate each strategy column and write results back to `output/backtest/<underlying>_prediction.csv`.
3. A CSV preview and summary metrics are shown in the browser.

**Output columns:**

```text
underlying, date, next_date, today_close, next_open, next_close,
max_high_price, min_low_price, actual_move, max_delta_pct,
aggregate_decision, aggregate_decision_result, detected_regime,
<strategy>, <strategy_result>, ...
```

**actual_move logic:**

```text
CALL        â€” max_high > next_open Ã— 1.01  AND  min_low > next_open Ã— 0.995
PUT         â€” min_low  < next_open Ã— 0.99  AND  max_high < next_open Ã— 1.005
NO_POSITION â€” volatile/mixed/flat day
```

**Profit/stop thresholds (historical backtest):**

```text
profit target = 1%   (next_open Â± 1%)
stop loss     = 0.5% (next_open âˆ“ 0.5%)
```

The old CSV backtest runner is parked under `tests/legacy` so it does not look
like a production package. To run it manually:

```powershell
python tests/legacy/historical_underlying_backtest.py --underlying NIFTY
```

---

## News Signal Backtest

News prediction/backtesting is no longer exposed in the Flask app. To run the
full news orchestration pipeline from Python:

```python
from src.news_analysis_phase2.orchestration_service import OrchestrationService

result = OrchestrationService.default().run()
```

To run the news backtest standalone:

```powershell
python src/news_analysis_phase2/backtest/news_underlying_backtest.py --signal-journal-file output/trade_signal_journal.csv
```

---

## Troubleshooting

**Port 5000 already in use:**

```powershell
netstat -ano | findstr ":5000"
Stop-Process -Id <PID> -Force
python flask_app.py
```

**No stocks in the dropdown:**
Run `python scripts/legacy/populate_watched_instruments.py` and ensure `WatchedInstrument` has `is_active = 1` rows.

**No market data for backtest:**
Run the daily market refresh to populate `UnderlyingSnapshot` and `UnderlyingCandle5m`:

```powershell
python scripts/daily_NIFTY/daily_market_refresh.py --lookback 90 --underlying RELIANCE
```

