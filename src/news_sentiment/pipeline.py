from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
load_dotenv(_repo_root / ".env")

from src.news_sentiment.article_store import append_articles, append_composite_signal, append_enriched_articles
from src.news_sentiment.config import article_sentiment_store_path, article_store_path, composite_signal_store_path
from src.news_sentiment.db_store import persist_news_sentiment_run
from src.news_sentiment.schemas import CompositeSignal, EnrichedArticle
from src.news_sentiment.sector_classifier import KeywordSectorClassifier, LlmSectorClassifier, ZeroShotSectorClassifier
from src.news_sentiment.sentiment import FinBertSentimentScorer
from src.news_sentiment.sector_weights import load_nifty50_sector_weights
from src.news_sentiment.sources import fetch_all_articles
from src.news_sentiment.weighting import build_composite_signal, enrich_article

IST = ZoneInfo("Asia/Kolkata")


def sentiment_window(target_date: date) -> tuple[datetime, datetime]:
    previous_day = target_date - timedelta(days=1)
    return (
        datetime.combine(previous_day, time(15, 30), tzinfo=IST),
        datetime.combine(target_date, time(9, 0), tzinfo=IST),
    )


def run_news_sentiment_pipeline(
    target_date: date | None = None,
    include_newsapi: bool = True,
    use_transformers: bool = True,
    use_zero_shot_sectors: bool = True,
    sector_classifier_mode: str = "bart",
) -> dict[str, object]:
    target = target_date or datetime.now(IST).date()
    scorer = FinBertSentimentScorer(use_transformers=use_transformers)
    sector_classifier = _build_sector_classifier(sector_classifier_mode, use_transformers, use_zero_shot_sectors)
    signal = run_news_sentiment_for_target(
        target,
        scorer=scorer,
        sector_classifier=sector_classifier,
        include_newsapi=include_newsapi,
    )
    if signal is None:
        target_str = target.isoformat()
        print(f"No articles found for target_date={target_str}; skipped sentiment and market signal files.")
        return {
            "target_date": target_str,
            "article_count": 0,
            "usable_article_count": 0,
            "composite_score": None,
            "composite_label": "skipped_no_articles",
        }
    _print_signal(signal)
    return {
        "target_date": signal.target_date,
        "article_count": signal.article_count,
        "usable_article_count": signal.usable_article_count,
        "composite_score": signal.composite_score,
        "composite_label": signal.composite_label,
    }


def run_news_sentiment_for_target(
    target: date,
    scorer: FinBertSentimentScorer,
    sector_classifier: KeywordSectorClassifier | LlmSectorClassifier | ZeroShotSectorClassifier,
    include_newsapi: bool = True,
) -> CompositeSignal | None:
    window_start, window_end = sentiment_window(target)
    articles = fetch_all_articles(window_start, window_end, include_newsapi=include_newsapi)
    if not articles:
        print(f"[SKIP] {target.isoformat()}: no articles fetched for sentiment window.")
        return None
    target_str = target.isoformat()
    _safe_write_output(
        "news articles CSV",
        lambda: append_articles(articles, path=article_store_path(target_str)),
    )

    article_texts = [article.text_for_model() for article in articles]
    sentiments = scorer.score_many(article_texts)
    sector_tags = sector_classifier.classify_many(article_texts)
    sector_weights = load_nifty50_sector_weights()
    enriched: list[EnrichedArticle] = []
    for article, sentiment, sectors in zip(articles, sentiments, sector_tags):
        enriched.append(enrich_article(article, sentiment, sectors, sector_weights=sector_weights))

    _safe_write_output(
        "article sentiment CSV",
        lambda: append_enriched_articles(
            enriched,
            target_str,
            window_start,
            window_end,
            path=article_sentiment_store_path(target_str),
        ),
    )
    signal = build_composite_signal(target_str, window_start, window_end, enriched, datetime.now(IST))
    _safe_write_output(
        "market sentiment CSV",
        lambda: append_composite_signal(signal, path=composite_signal_store_path(target_str)),
    )
    _safe_persist_to_db(articles, enriched, target_str, window_start, window_end, signal)
    return signal


def _safe_write_output(label: str, writer) -> None:
    try:
        writer()
    except Exception as exc:  # noqa: BLE001 - Render/local filesystem output is best-effort.
        print(f"[WARN] Skipped {label} write: {type(exc).__name__}: {exc}")


def _safe_persist_to_db(
    articles: list,
    enriched: list[EnrichedArticle],
    target_str: str,
    window_start: datetime,
    window_end: datetime,
    signal: CompositeSignal,
) -> None:
    try:
        summary = persist_news_sentiment_run(articles, enriched, target_str, window_start, window_end, signal)
        if any(summary.values()):
            print(
                "DB persistence       : "
                f"articles={summary['news_articles']}, "
                f"article_sentiment={summary['article_sentiments']}, "
                f"market_sentiment={summary['market_sentiments']}"
            )
    except Exception as exc:  # noqa: BLE001 - DB persistence should not hide the computed signal.
        print(f"[WARN] News sentiment DB persistence skipped: {type(exc).__name__}: {exc}")


def _build_sector_classifier(
    mode: str,
    use_transformers: bool,
    use_zero_shot_sectors: bool,
) -> KeywordSectorClassifier | LlmSectorClassifier | ZeroShotSectorClassifier:
    selected = mode.lower().strip()
    if not use_zero_shot_sectors:
        selected = "keyword"
    if selected == "keyword":
        return KeywordSectorClassifier()
    if selected == "llm":
        return LlmSectorClassifier()
    return ZeroShotSectorClassifier(use_transformers=use_transformers)


def _print_signal(signal: CompositeSignal) -> None:
    print("=" * 72)
    print("NIFTY pre-market news sentiment")
    print("=" * 72)
    print(f"target_date         : {signal.target_date}")
    print(f"window              : {signal.window_start} .. {signal.window_end}")
    print(f"articles            : {signal.article_count} ({signal.usable_article_count} usable)")
    print(f"positive/neutral/neg: {signal.positive_count}/{signal.neutral_count}/{signal.negative_count}")
    print(f"weighted signal    : {signal.weighted_signal_sum:.6f}")
    print(f"normalization denom: {signal.normalization_denominator:.6f}")
    print(f"composite           : {signal.composite_score:.4f} -> {signal.composite_label}")
    print(f"sources             : {signal.source_mix or 'none'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the NIFTY pre-market news sentiment signal.")
    parser.add_argument("--target-date", default=None, help="Target market date YYYY-MM-DD. Default: today IST.")
    parser.add_argument("--no-newsapi", action="store_true", help="Skip NewsAPI and use RSS only.")
    parser.add_argument("--no-transformers", action="store_true", help="Skip FinBERT and use lexical fallback.")
    parser.add_argument("--no-zero-shot-sectors", action="store_true", help="Skip BART zero-shot sector tagging and use keyword fallback.")
    parser.add_argument(
        "--sector-classifier",
        choices=("bart", "keyword", "llm"),
        default="bart",
        help="Sector classifier backend. Default: bart. Use llm for Azure/OpenAI chat classification.",
    )
    args = parser.parse_args()

    target = date.fromisoformat(args.target_date) if args.target_date else None
    result = run_news_sentiment_pipeline(
        target_date=target,
        include_newsapi=not args.no_newsapi,
        use_transformers=not args.no_transformers,
        use_zero_shot_sectors=not args.no_zero_shot_sectors,
        sector_classifier_mode=args.sector_classifier,
    )
    print(result)


if __name__ == "__main__":
    main()
