"""Daily NIFTY pre-market news sentiment job.

Builds a market-level sentiment signal for the target session using news from
15:30 IST on the prior calendar day through 09:00 IST on the target date.

Usage:
    python scripts/daily_NIFTY/daily_news_sentiment.py
    python scripts/daily_NIFTY/daily_news_sentiment.py --target-date 2026-06-26
    python scripts/daily_NIFTY/daily_news_sentiment.py --target-date 2026-06-26 --skip-existing
    python scripts/daily_NIFTY/daily_news_sentiment.py --no-newsapi --no-transformers
    python scripts/daily_NIFTY/daily_news_sentiment.py --no-newsapi --no-zero-shot-sectors
    python scripts/daily_NIFTY/daily_news_sentiment.py --sector-classifier llm
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.news_sentiment.config import article_sentiment_store_path, article_store_path, composite_signal_store_path
from src.news_sentiment.pipeline import run_news_sentiment_pipeline

IST = ZoneInfo("Asia/Kolkata")


def run_daily_news_sentiment(
    target_date: date | None = None,
    include_newsapi: bool = True,
    use_transformers: bool = True,
    use_zero_shot_sectors: bool = True,
    sector_classifier_mode: str = "bart",
    skip_existing: bool = False,
) -> dict[str, object]:
    target = target_date or datetime.now(IST).date()
    article_path = article_store_path(target)
    article_sentiment_path = article_sentiment_store_path(target)
    market_sentiment_path = composite_signal_store_path(target)

    if skip_existing and market_sentiment_path.exists():
        print(f"[SKIP] {target.isoformat()} already has {market_sentiment_path}")
        return {
            "target_date": target.isoformat(),
            "status": "skipped_existing",
            "article_store": str(article_path),
            "article_sentiment_store": str(article_sentiment_path),
            "market_sentiment_store": str(market_sentiment_path),
        }

    result = run_news_sentiment_pipeline(
        target_date=target,
        include_newsapi=include_newsapi,
        use_transformers=use_transformers,
        use_zero_shot_sectors=use_zero_shot_sectors,
        sector_classifier_mode=sector_classifier_mode,
    )
    status = "ok" if result.get("article_count", 0) else "no_articles"
    return {
        **result,
        "status": status,
        "article_store": str(article_path),
        "article_sentiment_store": str(article_sentiment_path),
        "market_sentiment_store": str(market_sentiment_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the NIFTY pre-market news sentiment signal.")
    parser.add_argument("--target-date", default=None, help="Target market date YYYY-MM-DD. Default: today IST.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip when the target date market sentiment file already exists.")
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
    result = run_daily_news_sentiment(
        target_date=target,
        include_newsapi=not args.no_newsapi,
        use_transformers=not args.no_transformers,
        use_zero_shot_sectors=not args.no_zero_shot_sectors,
        sector_classifier_mode=args.sector_classifier,
        skip_existing=args.skip_existing,
    )
    print(result)


if __name__ == "__main__":
    main()
