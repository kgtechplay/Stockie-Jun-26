# Run Locally

## Prerequisites

- Python virtual environment activated (`.venv`)
- `.env` file present at repo root (copy from `.env.example` and fill in values)
- Supabase or Azure SQL database reachable
- Kite access token refreshed for today in `KiteAccessToken` (see step below if needed)

## 1. Refresh Kite Access Token (once per trading day)

```powershell
python scripts/daily/daily_get_kite_access_token.py
```

If you already have the redirect URL from the Kite login flow:

```powershell
python scripts/daily/daily_get_kite_access_token.py "http://127.0.0.1/?request_token=...&status=success"
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

## Predict a Stock (Technical Analysis)

Use the **Technical Analysis** tab in the app.

1. Select a stock or index from the dropdown (populated from active `WatchedInstrument` rows).
2. Click **Predict** to run today's direction prediction using all registered strategies.
3. The result shows per-strategy predictions (`CALL`, `PUT`, `NO_POSITION`) and an `aggregate_decision`.

To generate predictions from the CLI directly:

```powershell
python src/services/historical_prediction.py --underlying RELIANCE
```

This writes `output/historical/RELIANCE_prediction.csv` covering the last 60 days.

---

## Backtest a Stock (Technical Analysis)

Use the **Technical Analysis** tab in the app.

1. Select a stock or index.
2. Click **Backtest**. The app will:
   - Generate 60 days of historical predictions if the file does not exist.
   - Enrich each prediction row with next-day market data (`next_open`, `max_high_price`, `min_low_price`).
   - Classify `actual_move` (CALL / PUT / NO_POSITION) and compute `max_delta_pct`.
   - Evaluate each strategy column and write results back to `output/historical/<underlying>_prediction.csv`.
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
CALL        — max_high > next_open × 1.01  AND  min_low > next_open × 0.995
PUT         — min_low  < next_open × 0.99  AND  max_high < next_open × 1.005
NO_POSITION — volatile/mixed/flat day
```

**Profit/stop thresholds (historical backtest):**

```text
profit target = 1%   (next_open ± 1%)
stop loss     = 0.5% (next_open ∓ 0.5%)
```

To run the historical backtest from the CLI:

```powershell
python src/backtest/historical_underlying_backtest.py --underlying RELIANCE
```

---

## News Signal Backtest

Use the **News Signal Backtest** tab in the app.

1. Select a published date from the dropdown.
2. The backtest evaluates rows in `output/trade_signal_journal.csv` against market data for that date.

News prediction pipeline is disabled in the UI. To run the full orchestration pipeline from Python:

```python
from src.services.orchestration_service import OrchestrationService

result = OrchestrationService.default().run()
```

To run the news backtest standalone:

```powershell
python src/backtest/news_underlying_backtest.py --signal-journal-file output/trade_signal_journal.csv
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
Run `python scripts/populate_watched_instruments.py` and ensure `WatchedInstrument` has `is_active = 1` rows.

**No market data for backtest:**
Run the daily market refresh to populate `UnderlyingSnapshot` and `UnderlyingCandle5m`:

```powershell
python scripts/daily/daily_market_refresh.py --lookback 90 --underlying RELIANCE
```
