# Layer 5 - UI (Current)

## What this layer does
- Provides UI actions for:
  - backfill execution + range display
  - prediction runs (selected or all strategies)
  - index and e2e backtest execution
  - summary display (accuracy/recall/option metrics)
  - output file download

## Where code lives now
- Main prediction workflow screen:
  - `flutter_app/lib/prediction_test_screen.dart`
- App shell/nav:
  - `flutter_app/lib/main.dart`
- Backend endpoints used by UI:
  - backfill: `/api/backfill/*`
  - predictions: `/api/predictions/run`
  - backtests: `/api/predictions/backtest`, `/api/predictions/backtest/e2e`
  - files: `/api/predictions/files*`

## Where teammates should update
- UI controls/sections/messages: `prediction_test_screen.dart`
- API payload shape consumed by UI: update both `api.py` and `prediction_test_screen.dart`
- Navigation/page placement: `main.dart`

## Quick checks
- Manual workflow: backfill -> run predictions -> run backtest -> summary cards -> file download.
- Verify empty strategy selection triggers all-strategy run behavior.
