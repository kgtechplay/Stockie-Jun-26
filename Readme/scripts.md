# Scripts Documentation

This document describes the scripts currently present in `scripts/` and how they map to the running codebase.

## Active scripts

### `scripts/get_kite_access_token.py`

Purpose:
- Generates a Zerodha Kite access token from a login `request_token`.
- Saves the token to file (`KITE_ACCESS_TOKEN_PATH`) and attempts to persist it to DB.

Usage:
```bash
python scripts/get_kite_access_token.py
```

Optional non-interactive style:
```bash
python scripts/get_kite_access_token.py "http://127.0.0.1/?request_token=...&status=success"
```

Required env:
- `KITE_API_KEY`
- `KITE_API_SECRET`
- `KITE_ACCESS_TOKEN_PATH` (optional; default `kite_access_token.txt`)
- `AZURE_SQL_CONN_STR` (optional for DB token save)

---

### `scripts/backfill_nifty_underlying.py`

Purpose:
- Backfills `dbo.UnderlyingSnapshot` (daily OHLC/volume) and `dbo.UnderlyingCandle5m` for NIFTY/BANKNIFTY.
- Uses Kite historical data and performs upsert behavior.

Usage:
```bash
python scripts/backfill_nifty_underlying.py
```

Required env:
- `KITE_API_KEY`
- valid access token via `KITE_ACCESS_TOKEN_PATH`
- `AZURE_SQL_CONN_STR`

---

### `scripts/backfill_nifty_options.py`

Purpose:
- Backfills option snapshots for NIFTY/BANKNIFTY from historical candles.
- Calculates IV/Greeks and writes snapshot rows linked to `OptionInstrument`.

Usage:
```bash
python scripts/backfill_nifty_options.py
```

Required env:
- `KITE_API_KEY`
- valid access token
- `AZURE_SQL_CONN_STR`

---

### `scripts/backfill_nifty_volumeproxy.py`

Purpose:
- Downloads NSE FO UDiFF bhavcopy data.
- Upserts near-month index futures activity metrics into `dbo.MarketActivityDaily`.
- Used as volume/OI proxy input for prediction datasets.

Usage:
```bash
python scripts/backfill_nifty_volumeproxy.py
```

Required env:
- `AZURE_SQL_CONN_STR`

## Legacy script in folder

### `scripts/backfill_nifty_marketsnapshot[DoNotUse].py`

- This file is explicitly marked legacy.
- Do not use it for the current pipeline unless you intentionally maintain old flows.

## Relationship to API

For day-to-day refreshes in the live app, the primary path is:
- `POST /api/options/process` (backend pipeline in `src/options_service.py`)

Backfill scripts are primarily for historical loading and dataset recovery.
