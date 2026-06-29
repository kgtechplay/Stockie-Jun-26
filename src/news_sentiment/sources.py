from __future__ import annotations

import hashlib
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

from src.news_sentiment.config import NEWSAPI_QUERY, RSS_FEEDS, RssFeed
from src.news_sentiment.schemas import NewsArticle

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Referer": "https://www.google.com/",
}


def fetch_all_articles(
    window_start: datetime,
    window_end: datetime,
    include_newsapi: bool = True,
    timeout_seconds: int = 20,
) -> list[NewsArticle]:
    fetched_at = datetime.now(IST)
    articles: list[NewsArticle] = []
    for feed in RSS_FEEDS:
        articles.extend(fetch_rss_feed(feed, window_start, window_end, fetched_at, timeout_seconds))
    if include_newsapi:
        api_key = os.getenv("NEWSAPI_KEY")
        if api_key:
            articles.extend(fetch_newsapi_articles(api_key, window_start, window_end, fetched_at, timeout_seconds))
        else:
            print("[WARN] NEWSAPI_KEY not set; skipping NewsAPI global macro feed.")
    return _dedupe_articles(articles)


def fetch_rss_feed(
    feed: RssFeed,
    window_start: datetime,
    window_end: datetime,
    fetched_at: datetime,
    timeout_seconds: int = 20,
) -> list[NewsArticle]:
    for url in (feed.url, *feed.fallback_urls):
        articles = _fetch_rss_url(feed, url, window_start, window_end, fetched_at, timeout_seconds)
        if articles:
            return articles
    return []


def _fetch_rss_url(
    feed: RssFeed,
    url: str,
    window_start: datetime,
    window_end: datetime,
    fetched_at: datetime,
    timeout_seconds: int,
) -> list[NewsArticle]:
    try:
        response = requests.get(url, timeout=timeout_seconds, headers=RSS_HEADERS)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - one feed should not fail the batch.
        print(f"[WARN] RSS fetch failed for {feed.name} ({url}): {exc}")
        return []

    text = response.text.lstrip()
    if not (text.startswith("<?xml") or text.startswith("<rss") or text.startswith("<feed")):
        print(f"[WARN] RSS response for {feed.name} was not XML; skipped {url}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"[WARN] RSS parse failed for {feed.name} ({url}): {exc}")
        return []

    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    articles: list[NewsArticle] = []
    for item in items:
        title = _node_text(item, "title")
        link = _node_text(item, "link") or _node_text(item, "guid")
        summary = _node_text(item, "description") or _node_text(item, "summary")
        published_raw = _node_text(item, "pubDate") or _node_text(item, "published") or _node_text(item, "updated")
        published_at = parse_datetime(published_raw) or fetched_at
        if not _in_window(published_at, window_start, window_end):
            continue
        articles.append(_article(feed.name, link, title, summary, published_at, fetched_at, feed.region, "rss"))
    return articles


def fetch_newsapi_articles(
    api_key: str,
    window_start: datetime,
    window_end: datetime,
    fetched_at: datetime,
    timeout_seconds: int = 20,
) -> list[NewsArticle]:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": NEWSAPI_QUERY,
        "language": "en",
        "sortBy": "publishedAt",
        "from": window_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "to": window_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "pageSize": 100,
        "apiKey": api_key,
    }
    try:
        response = requests.get(url, params=params, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        detail = _newsapi_error_detail(exc.response)
        print(f"[WARN] NewsAPI fetch failed: status={status_code}{detail}")
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] NewsAPI fetch failed: {type(exc).__name__}: {exc}")
        return []

    articles: list[NewsArticle] = []
    for item in payload.get("articles") or []:
        published_at = parse_datetime(str(item.get("publishedAt") or "")) or fetched_at
        if not _in_window(published_at, window_start, window_end):
            continue
        source = (item.get("source") or {}).get("name") or "NewsAPI"
        articles.append(_article(
            source=source,
            url=str(item.get("url") or ""),
            title=str(item.get("title") or ""),
            summary=str(item.get("description") or item.get("content") or ""),
            published_at=published_at,
            fetched_at=fetched_at,
            region="global",
            provider="newsapi",
        ))
    return articles


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except Exception:  # noqa: BLE001
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _article(
    source: str,
    url: str,
    title: str,
    summary: str,
    published_at: datetime,
    fetched_at: datetime,
    region: str,
    provider: str,
) -> NewsArticle:
    clean_title = _clean_text(title)
    clean_summary = _clean_text(summary)
    clean_url = url.strip()
    article_id = hashlib.sha256(
        "|".join([clean_url, source, clean_title, published_at.isoformat()]).encode("utf-8")
    ).hexdigest()[:16]
    return NewsArticle(article_id, source, clean_url, clean_title, clean_summary, published_at, fetched_at, region, provider)


def _node_text(item: ET.Element, tag: str) -> str:
    found = item.find(tag)
    if found is not None and found.text:
        return found.text
    found = item.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    if found is not None and found.text:
        return found.text
    return ""


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _in_window(timestamp: datetime, window_start: datetime, window_end: datetime) -> bool:
    ts = timestamp.astimezone(IST)
    return window_start <= ts <= window_end


def _dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    by_id: dict[str, NewsArticle] = {}
    for article in articles:
        by_id[article.article_id] = article
    return sorted(by_id.values(), key=lambda item: (item.published_at, item.source, item.title))


def _newsapi_error_detail(response: requests.Response | None) -> str:
    if response is None:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    code = payload.get("code")
    message = payload.get("message")
    parts = []
    if code:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    return " (" + ", ".join(parts) + ")" if parts else ""
