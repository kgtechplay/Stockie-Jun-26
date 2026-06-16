# Agent: impactList

## Gist

Map commodity impact into sectors and relevant stocks.

## Purpose

Map commodity impacts from `dailyNews` into impacted sectors and possible stocks.

## Rules

- Preserve the commodity and news event context from `dailyNews`.
- Rank impacted sectors by directness, sensitivity, confidence, and timeline.
- Include example stocks only when the mapping is explainable.
- Do not use future market movement or post-news price action.
