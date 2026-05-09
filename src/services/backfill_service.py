from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient
from scripts.backfill.backfill_nifty_underlying import run_backfill_underlying
from scripts.backfill.backfill_nifty_options import run_backfill_options
from scripts.backfill.backfill_nifty_volumeproxy import run_backfill_volumeproxy
from scripts.backfill.backfill_stocks_underlying import run_backfill_stocks_underlying
from scripts.backfill.backfill_stocks_options import run_backfill_stocks_options


@dataclass
class BackfillRequest:
    start_date: date
    end_date: date
    underlyings: list[str] | None = field(default=None)
    # When None, all active WatchedInstrument entries are backfilled.
    # Pass a list to restrict to specific symbols, e.g. ["NIFTY", "RELIANCE"].


class BackfillService:
    """
    Orchestrates underlying + options backfill for any mix of INDEX and STOCK
    instruments sourced from dbo.WatchedInstrument.
    """

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    def run_backfill(self, request: BackfillRequest) -> dict[str, Any]:
        if request.start_date > request.end_date:
            raise ValueError("start_date must be <= end_date")

        index_symbols, stock_symbols = self._classify_instruments(request.underlyings)

        if not index_symbols and not stock_symbols:
            return {
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
                "underlyings": [],
                "components": {},
            }

        results: dict[str, Any] = {}

        if index_symbols:
            results["index_underlying"] = run_backfill_underlying(
                start_date=request.start_date,
                end_date=request.end_date,
                underlyings=index_symbols,
            )
            results["index_options"] = run_backfill_options(
                global_start=request.start_date,
                global_end=request.end_date,
                underlyings=index_symbols,
            )
            results["index_volumeproxy"] = run_backfill_volumeproxy(
                start_date=request.start_date,
                end_date=request.end_date,
                underlyings=index_symbols,
            )

        if stock_symbols:
            results["stock_underlying"] = run_backfill_stocks_underlying(
                start_date=request.start_date,
                end_date=request.end_date,
                underlyings=stock_symbols,
            )
            results["stock_options"] = run_backfill_stocks_options(
                global_start=request.start_date,
                global_end=request.end_date,
                underlyings=stock_symbols,
            )

        return {
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "underlyings": index_symbols + stock_symbols,
            "components": results,
        }

    # ------------------------------------------------------------------
    # Data coverage queries (no instrument-type restriction)
    # ------------------------------------------------------------------

    def get_underlying_data_range(self, underlying: str) -> dict[str, Any]:
        symbol = self._clean_symbol(underlying)
        db = DatabaseClient(get_settings())
        db.connect()
        try:
            daily = self._query_date_range(
                db,
                """
                SELECT MIN(CAST(trade_date AS date)),
                       MAX(CAST(trade_date AS date)),
                       COUNT(1)
                FROM dbo.UnderlyingSnapshot
                WHERE underlying = ?
                """,
                [symbol],
            )
            intraday = self._query_date_range(
                db,
                """
                SELECT MIN(CAST(trade_date AS date)),
                       MAX(CAST(trade_date AS date)),
                       COUNT(1)
                FROM dbo.UnderlyingCandle5m
                WHERE underlying = ?
                """,
                [symbol],
            )
        finally:
            db.close()

        return {"underlying": symbol, "daily": daily, "candles_5m": intraday}

    def get_options_data_range(self, underlying: str) -> dict[str, Any]:
        symbol = self._clean_symbol(underlying)
        db = DatabaseClient(get_settings())
        db.connect()
        try:
            snapshots = self._query_date_range(
                db,
                """
                SELECT MIN(CAST(s.snapshot_time AS date)),
                       MAX(CAST(s.snapshot_time AS date)),
                       COUNT(1)
                FROM dbo.OptionSnapshot s
                INNER JOIN dbo.OptionInstrument oi ON oi.id = s.option_instrument_id
                WHERE oi.underlying = ?
                """,
                [symbol],
            )
            instruments = self._query_date_range(
                db,
                """
                SELECT MIN(CAST(fetch_date AS date)),
                       MAX(CAST(fetch_date AS date)),
                       COUNT(1)
                FROM dbo.OptionInstrument
                WHERE underlying = ?
                """,
                [symbol],
            )
        finally:
            db.close()

        return {"underlying": symbol, "snapshots": snapshots, "instruments": instruments}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _classify_instruments(
        self,
        underlyings: list[str] | None,
    ) -> tuple[list[str], list[str]]:
        """
        Return (index_symbols, stock_symbols) by querying WatchedInstrument.
        If `underlyings` is None, returns all active instruments.
        If `underlyings` is provided, restricts to those symbols (case-insensitive).
        """
        settings = get_settings()
        tdb = DatabaseClient(settings)
        tdb.connect()
        try:
            all_index = tdb.get_watched_instruments(instrument_type="INDEX")
            all_stock = tdb.get_watched_instruments(instrument_type="STOCK")
        finally:
            tdb.close()

        if underlyings is not None:
            requested = {u.strip().upper() for u in underlyings}
            all_index = [w for w in all_index if w.tradingsymbol in requested]
            all_stock = [w for w in all_stock if w.tradingsymbol in requested]

            found = {w.tradingsymbol for w in all_index + all_stock}
            missing = requested - found
            if missing:
                raise ValueError(f"Symbols not found in WatchedInstrument: {sorted(missing)}")

        return (
            [w.tradingsymbol for w in all_index],
            [w.tradingsymbol for w in all_stock],
        )

    @staticmethod
    def _clean_symbol(underlying: str) -> str:
        symbol = (underlying or "").strip().upper()
        if not symbol:
            raise ValueError("underlying must not be empty")
        return symbol

    @staticmethod
    def _query_date_range(db: DatabaseClient, sql: str, params: list[Any]) -> dict[str, Any]:
        cur = db.conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return {
            "min_date": _to_iso_date(row[0] if row else None),
            "max_date": _to_iso_date(row[1] if row else None),
            "row_count": int(row[2]) if row and row[2] is not None else 0,
        }


def _to_iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except Exception:
        return str(value)
