# Layer 3 - Aggregators (Current)

## What this layer does
- Loads strategies dynamically from registries.
- Produces:
  - per-strategy index predictions
  - combined index final prediction (majority vote)
  - per-strategy option selections (no combined option decision)

## Where code lives now
- Index aggregation:
  - `src/prediction/aggregator/index_aggregator.py`
- Option aggregation:
  - `src/prediction/aggregator/option_aggregator.py`
- Dynamic sources:
  - `src/prediction/strategies/index_registry.py`
  - `src/prediction/strategies/option_registry.py`

## Where teammates should update
- Change majority-vote behavior: `index_aggregator.py`
- Change per-strategy output shape: `index_aggregator.py` / `option_aggregator.py`
- Change strategy discovery behavior: registry files in `src/prediction/strategies/`

## Quick checks
- Add a dummy strategy file and verify aggregator picks it up.
- Verify deterministic output order (sorted strategy names).
