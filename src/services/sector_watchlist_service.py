from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from scripts.daily_optionInstrument_refresh import run_load_option_instruments
from src.common.config import get_settings
from src.common.models import WatchedInstrument
from src.data_manager.db.database_client import DatabaseClient
from src.agents.reviewList.output_schema import ReviewListOutput
from src.services.backfill_service import BackfillRequest, BackfillService


_NSE_INDEX_ALIASES = {
    "AUTO": "NIFTY AUTO",
    "AUTOMOBILE": "NIFTY AUTO",
    "AUTOMOBILES": "NIFTY AUTO",
    "BANK": "NIFTY BANK",
    "BANKING": "NIFTY BANK",
    "BANKS": "NIFTY BANK",
    "CONSUMER DURABLE": "NIFTY CONSUMER DURABLES",
    "CONSUMER DURABLES": "NIFTY CONSUMER DURABLES",
    "FINANCE": "NIFTY FINANCIAL SERVICES",
    "FINANCIAL": "NIFTY FINANCIAL SERVICES",
    "FINANCIAL SERVICES": "NIFTY FINANCIAL SERVICES",
    "FMCG": "NIFTY FMCG",
    "HEALTHCARE": "NIFTY HEALTHCARE INDEX",
    "HEALTH CARE": "NIFTY HEALTHCARE INDEX",
    "IT": "NIFTY IT",
    "INFORMATION TECHNOLOGY": "NIFTY IT",
    "MEDIA": "NIFTY MEDIA",
    "METAL": "NIFTY METAL",
    "METALS": "NIFTY METAL",
    "OIL": "NIFTY OIL & GAS",
    "OIL & GAS": "NIFTY OIL & GAS",
    "OIL AND GAS": "NIFTY OIL & GAS",
    "PHARMA": "NIFTY PHARMA",
    "PHARMACEUTICAL": "NIFTY PHARMA",
    "PHARMACEUTICALS": "NIFTY PHARMA",
    "PRIVATE BANK": "NIFTY PRIVATE BANK",
    "PRIVATE BANKS": "NIFTY PRIVATE BANK",
    "PSU BANK": "NIFTY PSU BANK",
    "PSU BANKS": "NIFTY PSU BANK",
    "REAL ESTATE": "NIFTY REALTY",
    "REALTY": "NIFTY REALTY",
}


@dataclass(frozen=True)
class SectorConstituent:
    symbol: str
    name: str | None
    nse_index: str
    industry: str | None = None


@dataclass
class NSESectorConstituentProvider:
    """
    Fetches sector constituents directly from NSE.

    The service intentionally treats NSE as the sector-membership source of
    truth. The local DB is only used later for Kite-token enrichment and
    WatchedInstrument persistence.
    """

    timeout_seconds: int = 30
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )

    def fetch_for_sector(self, sector: str) -> list[SectorConstituent]:
        index_name = normalize_nse_sector_index(sector)
        session = requests.Session()
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/market-data/live-equity-market",
            "Connection": "keep-alive",
        }
        session.get("https://www.nseindia.com/", headers=headers, timeout=self.timeout_seconds)
        response = session.get(
            f"https://www.nseindia.com/api/equity-stockIndices?index={quote(index_name)}",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or []

        constituents: list[SectorConstituent] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol or symbol in {"NIFTY", "NIFTY50", index_name} or symbol.startswith("NIFTY "):
                continue
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            constituents.append(
                SectorConstituent(
                    symbol=symbol,
                    name=row.get("companyName") or meta.get("companyName") or row.get("identifier"),
                    nse_index=index_name,
                    industry=row.get("industry") or meta.get("industry"),
                )
            )
        return constituents


@dataclass
class SectorWatchlistRequest:
    sectors: list[str]
    as_of: datetime | None = None
    backfill_days: int = 90


@dataclass
class SectorWatchlistService:
    """
    Expands reviewed sector ideas into watched stocks and backfills new entries.
    """

    constituent_provider: NSESectorConstituentProvider = field(default_factory=NSESectorConstituentProvider)
    backfill_service: BackfillService = field(default_factory=BackfillService)

    def expand_from_review(
        self,
        review_output: ReviewListOutput,
        as_of: datetime | None = None,
        backfill_days: int = 90,
    ) -> dict[str, Any]:
        return self.expand_and_backfill(
            SectorWatchlistRequest(
                sectors=review_output.sectors(),
                as_of=as_of or review_output.as_of,
                backfill_days=backfill_days,
            )
        )

    def expand_and_backfill(self, request: SectorWatchlistRequest) -> dict[str, Any]:
        timestamp = request.as_of or datetime.now()
        sectors = _dedupe_clean(request.sectors)
        if not sectors:
            return {
                "triggered": False,
                "reason": "No sectors supplied by reviewList.",
                "sectors": [],
                "new_symbols": [],
            }

        sector_results: dict[str, Any] = {}
        all_constituents: dict[str, SectorConstituent] = {}
        for sector in sectors:
            try:
                constituents = self.constituent_provider.fetch_for_sector(sector)
                sector_results[sector] = {
                    "nse_index": normalize_nse_sector_index(sector),
                    "constituents_found": len(constituents),
                    "symbols": [item.symbol for item in constituents],
                }
                for item in constituents:
                    all_constituents.setdefault(item.symbol, item)
            except Exception as exc:
                sector_results[sector] = {
                    "nse_index": normalize_nse_sector_index(sector),
                    "error": str(exc),
                    "constituents_found": 0,
                    "symbols": [],
                }

        if not all_constituents:
            return {
                "triggered": False,
                "reason": "No NSE constituents were resolved for the supplied sectors.",
                "sectors": sectors,
                "sector_results": sector_results,
                "new_symbols": [],
            }

        new_instruments = self._insert_new_watched_stocks(all_constituents)
        new_symbols = [item.tradingsymbol for item in new_instruments]
        if not new_symbols:
            return {
                "triggered": False,
                "reason": "All resolved NSE constituents are already in WatchedInstrument.",
                "sectors": sectors,
                "sector_results": sector_results,
                "new_symbols": [],
            }

        option_instruments = run_load_option_instruments(
            instrument_type="STOCK",
            underlyings=new_symbols,
        )

        end_date = timestamp.date()
        start_date = end_date - timedelta(days=request.backfill_days)
        backfill = self.backfill_service.run_backfill(
            BackfillRequest(
                start_date=start_date,
                end_date=end_date,
                underlyings=new_symbols,
            )
        )

        return {
            "triggered": True,
            "sectors": sectors,
            "sector_results": sector_results,
            "new_symbols": new_symbols,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "option_instruments": option_instruments,
            "backfill": backfill,
        }

    def _insert_new_watched_stocks(
        self,
        constituents_by_symbol: dict[str, SectorConstituent],
    ) -> list[WatchedInstrument]:
        settings = get_settings()
        db = DatabaseClient(settings)
        db.connect()
        try:
            existing = db.get_watched_symbol_set(exchange="NSE", instrument_type="STOCK")
            new_symbols = sorted(set(constituents_by_symbol) - existing)
            if not new_symbols:
                return []

            stockdb_rows = db.get_stock_instruments_by_symbols(new_symbols, exchange="NSE")
            watched_rows: list[WatchedInstrument] = []
            for symbol in new_symbols:
                constituent = constituents_by_symbol[symbol]
                stock = stockdb_rows.get(symbol)
                watched_rows.append(
                    WatchedInstrument(
                        tradingsymbol=symbol,
                        exchange=stock.exchange if stock else "NSE",
                        name=(stock.name if stock else constituent.name),
                        instrument_token=(stock.instrument_token if stock else None),
                        segment=stock.segment if stock else "NSE",
                        tick_size=stock.tick_size if stock else None,
                        lot_size=stock.lot_size if stock else 1,
                        instrument_type="STOCK",
                        sector=constituent.nse_index,
                        industry=constituent.industry or constituent.nse_index,
                        is_fo_enabled=True,
                        is_active=True,
                    )
                )

            db.upsert_watched_instruments(watched_rows)
            return watched_rows
        finally:
            db.close()


def normalize_nse_sector_index(sector: str) -> str:
    cleaned = " ".join(str(sector or "").replace("_", " ").split()).upper()
    if not cleaned:
        raise ValueError("sector must not be empty")
    if cleaned.startswith("NIFTY "):
        return cleaned
    return _NSE_INDEX_ALIASES.get(cleaned, f"NIFTY {cleaned}")


def _dedupe_clean(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.upper()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result
