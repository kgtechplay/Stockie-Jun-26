from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from scripts.backfill.backfill_underlying import run_backfill_underlying_data
from scripts.backfill.backfill_NIFTYoptions_from_historical import run_backfill_options_from_historical


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
            results["index_underlying"] = run_backfill_underlying_data(
                instrument_type="INDEX",
                start_date=request.start_date,
                end_date=request.end_date,
                underlyings=index_symbols,
            )
            results["index_options"] = run_backfill_options_from_historical(
                global_start=request.start_date,
                global_end=request.end_date,
                underlyings=index_symbols,
            )

        if stock_symbols:
            results["stock_underlying"] = run_backfill_underlying_data(
                instrument_type="STOCK",
                start_date=request.start_date,
                end_date=request.end_date,
                underlyings=stock_symbols,
            )
            results["stock_options"] = run_backfill_options_from_historical(
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
        db = get_database_client(get_settings())
        db.connect()
        pg = getattr(db, "db_kind", "") == "postgres"
        ph, snap, candle = ("%s", '"UnderlyingSnapshot"', '"UnderlyingCandle5m"') if pg else ("?", "dbo.UnderlyingSnapshot", "dbo.UnderlyingCandle5m")
        try:
            daily = self._query_date_range(db,
                f"SELECT MIN(trade_date), MAX(trade_date), COUNT(1) FROM {snap} WHERE underlying = {ph}",
                [symbol])
            intraday = self._query_date_range(db,
                f"SELECT MIN(trade_date), MAX(trade_date), COUNT(1) FROM {candle} WHERE underlying = {ph}",
                [symbol])
        finally:
            db.close()
        return {"underlying": symbol, "daily": daily, "candles_5m": intraday}

    def get_options_data_range(self, underlying: str) -> dict[str, Any]:
        symbol = self._clean_symbol(underlying)
        db = get_database_client(get_settings())
        db.connect()
        pg = getattr(db, "db_kind", "") == "postgres"
        ph, snap, inst = ("%s", '"OptionSnapshot"', '"OptionInstrument"') if pg else ("?", "dbo.OptionSnapshot", "dbo.OptionInstrument")
        try:
            snapshots = self._query_date_range(db,
                f"SELECT MIN(s.trade_date), MAX(s.trade_date), COUNT(1) FROM {snap} s "
                f"JOIN {inst} oi ON oi.id = s.option_instrument_id WHERE oi.underlying = {ph}",
                [symbol])
            instruments = self._query_date_range(db,
                f"SELECT MIN(fetch_date), MAX(fetch_date), COUNT(1) FROM {inst} WHERE underlying = {ph}",
                [symbol])
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
        settings = get_settings()
        tdb = get_database_client(settings)
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
    def _query_date_range(db, sql: str, params: list[Any]) -> dict[str, Any]:
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
