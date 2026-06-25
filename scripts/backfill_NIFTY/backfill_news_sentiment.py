"""Backfill NIFTY news sentiment in resumable target-date batches.

Examples:
    python scripts/backfill_NIFTY/backfill_news_sentiment.py --start-date 2026-01-01 --end-date 2026-06-25 --batch-size 10 --batch-index 0 --sector-classifier keyword
    python scripts/backfill_NIFTY/backfill_news_sentiment.py --start-date 2026-01-01 --end-date 2026-06-25 --batch-size 10 --batch-index 1 --sector-classifier keyword
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.news_sentiment.config import composite_signal_store_path
from src.news_sentiment.pipeline import _build_sector_classifier, run_news_sentiment_for_target
from src.news_sentiment.sentiment import FinBertSentimentScorer

IST = ZoneInfo("Asia/Kolkata")


def iter_dates(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def select_batch(dates: list[date], batch_size: int, batch_index: int | None) -> list[date]:
    if batch_size <= 0:
        raise ValueError("batch-size must be positive")
    if batch_index is None:
        return dates
    if batch_index < 0:
        raise ValueError("batch-index must be zero or greater")
    start = batch_index * batch_size
    return dates[start:start + batch_size]


def backfill_news_sentiment(
    start_date: date,
    end_date: date,
    batch_size: int,
    batch_index: int | None,
    include_newsapi: bool,
    use_transformers: bool,
    use_zero_shot_sectors: bool,
    sector_classifier_mode: str,
    skip_existing: bool,
    continue_on_error: bool,
) -> list[dict[str, object]]:
    dates = select_batch(iter_dates(start_date, end_date), batch_size, batch_index)
    scorer = FinBertSentimentScorer(use_transformers=use_transformers)
    sector_classifier = _build_sector_classifier(
        sector_classifier_mode,
        use_transformers,
        use_zero_shot_sectors,
    )

    results: list[dict[str, object]] = []
    for target in dates:
        output_path = composite_signal_store_path(target)
        if skip_existing and output_path.exists():
            print(f"[SKIP] {target.isoformat()} already has {output_path}")
            results.append({"target_date": target.isoformat(), "status": "skipped"})
            continue
        try:
            signal = run_news_sentiment_for_target(
                target,
                scorer=scorer,
                sector_classifier=sector_classifier,
                include_newsapi=include_newsapi,
            )
            if signal is None:
                print(f"[NO ARTICLES] {target.isoformat()}: no files written")
                results.append({"target_date": target.isoformat(), "status": "no_articles"})
                continue
            print(
                f"[OK] {signal.target_date}: articles={signal.article_count}, "
                f"usable={signal.usable_article_count}, score={signal.composite_score}, "
                f"label={signal.composite_label}"
            )
            results.append({
                "target_date": signal.target_date,
                "status": "ok",
                "article_count": signal.article_count,
                "usable_article_count": signal.usable_article_count,
                "composite_score": signal.composite_score,
                "composite_label": signal.composite_label,
            })
        except Exception as exc:  # noqa: BLE001 - backfill should report and keep moving when requested.
            print(f"[ERROR] {target.isoformat()}: {exc}")
            results.append({"target_date": target.isoformat(), "status": "error", "error": str(exc)})
            if not continue_on_error:
                raise
    return results


def main() -> None:
    today = datetime.now(IST).date()
    parser = argparse.ArgumentParser(description="Backfill NIFTY news sentiment in resumable batches.")
    parser.add_argument("--start-date", default="2026-01-01", help="First target date YYYY-MM-DD.")
    parser.add_argument("--end-date", default=today.isoformat(), help="Last target date YYYY-MM-DD. Default: today IST.")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of target dates per batch. Default: 10.")
    parser.add_argument("--batch-index", type=int, default=None, help="Zero-based batch index. Omit to process the full range.")
    parser.add_argument("--no-newsapi", action="store_true", help="Skip NewsAPI. Historical backfills usually need NewsAPI.")
    parser.add_argument("--no-transformers", action="store_true", help="Skip FinBERT and use lexical fallback.")
    parser.add_argument("--no-zero-shot-sectors", action="store_true", help="Force keyword sector tagging.")
    parser.add_argument(
        "--sector-classifier",
        choices=("bart", "keyword", "llm"),
        default="keyword",
        help="Sector classifier backend. Default: keyword for fast backfills.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Recompute dates even if their composite file exists.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop at the first failed target date.")
    args = parser.parse_args()

    results = backfill_news_sentiment(
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        batch_size=args.batch_size,
        batch_index=args.batch_index,
        include_newsapi=not args.no_newsapi,
        use_transformers=not args.no_transformers,
        use_zero_shot_sectors=not args.no_zero_shot_sectors,
        sector_classifier_mode=args.sector_classifier,
        skip_existing=not args.overwrite,
        continue_on_error=not args.stop_on_error,
    )
    ok_count = sum(1 for item in results if item.get("status") == "ok")
    skipped_count = sum(1 for item in results if item.get("status") == "skipped")
    no_article_count = sum(1 for item in results if item.get("status") == "no_articles")
    error_count = sum(1 for item in results if item.get("status") == "error")
    print(
        f"Backfill batch complete: ok={ok_count}, skipped={skipped_count}, "
        f"no_articles={no_article_count}, errors={error_count}"
    )


if __name__ == "__main__":
    main()