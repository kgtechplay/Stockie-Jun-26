from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from scripts.daily_optionInstrument_refresh import run_load_option_instruments
from src.common.config import get_settings
from src.common.models import WatchedInstrument
from src.data_manager.db.database_client import DatabaseClient
from src.agents.reviewList.output_schema import ReviewListOutput
from src.services.backfill_service import BackfillRequest, BackfillService

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Maps canonical NSE index names to substrings found in the sector/industry column
# of stocks_universe.csv. Listed from most-specific to least-specific so the first
# match that appears in the cell wins.
_INDEX_TO_CSV_KEYWORDS: dict[str, list[str]] = {
    "NIFTY AUTO":               ["AUTOMOBILE"],
    "NIFTY BANK":               ["BANK"],
    "NIFTY CONSUMER DURABLES":  ["CONSUMER DURABLE"],
    "NIFTY FINANCIAL SERVICES": ["FINANCIAL SERVICE"],
    "NIFTY FMCG":               ["FAST MOVING CONSUMER", "FMCG"],
    "NIFTY HEALTHCARE INDEX":   ["HEALTHCARE", "HEALTH CARE"],
    "NIFTY IT":                 ["INFORMATION TECHNOLOGY"],
    "NIFTY MEDIA":              ["MEDIA", "ENTERTAINMENT"],
    "NIFTY METAL":              ["METAL", "MINING"],
    "NIFTY OIL & GAS":          ["OIL GAS", "OIL & GAS"],
    "NIFTY PHARMA":             ["PHARMA"],
    "NIFTY PRIVATE BANK":       ["PRIVATE BANK"],
    "NIFTY PSU BANK":           ["PSU BANK"],
    "NIFTY REALTY":             ["REALTY", "REAL ESTATE"],
    "NIFTY CHEMICALS":          ["CHEMICAL"],
    "NIFTY CAPITAL GOODS":      ["CAPITAL GOODS"],
    "NIFTY POWER":              ["POWER"],
    "NIFTY TEXTILES":           ["TEXTILE"],
    "NIFTY SERVICES SECTOR":    ["SERVICES"],
}

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


# ──────────────────────────────────────────────────────────────────────────────
# SectorConstituent — carries full Kite fields when sourced from the CSV
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SectorConstituent:
    symbol: str
    name: str | None
    nse_index: str
    industry: str | None = None
    # Kite fields — populated when loaded from stocks_universe.csv;
    # None when loaded from the live NSE API (StockDB lookup used instead).
    instrument_token: int | None = None
    segment: str | None = None
    tick_size: float | None = None
    lot_size: int | None = None
    is_fo_enabled: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# CsvSectorConstituentProvider (default)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CsvSectorConstituentProvider:
    """
    Expands a sector name to its constituent stocks by filtering stocks_universe.csv.

    Matching: the requested sector is normalised to a canonical NSE index name
    (e.g. "pharma" → "NIFTY PHARMA"), then a keyword list is looked up from
    _INDEX_TO_CSV_KEYWORDS and used as case-insensitive substrings against the
    CSV's sector and industry columns.  For index names not in the keyword map the
    index name itself (with "NIFTY " stripped) is used as the keyword.

    The CSV is loaded once and cached for the lifetime of the provider object.
    """

    csv_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "stocks_universe.csv")
    fo_only: bool = True   # restrict to FO-enabled stocks (recommended)

    # internal cache — loaded lazily
    _rows: list[dict] = field(default_factory=list, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def fetch_for_sector(self, sector: str) -> list[SectorConstituent]:
        self._ensure_loaded()
        index_name = normalize_nse_sector_index(sector)
        keywords   = self._keywords_for(index_name)

        constituents: list[SectorConstituent] = []
        for row in self._rows:
            if self.fo_only and str(row.get("is_fo_enabled", "0")).strip() not in {"1", "True", "true"}:
                continue
            if row.get("instrument_type", "").strip().upper() != "STOCK":
                continue
            cell_sector   = (row.get("sector")   or "").upper()
            cell_industry = (row.get("industry")  or "").upper()
            if any(kw in cell_sector or kw in cell_industry for kw in keywords):
                constituents.append(self._to_constituent(row, index_name))

        return constituents

    def _keywords_for(self, index_name: str) -> list[str]:
        if index_name in _INDEX_TO_CSV_KEYWORDS:
            return [kw.upper() for kw in _INDEX_TO_CSV_KEYWORDS[index_name]]
        # fallback: strip "NIFTY " and use the remainder as one keyword
        keyword = index_name.removeprefix("NIFTY ").strip()
        return [keyword] if keyword else [index_name]

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"stocks_universe.csv not found at {self.csv_path}. "
                "Run scripts/fetch_stocks_universe.py to generate it."
            )
        with self.csv_path.open(newline="", encoding="utf-8") as f:
            self._rows = list(csv.DictReader(f))
        self._loaded = True

    @staticmethod
    def _to_constituent(row: dict, nse_index: str) -> SectorConstituent:
        def _int(v: str | None) -> int | None:
            try:
                return int(v) if v and v.strip() else None
            except (ValueError, TypeError):
                return None

        def _float(v: str | None) -> float | None:
            try:
                return float(v) if v and v.strip() else None
            except (ValueError, TypeError):
                return None

        return SectorConstituent(
            symbol=row["tradingsymbol"].strip().upper(),
            name=row.get("name") or None,
            nse_index=nse_index,
            industry=row.get("industry") or row.get("sector") or None,
            instrument_token=_int(row.get("instrument_token")),
            segment=row.get("segment") or None,
            tick_size=_float(row.get("tick_size")),
            lot_size=_int(row.get("lot_size")),
            is_fo_enabled=str(row.get("is_fo_enabled", "0")).strip() in {"1", "True", "true"},
        )


# ──────────────────────────────────────────────────────────────────────────────
# NSESectorConstituentProvider (live fallback)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class NSESectorConstituentProvider:
    """
    Fetches live sector constituents from the NSE API.
    Use as a fallback when stocks_universe.csv is unavailable or stale.
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
        rows = response.json().get("data") or []

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


# ──────────────────────────────────────────────────────────────────────────────
# SectorWatchlistRequest / SectorWatchlistService
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SectorWatchlistRequest:
    sectors: list[str]
    reference_date: date           # date N — backfill covers N-backfill_days to N-1
    backfill_days: int = 90


@dataclass
class SectorWatchlistService:
    """
    Expands reviewed sector names → individual stocks (via stocks_universe.csv),
    registers new entries in WatchedInstrument, backfills historical data
    (N-backfill_days → N-1), and triggers predictions for date N.
    """

    constituent_provider: CsvSectorConstituentProvider | NSESectorConstituentProvider = field(
        default_factory=CsvSectorConstituentProvider
    )
    backfill_service: BackfillService = field(default_factory=BackfillService)
    prediction_service: Any | None = None   # PredictionService injected by orchestrator

    def expand_from_review(
        self,
        review_output: ReviewListOutput,
        backfill_days: int = 90,
    ) -> dict[str, Any]:
        return self.expand_and_backfill(
            SectorWatchlistRequest(
                sectors=review_output.approved_sectors(),
                reference_date=review_output.reference_date,
                backfill_days=backfill_days,
            )
        )

    def expand_and_backfill(self, request: SectorWatchlistRequest) -> dict[str, Any]:
        sectors = _dedupe_clean(request.sectors)
        if not sectors:
            return {
                "triggered": False,
                "reason": "No approved sectors from reviewList.",
                "sectors": [],
                "new_symbols": [],
            }

        # ── 1. Expand sectors → constituents ──────────────────────────────
        sector_results: dict[str, Any] = {}
        all_constituents: dict[str, SectorConstituent] = {}
        for sector in sectors:
            try:
                constituents = self.constituent_provider.fetch_for_sector(sector)
                sector_results[sector] = {
                    "nse_index": normalize_nse_sector_index(sector),
                    "constituents_found": len(constituents),
                    "symbols": [c.symbol for c in constituents],
                }
                for c in constituents:
                    all_constituents.setdefault(c.symbol, c)
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
                "reason": "No constituents resolved for the supplied sectors.",
                "sectors": sectors,
                "sector_results": sector_results,
                "new_symbols": [],
            }

        # ── 2. Register net-new stocks in WatchedInstrument ───────────────
        new_instruments = self._insert_new_watched_stocks(all_constituents)
        new_symbols = [w.tradingsymbol for w in new_instruments]
        if not new_symbols:
            return {
                "triggered": False,
                "reason": "All resolved constituents are already in WatchedInstrument.",
                "sectors": sectors,
                "sector_results": sector_results,
                "new_symbols": [],
            }

        # ── 3. Load option instruments for new stocks ─────────────────────
        option_instruments = run_load_option_instruments(
            instrument_type="STOCK",
            underlyings=new_symbols,
        )

        # ── 4. Backfill historical data: N-backfill_days → N-1 ────────────
        end_date   = request.reference_date - timedelta(days=1)
        start_date = request.reference_date - timedelta(days=request.backfill_days)
        backfill = self.backfill_service.run_backfill(
            BackfillRequest(
                start_date=start_date,
                end_date=end_date,
                underlyings=new_symbols,
            )
        )

        # ── 5. Trigger predictions for date N ─────────────────────────────
        prediction_results: dict[str, Any] = {}
        if self.prediction_service is not None:
            as_of = datetime.combine(request.reference_date, datetime.min.time())
            for symbol in new_symbols:
                try:
                    result = self.prediction_service.run_prediction(
                        instrument=symbol,
                        strategies=None,
                        as_of=as_of,
                    )
                    prediction_results[symbol] = {"status": "ok", "output": result}
                except Exception as exc:
                    prediction_results[symbol] = {"status": "error", "error": str(exc)}

        return {
            "triggered": True,
            "sectors": sectors,
            "sector_results": sector_results,
            "new_symbols": new_symbols,
            "reference_date": request.reference_date.isoformat(),
            "backfill_start": start_date.isoformat(),
            "backfill_end":   end_date.isoformat(),
            "option_instruments": option_instruments,
            "backfill": backfill,
            "predictions": prediction_results,
        }

    def _insert_new_watched_stocks(
        self,
        constituents_by_symbol: dict[str, SectorConstituent],
    ) -> list[WatchedInstrument]:
        settings = get_settings()
        db = DatabaseClient(settings)
        db.connect()
        try:
            existing    = db.get_watched_symbol_set(exchange="NSE", instrument_type="STOCK")
            new_symbols = sorted(set(constituents_by_symbol) - existing)
            if not new_symbols:
                return []

            # Only fall back to StockDB for symbols whose CSV data had no token.
            needs_stockdb = [
                s for s in new_symbols
                if constituents_by_symbol[s].instrument_token is None
            ]
            stockdb_rows = (
                db.get_stock_instruments_by_symbols(needs_stockdb, exchange="NSE")
                if needs_stockdb else {}
            )

            watched_rows: list[WatchedInstrument] = []
            for symbol in new_symbols:
                c     = constituents_by_symbol[symbol]
                stock = stockdb_rows.get(symbol)   # None when token came from CSV

                watched_rows.append(
                    WatchedInstrument(
                        tradingsymbol=symbol,
                        exchange="NSE",
                        name=c.name or (stock.name if stock else None),
                        instrument_token=c.instrument_token or (stock.instrument_token if stock else None),
                        segment=c.segment or (stock.segment if stock else "NSE"),
                        tick_size=c.tick_size or (stock.tick_size if stock else None),
                        lot_size=c.lot_size or (stock.lot_size if stock else 1),
                        instrument_type="STOCK",
                        sector=c.nse_index,
                        industry=c.industry or c.nse_index,
                        is_fo_enabled=True,
                        is_active=True,
                    )
                )

            db.upsert_watched_instruments(watched_rows)
            return watched_rows
        finally:
            db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

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
