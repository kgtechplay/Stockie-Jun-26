from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTELLIGENCE_DIR = PROJECT_ROOT / "output" / "intelligence"
ARTICLE_STORE = INTELLIGENCE_DIR / "news_articles.csv"
ARTICLE_SENTIMENT_STORE = INTELLIGENCE_DIR / "NIFTY_article_sentiment.csv"
COMPOSITE_SIGNAL_STORE = INTELLIGENCE_DIR / "NIFTY_market_sentiment.csv"
ARTICLE_STORE_DIR = INTELLIGENCE_DIR / "news_articles"
ARTICLE_SENTIMENT_STORE_DIR = INTELLIGENCE_DIR / "article_sentiment"
COMPOSITE_SIGNAL_STORE_DIR = INTELLIGENCE_DIR / "market_sentiment"
NIFTY50_CONSTITUENT_WEIGHTS_STORE = INTELLIGENCE_DIR / "NIFTY50_constituent_weights.csv"
NIFTY50_SECTOR_WEIGHTS_STORE = INTELLIGENCE_DIR / "NIFTY50_sector_weights.csv"
SENTIMENT_BACKTEST_DIR = PROJECT_ROOT / "output" / "backtest" / "NIFTY" / "sentiment"


def target_date_folder(target_date: date | datetime | str) -> str:
    if isinstance(target_date, datetime):
        value = target_date.date()
    elif isinstance(target_date, date):
        value = target_date
    else:
        value = date.fromisoformat(str(target_date))
    return value.strftime("%d-%m-%Y")


def article_store_path(target_date: date | datetime | str) -> Path:
    return ARTICLE_STORE_DIR / target_date_folder(target_date) / "news_articles.csv"


def article_sentiment_store_path(target_date: date | datetime | str) -> Path:
    return ARTICLE_SENTIMENT_STORE_DIR / target_date_folder(target_date) / "NIFTY_article_sentiment.csv"


def composite_signal_store_path(target_date: date | datetime | str) -> Path:
    return COMPOSITE_SIGNAL_STORE_DIR / target_date_folder(target_date) / "NIFTY_market_sentiment.csv"


@dataclass(frozen=True)
class RssFeed:
    name: str
    url: str
    region: str = "india"
    fallback_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectorDefinition:
    key: str
    label: str
    nifty50_weight: float


RSS_FEEDS: tuple[RssFeed, ...] = (
    RssFeed(
        "ET Markets",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        fallback_urls=("https://economictimes.indiatimes.com/markets/rss.cms",),
    ),
    RssFeed("Moneycontrol Market Reports", "https://www.moneycontrol.com/rss/marketreports.xml"),
    RssFeed("LiveMint Markets", "https://www.livemint.com/rss/markets"),
)

NEWSAPI_QUERY = (
    "NIFTY OR Sensex OR India markets OR RBI OR rupee OR crude oil OR Fed OR inflation "
    "OR global markets OR US yields"
)

# NIFTY50 sector definitions are the single source of truth for both Azure LLM
# prompt labels and Layer 4 fallback sector weighting. Official weights should
# be refreshed from NSE into NIFTY50_SECTOR_WEIGHTS_STORE.
NIFTY50_SECTOR_DEFINITIONS: tuple[SectorDefinition, ...] = (
    SectorDefinition("financial_services", "Banking & Finance", 0.33),
    SectorDefinition("information_technology", "IT & Technology", 0.13),
    SectorDefinition("oil_gas", "Energy & Oil", 0.12),
    SectorDefinition("fmcg", "FMCG", 0.09),
    SectorDefinition("automobile", "Auto", 0.07),
    SectorDefinition("healthcare", "Pharma & Healthcare", 0.05),
    SectorDefinition("metals", "Metals & Mining", 0.04),
    SectorDefinition("consumer_durables", "Consumer Durables", 0.03),
    SectorDefinition("telecom", "Telecom", 0.03),
    SectorDefinition("construction", "Infrastructure", 0.03),
    SectorDefinition("power", "Power & Utilities", 0.02),
    SectorDefinition("services", "Services & Logistics", 0.01),
    SectorDefinition("realty", "Realty", 0.01),
)

NIFTY50_SECTOR_WEIGHTS: dict[str, float] = {
    definition.key: definition.nifty50_weight for definition in NIFTY50_SECTOR_DEFINITIONS
}

# Broad-market articles should affect the whole index. This is not an NSE sector;
# it is a routing bucket for macro/index-level news.
NIFTY50_SECTOR_WEIGHTS["broad_market"] = 1.00

POSITIVE_THRESHOLD = 0.10
NEGATIVE_THRESHOLD = -0.10
