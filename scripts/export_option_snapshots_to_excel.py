# scripts/export_option_snapshots_to_excel.py
"""
Export NIFTY option snapshot history into an Excel workbook.

Optional token CSV format, no header. Both orders are accepted:
    instrument_token,tradingsymbol
    tradingsymbol,instrument_token

Rows are queried from Supabase/Postgres tables:
OptionInstrument, OptionSnapshot, OptionSnapshotCalc, and UnderlyingSnapshot.
Historical and live rows are merged by instrument + trade_date + snapshot label,
preferring live Kite quotes when both sources are present.
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

load_dotenv()

DEFAULT_COLUMNS = [
    "option_snapshot_id",
    "option_instrument_id",
    "instrument_token",
    "underlying",
    "tradingsymbol",
    "strike",
    "expiry",
    "instrument_type",
    "snapshot_time",
    "snapshot_label",
    "trade_date",
    "data_source",
    "nifty_close_price",
    "option_price",
    "bid_price",
    "ask_price",
    "volume",
    "open_interest",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
]

POSTGRES_QUERY = """
WITH ranked_snapshots AS (
    SELECT
        os.id AS option_snapshot_id,
        oi.id AS option_instrument_id,
        oi.instrument_token,
        oi.underlying,
        oi.tradingsymbol,
        oi.strike,
        oi.expiry,
        oi.instrument_type,
        os.snapshot_time,
        CASE
            WHEN os.snapshot_label IS NULL
             AND os.snapshot_time::time = TIME '15:15:00'
                THEN 'CLOSE_1515'
            ELSE os.snapshot_label
        END AS snapshot_label,
        os.trade_date,
        os.data_source,
        us.close_price AS nifty_close_price,
        os.last_price AS option_price,
        os.bid_price,
        os.ask_price,
        os.volume,
        os.open_interest,
        osc.implied_volatility,
        osc.delta,
        osc.gamma,
        osc.theta,
        osc.vega,
        ROW_NUMBER() OVER (
            PARTITION BY
                oi.id,
                os.trade_date,
                CASE
                    WHEN os.snapshot_label IS NULL
                     AND os.snapshot_time::time = TIME '15:15:00'
                        THEN 'CLOSE_1515'
                    ELSE os.snapshot_label
                END
            ORDER BY
                CASE
                    WHEN os.data_source = 'KITE_QUOTE_LIVE' THEN 0
                    WHEN os.data_source = 'KITE_HISTORICAL_5M_CLOSE_PROXY' THEN 1
                    ELSE 2
                END,
                os.snapshot_time DESC,
                os.id DESC
        ) AS source_rank
    FROM "OptionInstrument" oi
    JOIN "OptionSnapshot" os
      ON os.option_instrument_id = oi.id
    LEFT JOIN "OptionSnapshotCalc" osc
      ON osc.option_snapshot_id = os.id
    LEFT JOIN "UnderlyingSnapshot" us
      ON us.underlying = oi.underlying
     AND us.trade_date = os.trade_date
    WHERE oi.underlying = %(underlying)s
      AND (%(instrument_token)s IS NULL OR oi.instrument_token = %(instrument_token)s)
      AND os.trade_date >= %(start_date)s
      AND os.trade_date <= %(end_date)s
      AND os.data_source IN ('KITE_QUOTE_LIVE', 'KITE_HISTORICAL_5M_CLOSE_PROXY')
      AND (
          %(snapshot_label)s = 'all'
          OR (
              %(snapshot_label)s = 'close'
              AND (
                  UPPER(COALESCE(os.snapshot_label, '')) = 'CLOSE_1515'
                  OR (
                      os.snapshot_label IS NULL
                      AND os.snapshot_time::time = TIME '15:15:00'
                  )
              )
          )
          OR (
              %(snapshot_label)s = 'open'
              AND UPPER(COALESCE(os.snapshot_label, '')) = 'OPEN_0915'
          )
          OR LOWER(COALESCE(os.snapshot_label, '')) = %(snapshot_label)s
      )
)
SELECT
    option_snapshot_id,
    option_instrument_id,
    instrument_token,
    underlying,
    tradingsymbol,
    strike,
    expiry,
    instrument_type,
    snapshot_time,
    snapshot_label,
    trade_date,
    data_source,
    nifty_close_price,
    option_price,
    bid_price,
    ask_price,
    volume,
    open_interest,
    implied_volatility,
    delta,
    gamma,
    theta,
    vega
FROM ranked_snapshots
WHERE source_rank = 1
ORDER BY trade_date, snapshot_label, expiry, strike, instrument_type, tradingsymbol;
"""


def read_tokens(path: Path) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row_no, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) < 2:
                raise ValueError(f"{path}:{row_no} must have instrument_token,tradingsymbol")
            first = row[0].strip()
            second = row[1].strip()
            if first.isdigit():
                tokens.append((int(first), second.upper()))
            elif second.isdigit():
                tokens.append((int(second), first.upper()))
            else:
                raise ValueError(
                    f"{path}:{row_no} must contain one numeric instrument_token and one tradingsymbol"
                )
    return tokens


def sheet_name_for(symbol: str, used: set[str]) -> str:
    clean = re.sub(r"[\[\]\:\*\?\/\\]", "_", symbol).strip() or "Sheet"
    clean = clean[:31]
    candidate = clean
    suffix = 1
    while candidate in used:
        suffix_text = f"_{suffix}"
        candidate = f"{clean[:31 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used.add(candidate)
    return candidate


def export_option_snapshots(
    tokens_csv: Path | None,
    output_xlsx: Path,
    start_date: date,
    end_date: date,
    underlying: str = "NIFTY",
    snapshot_label: str = "all",
) -> dict[str, object]:
    tokens = read_tokens(tokens_csv) if tokens_csv else []
    if tokens_csv and not tokens:
        raise ValueError(f"No tokens found in {tokens_csv}")

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    sheet_counts: dict[str, int] = {}
    try:
        with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
            used_sheet_names: set[str] = set()
            if not tokens:
                df = pd.read_sql_query(
                    POSTGRES_QUERY,
                    db.conn,
                    params={
                        "instrument_token": None,
                        "underlying": underlying,
                        "snapshot_label": snapshot_label.lower(),
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )
                if df.empty:
                    df = pd.DataFrame(columns=DEFAULT_COLUMNS)
                df.to_excel(writer, sheet_name="NIFTY", index=False)
                sheet_counts["NIFTY"] = len(df)
                print(f"NIFTY: {len(df)} rows")
            else:
                for instrument_token, tradingsymbol in tokens:
                    df = pd.read_sql_query(
                        POSTGRES_QUERY,
                        db.conn,
                        params={
                            "instrument_token": instrument_token,
                            "underlying": underlying,
                            "snapshot_label": snapshot_label.lower(),
                            "start_date": start_date,
                            "end_date": end_date,
                        },
                    )
                    if df.empty:
                        df = pd.DataFrame(columns=DEFAULT_COLUMNS)
                    sheet_name = sheet_name_for(tradingsymbol, used_sheet_names)
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    sheet_counts[tradingsymbol] = len(df)
                    print(f"{tradingsymbol}: {len(df)} rows")
    finally:
        db.close()

    return {
        "tokens_csv": str(tokens_csv) if tokens_csv else None,
        "output_xlsx": str(output_xlsx),
        "underlying": underlying,
        "snapshot_label": snapshot_label,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "tabs": len(tokens) if tokens else 1,
        "rows_by_tradingsymbol": sheet_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export NIFTY option snapshots for token CSV to Excel.")
    parser.add_argument(
        "--tokens-csv",
        default=None,
        help="Optional CSV with instrument_token,tradingsymbol rows. If omitted, exports all matching rows.",
    )
    parser.add_argument("--output", required=True, help="Output .xlsx path")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying filter, default NIFTY")
    parser.add_argument(
        "--snapshot-label",
        default="all",
        help="Snapshot label to export: all, close, open, or an exact label. Default: all",
    )
    args = parser.parse_args()

    result = export_option_snapshots(
        tokens_csv=Path(args.tokens_csv) if args.tokens_csv else None,
        output_xlsx=Path(args.output),
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        underlying=args.underlying.upper(),
        snapshot_label=args.snapshot_label,
    )
    print(result)


if __name__ == "__main__":
    main()
