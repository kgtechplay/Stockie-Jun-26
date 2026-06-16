# News Analysis Phase 2

`src/news_analysis_phase2/` is parked for later. It is not part of the current NIFTY prediction and option-selection pipeline.

What is inside:

| Area | Purpose |
|---|---|
| `dailyNews/` | Reads/news-parses commodity or market-impact events. |
| `impactList/` | Maps news/commodity events to sectors and possible stocks. |
| `reviewList/` | Reviews mapped impacts and emits candidate trade signals. |
| `signal_normalizer.py` | Normalizes reviewed signals into a signal journal shape. |
| `sector_expansion_service.py` | Phase-2 helper to map sectors into watched instruments. |
| `backtest/` | News signal backtest helpers retained for future review. |
| `legacy_agents/` | Older agent prompt/schema files kept only for reference. |

Current status:

- Not used by `scripts/daily_NIFTY`, `scripts/backfill_NIFTY`, or `scripts/Common`.
- Not exposed in `flask_app.py`.
- Backfill inside orchestration is intentionally parked; revisit when phase 2 starts.
