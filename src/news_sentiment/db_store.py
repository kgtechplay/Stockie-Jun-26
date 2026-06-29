from __future__ import annotations

from datetime import datetime
from typing import Any

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.news_sentiment.schemas import CompositeSignal, EnrichedArticle, NewsArticle


def persist_news_sentiment_run(
    articles: list[NewsArticle],
    enriched: list[EnrichedArticle],
    target_date: str,
    window_start: datetime,
    window_end: datetime,
    signal: CompositeSignal,
) -> dict[str, int]:
    settings = get_settings()
    if not settings.supabase_conn_str and settings.database_provider != "supabase":
        return {"news_articles": 0, "article_sentiments": 0, "market_sentiments": 0}

    db = get_database_client(settings)
    db.connect()
    try:
        _require_news_methods(db)
        article_rows = [article.to_row() for article in articles]
        sentiment_rows = [item.to_row(target_date, window_start, window_end) for item in enriched]
        market_rows = [signal.to_row()]
        return {
            "news_articles": db.upsert_news_articles(article_rows),
            "article_sentiments": db.upsert_news_article_sentiments(sentiment_rows),
            "market_sentiments": db.upsert_nifty_market_sentiments(market_rows),
        }
    except Exception:
        if hasattr(db, "conn"):
            try:
                db.conn.rollback()
            except Exception:
                pass
        raise
    finally:
        db.close()


def _require_news_methods(db: Any) -> None:
    missing = [
        name for name in (
            "upsert_news_articles",
            "upsert_news_article_sentiments",
            "upsert_nifty_market_sentiments",
        )
        if not hasattr(db, name)
    ]
    if missing:
        raise RuntimeError(f"Database client does not support news sentiment persistence: {', '.join(missing)}")
