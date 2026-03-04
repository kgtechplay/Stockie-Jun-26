# OT-v1 - Options Trading Analytics Platform

OT-v1 is a full-stack system for options analytics on Indian indices (NIFTY and BANKNIFTY), built with a Flask API backend, Azure SQL storage, and a Flutter web frontend.

## What the project does

- Ingests market instruments and quote data from Zerodha Kite Connect.
- Stores option contracts and snapshots in SQL tables.
- Computes implied volatility and Greeks (delta, gamma, theta, vega) using Black-Scholes.
- Exposes APIs for stock search, option chain refresh, latest chain view, trend charts, and prediction workflows.
- Runs index-direction prediction and backtesting pipelines and exports CSV/XLSX outputs.

## Repository structure

```text
OT_v1/
  api.py                         # Flask app entry point + API routes + Flutter static hosting
  scripts/run_local.py           # Local Flask runner
  requirements.txt               # Python dependencies
  src/
    core/config.py               # Environment-driven settings
    core/logging_config.py
    domain/models.py             # Dataclasses for stocks/options/snapshots
    integrations/kite_client.py  # Kite API wrapper
    fetchers/stock_fetcher.py    # Instrument filtering for stocks/indices
    fetchers/option_fetcher.py   # Option filtering + IV/Greeks calculations
    services/stock_search.py     # Interactive symbol lookup helper
    services/options_service.py  # End-to-end option refresh pipeline
    services/trend_service.py    # Historical option trend retrieval
    cli/main.py                  # CLI entry point
    data/db_client.py            # Azure SQL access layer

  src/prediction/
    prediction_service.py        # Prediction orchestration service
    contracts.py                 # Shared prediction/news/event dataclasses
    strategies/                  # one-file-per-strategy + registries
    aggregator/index_aggregator.py
    aggregator/option_aggregator.py
    underlying_data_provider.py
    options_data_provider.py

  src/backtest/
    index/index_backtest.py
    e2e_backtest.py

  output/                        # Generated prediction/backtest files

  predictions_backup/            # Legacy scripts kept only as reference

  scripts/
    get_kite_access_token.py
    backfill_nifty_underlying.py
    backfill_nifty_options.py
    backfill_nifty_volumeproxy.py
    backfill_nifty_marketsnapshot[DoNotUse].py

  scripts_Daily/                 # Legacy scheduler scripts (marked [DoNotUse])

  flutter_app/
    lib/main.dart                # Stock search + option chain screen
    lib/trend_view_screen.dart   # Trend chart screen
    lib/prediction_test_screen.dart

  Readme/
    README.md                    # Primary project documentation
    LOCAL_TESTING.md             # Local API + Flutter testing steps
    agents.md                    # Agent module implementation reference
    scripts.md                   # Data/token/backfill scripts
```

## Prerequisites

- Python 3.10+
- Flutter SDK (for web UI)
- ODBC Driver for SQL Server (17 or 18)
- Zerodha Kite Connect credentials
- Azure SQL database (or compatible SQL Server schema)

## Environment variables

Create `.env` in repository root:

```bash
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret
KITE_ACCESS_TOKEN_PATH=kite_access_token.txt

AZURE_SQL_CONN_STR=DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=...;Encrypt=yes;TrustServerCertificate=no;

TARGET_UNDERLYINGS=NIFTY,BANKNIFTY
```

## Agentic Prediction (MVP)

Agentic aggregation is optional and disabled by default.

- `USE_AGENTIC_AGGREGATOR=1`
  - Enables the new index aggregator that combines TA + event + news signals.
- `NEWS_SAMPLE_JSON_PATH=/absolute/or/relative/path/to/news_sample.json`
  - Optional local sample input for the News Agent. If omitted, news input is empty.

Default behavior remains TA-only when `USE_AGENTIC_AGGREGATOR` is not set to `1`.

## Quick start

1. Install backend dependencies:
```bash
pip install -r requirements.txt
```

2. Generate Kite access token:
```bash
python scripts/get_kite_access_token.py
```

3. Start backend:
```bash
python scripts/run_local.py
```

4. Start Flutter web app (new terminal):
```bash
cd flutter_app
flutter pub get
flutter run -d chrome
```

## API endpoints

### Health
- `GET /api/health`

### Stocks
- `GET /api/stocks/count`
- `POST /api/stocks/search`
  - body: `{ "query": "nifty", "segment": "INDICES" }`
  - `segment` optional: `NSE`, `BSE`, `INDICES`

### Options
- `POST /api/options/process`
  - body: `{ "tradingsymbol": "NIFTY" }`
  - fetches latest contracts, quotes, IV/Greeks, and writes snapshots
- `GET /api/options/latest?tradingsymbol=NIFTY`
- `GET /api/options/trend?option_instrument_id=123&days=30`

### Predictions
- `GET /api/predictions/strategies`
- `POST /api/predictions/run`
  - body: `{ "instrument": "NIFTY", "strategies": ["MaTrend_001"] }`
- `POST /api/predictions/backtest`
  - body: `{ "instrument": "NIFTY" }`
- `POST /api/predictions/backtest/e2e`
  - body: `{ "instrument": "NIFTY" }`
- `GET /api/predictions/files?instrument=NIFTY`
- `GET /api/predictions/files/download?file=<filename>`

### Backfill
- `POST /api/backfill/nifty`
- `POST /api/backfill/banknifty`
- `GET /api/backfill/range/underlying?underlying=NIFTY|BANKNIFTY`
- `GET /api/backfill/range/options?underlying=NIFTY|BANKNIFTY`

## Database schema checklist

Use this as a readiness checklist before running each feature area.

### Core option chain refresh (`/api/options/process`)
- `dbo.OptionInstrument`
  - required fields used by code: `id`, `instrument_token`, `underlying`, `exchange`, `tradingsymbol`, `strike`, `expiry`, `instrument_type`, `lot_size`, `tick_size`, `segment`
- `dbo.OptionSnapshot`
  - required fields used by code: `id`, `option_instrument_id`, `snapshot_time`, `underlying_price`, `last_price`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`, `volume`, `open_interest`
- `dbo.OptionSnapshotCalc`
  - required fields used by code: `option_snapshot_id`, `implied_volatility`, `delta`, `gamma`, `theta`, `vega`

### Stock search APIs (`/api/stocks/*`)
- `dbo.StockDB`
  - required fields used by code: `exchange`, `tradingsymbol`, `name`, `instrument_token`, `segment`, `tick_size`, `lot_size`

### Option trend API (`/api/options/trend`)
- `dbo.OptionInstrument`
- `dbo.OptionSnapshot`
- `dbo.OptionSnapshotCalc`
  - trend endpoint reads historical snapshots + Greeks by `option_instrument_id`

### Prediction generation (`src/prediction/prediction_service.py`)
- `dbo.UnderlyingSnapshot`
  - required fields: `underlying`, `trade_date`, `open_price`, `high_price`, `low_price`, `close_price`, `volume`
- `dbo.MarketActivityDaily` (optional but supported via join)
  - used fields: `underlying`, `trade_date`, `fin_instrm_tp`, `tckr_symb`, `expiry_date`, `close_price`, `settle_price`, `underlying_price`, `open_interest`, `change_in_oi`, `traded_volume`, `traded_value`

### Index backtest (`src/backtest/index/index_backtest.py`)
- `dbo.UnderlyingSnapshot`
- `dbo.UnderlyingCandle5m`
  - required fields: `underlying`, `trade_date`, `low_price`, `high_price`

### Option selection/backtest (`src/prediction/aggregator/option_aggregator.py`, `src/backtest/e2e_backtest.py`)
- `dbo.OptionInstrument`
- `dbo.OptionSnapshot`
- `dbo.OptionSnapshotCalc`
- `dbo.UnderlyingSnapshot`
- `dbo.MarketActivityDaily`

### Kite token persistence (optional DB fallback)
- token table used by `db_client` token methods (if configured in your DB)
  - code falls back to file token if DB token operations are unavailable

## Prediction workflow

1. Run predictions (API):
```bash
curl -X POST http://localhost:5000/api/predictions/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","strategies":["MaTrend_001"],"use_agentic":true}'
```

2. Backtest index predictions (API):
```bash
curl -X POST http://localhost:5000/api/predictions/backtest \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY"}'
```

Generated files are written to `output/`.

## Documentation map

- `Readme/scripts.md`: data and token scripts
- `Readme/agents.md`: prediction agents (events/news/impact scoring) implementation reference
- `Readme/LOCAL_TESTING.md`: local backend/frontend validation

## Notes

- `scripts_Daily/*[DoNotUse].*` are legacy scheduler helpers and not part of the active flow.
- `api.py` can serve Flutter static build (`flutter_app/build/web`) in deployed environments.
