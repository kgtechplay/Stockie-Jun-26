# Run Locally

This repo is DB-first for the NIFTY production flow. Local files under `output/`
are developer artifacts only and are ignored by git.

## Prerequisites

- Python virtual environment activated.
- `.env` present at repo root.
- Supabase reachable through `SUPABASE_CONN_STR`.
- Kite access token refreshed for the trading day if running market or option
  snapshot jobs.

Required production environment variables:

```text
DATABASE_PROVIDER=supabase
SUPABASE_CONN_STR=<postgres connection string>
KITE_API_KEY=<kite api key>
KITE_API_SECRET=<kite api secret>
```

## Daily NIFTY Signal Flow

Run upstream refresh jobs first:

```powershell
python scripts/daily_NIFTY/daily_market_refresh.py --underlying NIFTY
python scripts/Common/load_daily_index_data.py --no-local-output
python scripts/daily_NIFTY/daily_optionInstrument_refresh.py --underlying NIFTY
python scripts/daily_NIFTY/daily_NIFTYoption_snapshot.py
```

If the option snapshot job did not calculate IV/Greeks in the deployed flow,
run the calc job for the same date before signal generation:

```powershell
python scripts/Common/calculate_option_snapshot_calc.py --from-date 2026-06-26 --to-date 2026-06-26
```

Then run the cron-friendly signal wrapper:

```powershell
python scripts/daily_NIFTY/daily_nifty_signal.py --model-version cascade_v1
```

The wrapper runs production prediction, persists `NiftyPrediction`, runs option
selection for the latest unresolved prediction, persists `NiftyOptionSelection`,
and prints `FINAL_SIGNAL_JSON=...` with one actionable option token/symbol plus
target levels.

To rerun option selection for an existing prediction row without rerunning the
prediction step:

```powershell
python scripts/daily_NIFTY/daily_nifty_signal.py --skip-prediction --trade-date 2026-06-25 --model-version cascade_v1
```

Stop loss is disabled by default. Enable it explicitly if needed:

```powershell
python scripts/daily_NIFTY/daily_nifty_signal.py --model-version cascade_v1 --stop-loss-pct 0.01
```

## Individual Jobs

Prediction only:

```powershell
python scripts/daily_NIFTY/daily_nifty_prediction.py --model-version cascade_v1
```

Option selection only:

```powershell
python scripts/daily_NIFTY/daily_option_selection.py --trade-date 2026-06-25 --model-version cascade_v1
```

News sentiment is intentionally not wired into production NIFTY prediction yet.
Run it only for research or if a separate cron needs to maintain sentiment data:

```powershell
python scripts/daily_NIFTY/daily_news_sentiment.py --sector-classifier keyword
```

## Flask App

The Flask dashboard reads Supabase directly. It shows a stock dropdown and a
June NIFTY 50 table with predicted direction, actual trade label, option
selection, targets, and option snapshot-derived P&L.

```powershell
python flask_app.py
```

Open `http://127.0.0.1:5000`.

## Validation

```powershell
python -m pytest backtest/test_optionselection_e2e.py backtest/test_cascade_option_signal_mapper.py backtest/test_underlying_prediction.py
python -m pytest backtest/test_news_sentiment.py
```

## Troubleshooting

Port 5000 already in use:

```powershell
netstat -ano | findstr ":5000"
Stop-Process -Id <PID> -Force
```

Missing option selection usually means one of these is absent for the signal
date: `NiftyPrediction`, `OptionInstrument`, `OptionSnapshot`, or
`OptionSnapshotCalc`.