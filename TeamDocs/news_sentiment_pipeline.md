# NIFTY News Sentiment Pipeline

Goal: build a daily market-intelligence layer that produces a negative / neutral / positive pre-market NIFTY50 sentiment signal, then test whether it explains next-day NIFTY movement not already explained by the technical cascade.

## Target Architecture

```text
[RSS / NewsAPI]
      ↓  scrape 3:30 PM - 9:00 AM IST
[Article store - headline + summary + timestamp]
      ↓
[FinBERT] -> sentiment label + confidence score
      ↓
[Sector classifier] -> sector tag(s) per article
      ↓
[Weighting engine] -> article_sentiment x NIFTY50 sector weight
      ↓
[Composite signal] -> pre-market NIFTY50 sentiment score
      ↓
[Backtest / trading logic]
```

## Date Semantics

A sentiment row has `target_date`, meaning the market session it is intended to inform. For example, `target_date=2026-06-25` uses news from `2026-06-24 15:30 IST` through `2026-06-25 09:00 IST`. Likewise, `target_date=2026-06-26` uses news from `2026-06-25 15:30 IST` through `2026-06-26 09:00 IST`.

For residual research, join:

- `NIFTY_prediction.next_trade_date` -> `NIFTY_market_sentiment.target_date`

That keeps it aligned with the cascade: a prediction row with `trade_date=2026-06-25` predicts the `next_trade_date=2026-06-26`, and the sentiment window for `target_date=2026-06-26` uses only pre-market information.

## Research Questions

1. Does sentiment predict movement when the technical cascade says `NO_POSITION`?
2. Does sentiment improve precision when it agrees with CALL / PUT?
3. Does sentiment explain residual returns after grouping by `regime` and `final_prediction`?

Residual definition for the first pass:

```text
expected_return = mean(next_return_pct | regime, final_prediction)
residual_return = next_return_pct - expected_return
```

Then test whether `residual_return` varies by sentiment label/score.

## Current Scaffold

Package: `src/news_sentiment/`

- `sources.py`: RSS + NewsAPI collection.
- `article_store.py`: CSV-backed article and enriched-article stores.
- `sentiment.py`: FinBERT adapter using `ProsusAI/finbert` on headline + summary,
  with deterministic fallback if `transformers` / model weights are unavailable.
- `sector_classifier.py`: sector classifier backends over headline + summary:
      BART zero-shot (`facebook/bart-large-mnli`), Azure/OpenAI LLM, or keyword fallback.
- `config.py`: single source of truth for Layer 3 sector labels and Layer 4
      NIFTY50 sector weights.
- `weighting.py`: sector-weighted article sentiment and Layer 5 composite score.
- `pipeline.py`: end-to-end daily signal generation.
- `backtest.py`: residual experiment against `NIFTY_prediction.csv`.

Outputs:

- `output/intelligence/news_articles.csv`
- `output/intelligence/NIFTY_article_sentiment.csv`
- `output/intelligence/NIFTY_market_sentiment.csv`
- `output/intelligence/NIFTY50_constituent_weights.csv`
- `output/intelligence/NIFTY50_sector_weights.csv`
- `output/backtest/NIFTY/sentiment/sentiment_joined.csv`
- `output/backtest/NIFTY/sentiment/sentiment_residual_summary.txt`

## Layer 3/4 Sector Contract

The zero-shot classifier labels are derived from the same sector definitions used
for NIFTY50 weighting. NSE publishes constituent-level `Weightage`; the refresh
script maps each constituent `Industry` into the internal sector key and then
aggregates weights by sector:

| Internal key | Zero-shot label | Approx NIFTY50 weight |
|---|---|---:|
| `financial_services` | Banking & Finance | 33% |
| `information_technology` | IT & Technology | 13% |
| `oil_gas` | Energy & Oil | 12% |
| `fmcg` | FMCG | 9% |
| `automobile` | Auto | 7% |
| `healthcare` | Pharma & Healthcare | 5% |
| `metals` | Metals & Mining | 4% |
| `consumer_durables` | Consumer Durables | 3% |
| `telecom` | Telecom | 3% |
| `construction` | Infrastructure | 3% |
| `power` | Power & Utilities | 2% |
| `services` | Services & Logistics | 1% |
| `realty` | Realty | 1% |

Kite Connect does not expose NSE's official monthly NIFTY50 sector-weight files
through its instruments / quote / LTP APIs. The pipeline therefore refreshes from
NSE's official NIFTY50 constituent CSV and falls back to the approximate config
weights if the cached NSE file is absent.

`broad_market` is an extra macro/index routing bucket with weight `1.0`; it is
not an NSE sector and is not offered as a zero-shot sector label.

## Layer 5 Composite Formula

Each article contributes:

```text
article_signal = sentiment_score * sentiment_confidence * sector_weight
```

where `sentiment_score` is `+1`, `0`, or `-1` after FinBERT label mapping, and
`sector_weight` is the confidence-weighted NIFTY50 exposure for the tagged
sector(s).

The daily composite is normalized by total weight hit:

```text
composite_score = sum(article_signal) / sum(sentiment_confidence * sector_weight)
```

This keeps the score approximately in `[-1, +1]`. The composite CSV stores both
`weighted_signal_sum` and `normalization_denominator` so the calculation is
auditable.

## Commands

Generate a pre-market signal:

```powershell
python -m src.news_sentiment.pipeline --target-date 2026-06-26
```

Refresh NSE NIFTY50 sector weights:

```powershell
python scripts/daily_NIFTY/refresh_nifty50_sector_weights.py
```

RSS only:

```powershell
python -m src.news_sentiment.pipeline --target-date 2026-06-26 --no-newsapi
```

RSS only with the deterministic fallback scorer:

```powershell
python -m src.news_sentiment.pipeline --target-date 2026-06-26 --no-newsapi --no-transformers
```

RSS only with FinBERT enabled but keyword sector fallback:

```powershell
python -m src.news_sentiment.pipeline --target-date 2026-06-26 --no-newsapi --no-zero-shot-sectors
```

Use an Azure/OpenAI LLM instead of BART for sector classification:

```powershell
python -m src.news_sentiment.pipeline --target-date 2026-06-26 --sector-classifier llm
```

The LLM classifier uses Azure when `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_DEPLOYMENT` are configured. If Azure
is absent, it uses OpenAI when `OPENAI_API_KEY` is configured. If neither is
configured or the call fails, the pipeline falls back to keyword sector tagging.

Install optional transformer dependencies (FinBERT sentiment + BART zero-shot
sectors) before running without `--no-transformers`:

```powershell
pip install -r requirements-news.txt
```

Run residual experiment:

```powershell
python -m src.news_sentiment.backtest
```

## Promotion Rule

Do not wire sentiment into `final_prediction` until research shows one of:

- sentiment adds edge on `NO_POSITION` days,
- sentiment agreement improves CALL/PUT precision,
- sentiment disagreement lowers wrong-way rate enough to justify a filter.
