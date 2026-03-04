from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.core.config import get_settings
from src.data.db_client import AzureSqlClient
from scripts.backfill_nifty_options import run_backfill_options
from scripts.backfill_nifty_underlying import run_backfill_underlying
from scripts.backfill_nifty_volumeproxy import run_backfill_volumeproxy

VALID_UNDERLYINGS = {"NIFTY", "BANKNIFTY"}


@dataclass
class BackfillRequest:
    underlying: str
    start_date: date
    end_date: date

    def normalized_underlying(self) -> str:
        underlying = (self.underlying or "").strip().upper()
        if underlying not in VALID_UNDERLYINGS:
            raise ValueError("underlying must be NIFTY or BANKNIFTY")
        return underlying


class IndexBackfillService:
    def run_full_backfill(self, request: BackfillRequest) -> dict[str, Any]:
        underlying = request.normalized_underlying()
        if request.start_date > request.end_date:
            raise ValueError("start_date must be <= end_date")

        underlying_result = run_backfill_underlying(
            start_date=request.start_date,
            end_date=request.end_date,
            underlyings=[underlying],
        )
        options_result = run_backfill_options(
            start_date=request.start_date,
            end_date=request.end_date,
            underlyings=[underlying],
        )
        volumeproxy_result = run_backfill_volumeproxy(
            start_date=request.start_date,
            end_date=request.end_date,
            underlyings=[underlying],
        )

        return {
            "underlying": underlying,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "components": {
                "underlying": underlying_result,
                "options": options_result,
                "volumeproxy": volumeproxy_result,
            },
        }

    def get_underlying_data_range(self, underlying: str) -> dict[str, Any]:
        normalized = self._normalize_underlying(underlying)
        settings = get_settings()
        db = AzureSqlClient(settings)
        db.connect()
        try:
            daily = self._query_date_range(
                db,
                """
                SELECT
                    MIN(CAST(trade_date AS date)) AS min_date,
                    MAX(CAST(trade_date AS date)) AS max_date,
                    COUNT(1) AS row_count
                FROM dbo.UnderlyingSnapshot
                WHERE underlying = ?
                """,
                [normalized],
            )
            intraday = self._query_date_range(
                db,
                """
                SELECT
                    MIN(CAST(trade_date AS date)) AS min_date,
                    MAX(CAST(trade_date AS date)) AS max_date,
                    COUNT(1) AS row_count
                FROM dbo.UnderlyingCandle5m
                WHERE underlying = ?
                """,
                [normalized],
            )
        finally:
            db.close()

        return {
            "underlying": normalized,
            "daily": daily,
            "candles_5m": intraday,
        }

    def get_options_data_range(self, underlying: str) -> dict[str, Any]:
        normalized = self._normalize_underlying(underlying)
        settings = get_settings()
        db = AzureSqlClient(settings)
        db.connect()
        try:
            snapshots = self._query_date_range(
                db,
                """
                SELECT
                    MIN(CAST(s.snapshot_time AS date)) AS min_date,
                    MAX(CAST(s.snapshot_time AS date)) AS max_date,
                    COUNT(1) AS row_count
                FROM dbo.OptionSnapshot AS s
                INNER JOIN dbo.OptionInstrument AS oi
                    ON oi.id = s.option_instrument_id
                WHERE oi.underlying = ?
                """,
                [normalized],
            )
            instruments = self._query_date_range(
                db,
                """
                SELECT
                    MIN(CAST(fetch_date AS date)) AS min_date,
                    MAX(CAST(fetch_date AS date)) AS max_date,
                    COUNT(1) AS row_count
                FROM dbo.OptionInstrument
                WHERE underlying = ?
                """,
                [normalized],
            )
        finally:
            db.close()

        return {
            "underlying": normalized,
            "snapshots": snapshots,
            "instruments": instruments,
        }

    def _normalize_underlying(self, underlying: str) -> str:
        normalized = (underlying or "").strip().upper()
        if normalized not in VALID_UNDERLYINGS:
            raise ValueError("underlying must be NIFTY or BANKNIFTY")
        return normalized

    @staticmethod
    def _query_date_range(db: AzureSqlClient, sql: str, params: list[Any]) -> dict[str, Any]:
        cur = db.conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()

        min_date = row[0] if row else None
        max_date = row[1] if row else None
        row_count = int(row[2]) if row and row[2] is not None else 0

        return {
            "min_date": _to_iso_date(min_date),
            "max_date": _to_iso_date(max_date),
            "row_count": row_count,
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


