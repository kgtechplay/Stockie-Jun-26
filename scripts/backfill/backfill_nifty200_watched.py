from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill.backfill_NIFTYoptions_from_historical import run_backfill_options_from_historical
from scripts.backfill.backfill_underlying import run_backfill_underlying_data
from scripts.daily.daily_optionInstrument_refresh import run_load_option_instruments
from src.common.config import get_settings
from src.common.models import WatchedInstrument
from src.data_manager.db.client_factory import get_database_client


DEFAULT_CSV = PROJECT_ROOT / "nifty200_stocks_universe.csv"
DEFAULT_PROGRESS_LOG = PROJECT_ROOT / "output" / "nifty200_backfill_progress.jsonl"


def parse_bool(value: Any) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def load_watched_rows(csv_path: Path) -> list[WatchedInstrument]:
    df = pd.read_csv(csv_path)
    required = {
        "tradingsymbol",
        "exchange",
        "name",
        "instrument_token",
        "segment",
        "tick_size",
        "lot_size",
        "instrument_type",
        "sector",
        "industry",
        "is_fo_enabled",
        "is_active",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path} missing required columns: {', '.join(sorted(missing))}")

    rows: list[WatchedInstrument] = []
    for _, r in df.iterrows():
        symbol = str(r["tradingsymbol"]).strip().upper()
        if not symbol:
            continue
        rows.append(
            WatchedInstrument(
                tradingsymbol=symbol,
                exchange=str(r["exchange"]).strip().upper() or "NSE",
                name=str(r["name"]).strip() if pd.notna(r["name"]) else None,
                instrument_token=int(r["instrument_token"]) if pd.notna(r["instrument_token"]) else None,
                segment=str(r["segment"]).strip() if pd.notna(r["segment"]) else None,
                tick_size=float(r["tick_size"]) if pd.notna(r["tick_size"]) else None,
                lot_size=int(r["lot_size"]) if pd.notna(r["lot_size"]) else None,
                instrument_type=str(r["instrument_type"]).strip().upper() or "STOCK",
                sector=str(r["sector"]).strip() if pd.notna(r["sector"]) else None,
                industry=str(r["industry"]).strip() if pd.notna(r["industry"]) else None,
                is_fo_enabled=parse_bool(r["is_fo_enabled"]),
                is_active=parse_bool(r["is_active"]),
            )
        )
    return rows


def upsert_watched(rows: list[WatchedInstrument], batch_size: int) -> int:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    total = 0
    try:
        for batch in chunked(rows, batch_size):
            total += db.upsert_watched_instruments(batch)
            db.conn.commit()
    finally:
        db.close()
    return total


def load_watched_stock_symbols(only_symbols: list[str] | None = None) -> list[str]:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        watched = db.get_watched_instruments(instrument_type="STOCK")
    finally:
        db.close()

    symbols = [row.tradingsymbol.upper() for row in watched if row.is_active]
    if only_symbols:
        allowed = {s.strip().upper() for s in only_symbols if s.strip()}
        symbols = [symbol for symbol in symbols if symbol in allowed]
    return sorted(set(symbols))


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def append_progress(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def load_completed_symbols(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != "batch_done":
                continue
            completed.update(str(symbol).upper() for symbol in record.get("symbols", []))
    return completed


def run_job(
    csv_path: Path | None,
    start_date: date,
    end_date: date,
    batch_size: int,
    sleep_seconds: float,
    progress_log: Path,
    skip_options_refresh: bool,
    only_symbols: list[str] | None,
    resume: bool,
    upsert_csv: bool = False,
) -> dict[str, Any]:
    upserted = 0
    if upsert_csv:
        if csv_path is None:
            raise ValueError("csv_path is required when upsert_csv=True")
        rows = load_watched_rows(csv_path)
        if only_symbols:
            allowed = {s.strip().upper() for s in only_symbols if s.strip()}
            rows = [row for row in rows if row.tradingsymbol in allowed]
        upserted = upsert_watched(rows, batch_size=batch_size)
        symbols = [row.tradingsymbol for row in rows]
    else:
        symbols = load_watched_stock_symbols(only_symbols)

    started = time.time()
    append_progress(
        progress_log,
        {
            "event": "job_start",
            "source": "csv" if upsert_csv else "watched_instruments",
            "csv_path": str(csv_path) if csv_path is not None else None,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "symbols": len(symbols),
            "batch_size": batch_size,
        },
    )

    if upsert_csv:
        append_progress(progress_log, {"event": "watched_upserted", "count": upserted})

    option_refresh_result = None
    if not skip_options_refresh:
        option_refresh_result = run_load_option_instruments(
            instrument_type="STOCK",
            underlyings=symbols,
        )
        append_progress(progress_log, {"event": "option_instruments_loaded", "result": option_refresh_result})

    batch_results: list[dict[str, Any]] = []
    completed_symbols = load_completed_symbols(progress_log) if resume else set()
    pending_symbols = [symbol for symbol in symbols if symbol not in completed_symbols]
    if completed_symbols:
        append_progress(
            progress_log,
            {
                "event": "resume",
                "completed_symbols": len(completed_symbols),
                "pending_symbols": len(pending_symbols),
            },
        )

    symbol_batches = chunked(pending_symbols, batch_size)
    for idx, batch in enumerate(symbol_batches, 1):
        batch_record: dict[str, Any] = {
            "event": "batch_start",
            "batch": idx,
            "batch_count": len(symbol_batches),
            "symbols": batch,
        }
        append_progress(progress_log, batch_record)

        underlying_result = run_backfill_underlying_data(
            instrument_type="STOCK",
            start_date=start_date,
            end_date=end_date,
            underlyings=batch,
        )
        append_progress(
            progress_log,
            {
                "event": "batch_underlying_done",
                "batch": idx,
                "result": underlying_result,
            },
        )

        options_result = run_backfill_options_from_historical(
            global_start=start_date,
            global_end=end_date,
            underlyings=batch,
        )
        done_record = {
            "event": "batch_done",
            "batch": idx,
            "symbols": batch,
            "underlying": underlying_result,
            "options": options_result,
        }
        append_progress(progress_log, done_record)
        batch_results.append(done_record)

        if idx < len(symbol_batches) and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    result = {
        "source": "csv" if upsert_csv else "watched_instruments",
        "csv_path": str(csv_path) if csv_path is not None else None,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": symbols,
        "pending_symbols": pending_symbols,
        "upserted": upserted,
        "option_refresh": option_refresh_result,
        "batches": len(batch_results),
        "elapsed_seconds": round(time.time() - started, 2),
        "progress_log": str(progress_log),
    }
    append_progress(progress_log, {"event": "job_done", **result})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run batched stock underlying + option backfill for active WatchedInstrument STOCK rows."
    )
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to nifty200_stocks_universe.csv when --upsert-csv is used.")
    parser.add_argument("--upsert-csv", action="store_true", help="Upsert WatchedInstrument rows from CSV before backfilling.")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD. Defaults to today - 90 days.")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of stocks per backfill batch.")
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds to sleep between batches.")
    parser.add_argument("--progress-log", default=str(DEFAULT_PROGRESS_LOG), help="JSONL progress log path.")
    parser.add_argument("--skip-options-refresh", action="store_true", help="Skip OptionInstrument refresh.")
    parser.add_argument("--no-resume", action="store_true", help="Do not skip symbols already completed in the progress log.")
    parser.add_argument("--symbols", default=None, help="Optional comma-separated symbol allow-list for testing/resume.")
    args = parser.parse_args()

    today = date.today()
    start_date = date.fromisoformat(args.start) if args.start else today - timedelta(days=90)
    end_date = date.fromisoformat(args.end) if args.end else today
    only_symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    result = run_job(
        csv_path=Path(args.csv) if args.csv else None,
        start_date=start_date,
        end_date=end_date,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep,
        progress_log=Path(args.progress_log),
        skip_options_refresh=args.skip_options_refresh,
        only_symbols=only_symbols,
        resume=not args.no_resume,
        upsert_csv=args.upsert_csv,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
