# Scheduler Setup

This repository currently does not include an active daily scheduler script in `scripts/`.

Legacy scheduler helpers exist in `scripts_Daily/` but are marked `[DoNotUse]`.

## Recommended current approach

Use Windows Task Scheduler (or any job runner) to call the live API endpoint twice per trading day.

### Endpoint to schedule

- `POST /api/options/process`
- Body: `{ "tradingsymbol": "NIFTY" }` and `{ "tradingsymbol": "BANKNIFTY" }`

### Example PowerShell command

```powershell
$base = "http://localhost:5000/api/options/process"
Invoke-RestMethod -Method Post -Uri $base -ContentType "application/json" -Body '{"tradingsymbol":"NIFTY"}'
Invoke-RestMethod -Method Post -Uri $base -ContentType "application/json" -Body '{"tradingsymbol":"BANKNIFTY"}'
```

Schedule the above near your desired market snapshot times.

## If you still need legacy scheduler scripts

Legacy files:
- `scripts_Daily/schedule_daily_snapshots[DoNotUse].py`
- `scripts_Daily/setup_scheduler[DoNotUse].py`
- `scripts_Daily/start_scheduler[DoNotUse].bat`
- `scripts_Daily/daily_intraday_stock_option[DoNotUse].py`

These are retained for reference only and are not part of the active documented flow.
