# Local Testing Guide

This guide validates the current Flask API + Flutter web app locally.

## Prerequisites

- Python 3.10+
- Flutter SDK (for the UI in `flutter_app/`)
- SQL Server ODBC driver
- Valid `.env` in repo root with:
  - `KITE_API_KEY`
  - `KITE_API_SECRET`
  - `KITE_ACCESS_TOKEN_PATH`
  - `AZURE_SQL_CONN_STR`

### Windows note: install Flutter + add to PATH

If you see `flutter is not recognized` (PowerShell) / `flutter: command not found`, Flutter is not installed **or** not on your `PATH`.

- Install Flutter SDK from the official docs: `https://docs.flutter.dev/get-started/install/windows`
- Add Flutter to PATH:
  - Add `<flutter-sdk>\bin` (example: `C:\src\flutter\bin`) to your **User** or **System** `Path`
  - Close/reopen **all** terminals and your IDE after changing PATH
- Verify:

```bash
flutter --version
flutter doctor
```

## 1) Start backend

```bash
pip install -r requirements.txt
python run_local.py
```

Expected base URLs:
- API: `http://localhost:5000/api`
- Health: `http://localhost:5000/api/health`

If port 5000 is already in use on your machine, you can override it:

```bash
$env:PORT=5050  # PowerShell
python run_local.py
```

If you override the backend port, run Flutter with an explicit API base URL:

```bash
cd flutter_app
flutter pub get
flutter run -d chrome --dart-define=API_BASE_URL=http://localhost:5050/api
```

If you see Flutter web errors like `ClientException: Failed to fetch` while `/api/health` works in a terminal,
try using `127.0.0.1` instead of `localhost` (avoids IPv6 `::1` vs IPv4 binding issues on some Windows setups):

```bash
flutter run -d chrome --dart-define=API_BASE_URL=http://127.0.0.1:5050/api
```

## 2) Start Flutter frontend

```bash
cd flutter_app
flutter pub get
flutter run -d chrome
```

`flutter_app/lib/main.dart` auto-detects localhost and uses `http://localhost:5000/api`.

If you don't have Chrome installed, try Edge:

```bash
flutter run -d edge
```

## 3) Validate core APIs

### Health
```bash
curl http://localhost:5000/api/health
```

### Stock search (POST)
```bash
curl -X POST http://localhost:5000/api/stocks/search \
  -H "Content-Type: application/json" \
  -d '{"query":"NIFTY","segment":"INDICES"}'
```

### Option refresh
```bash
curl -X POST http://localhost:5000/api/options/process \
  -H "Content-Type: application/json" \
  -d '{"tradingsymbol":"NIFTY"}'
```

### Latest option chain
```bash
curl "http://localhost:5000/api/options/latest?tradingsymbol=NIFTY"
```

### Prediction strategies
```bash
curl http://localhost:5000/api/predictions/strategies
```

### Run predictions
```bash
curl -X POST http://localhost:5000/api/predictions/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","strategies":["MaTrend_001"]}'
```

### Run prediction backtest
```bash
curl -X POST http://localhost:5000/api/predictions/backtest \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY"}'
```

### List generated files
```bash
curl "http://localhost:5000/api/predictions/files?instrument=NIFTY"
```

## Troubleshooting

- Access token errors: run `python scripts/get_kite_access_token.py`.
- SQL connection errors: verify `AZURE_SQL_CONN_STR` and SQL firewall rules.
- Flutter cannot reach API: ensure backend is running on port 5000.
- CORS: backend already enables `CORS(app)` in `api.py`.
