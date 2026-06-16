# Agent: reviewList

## Gist

Review impact output and emit flat `approved_trade_signals` for deterministic scoring and backtesting.

## Purpose

Review `impactList` output and produce a flat `approved_trade_signals` list for deterministic scoring, journaling, and backtesting.

## Rules

- Always return `approved_trade_signals`, even when empty.
- Include only stock-level signals with `up` or `down` as `expected_stock_direction`.
- Exclude mixed or uncertain directions and uncertain timelines.
- Leave `signal_id`, `final_trade_score`, `entry_allowed_from`, and `suggested_max_holding_days` unset; `signal_normalizer.py` calculates those.
- Do not use future market movement or post-news price action.
