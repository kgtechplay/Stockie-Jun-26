# scripts/export_option_snapshots_to_excel.py
"""
Export NIFTY option snapshot history for a token CSV into an Excel workbook.

Input CSV format, no header. Both orders are accepted:
    instrument_token,tradingsymbol
    tradingsymbol,instrument_token

Each token is queried from OptionInstrument/OptionSnapshot/OptionSnapshotCalc.
By default, the "close" option snapshot is exported, with the NIFTY close
price for that trade date. If older rows do not have snapshot_label populated,
the query falls back to exact 15:15:00 rows. Each token gets one worksheet
named after its tradingsymbol.
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
    "option_instrument_id",
    "instrument_token",
    "underlying",
    "tradingsymbol",
    "strike",
    "expiry",
    "instrument_type",
    "snapshot_time",
    "trade_date",
    "nifty_close_price",
    "option_price",
    "volume",
    "open_interest",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
]

QUERY = """
SELECT
    oi.id AS option_instrument_id,
    oi.instrument_token,
    oi.underlying,
    oi.tradingsymbol,
    oi.strike,
    oi.expiry,
    oi.instrument_type,
    os.snapshot_time,
    os.snapshot_label,
    CAST(os.snapshot_time AS DATE) AS trade_date,
    us.close_price AS nifty_close_price,
    os.last_price AS option_price,
    os.volume,
    os.open_interest,
    osc.implied_volatility,
    osc.delta,
    osc.gamma,
    osc.theta,
    osc.vega
FROM dbo.OptionInstrument oi
JOIN dbo.OptionSnapshot os
    ON os.option_instrument_id = oi.id
LEFT JOIN dbo.OptionSnapshotCalc osc
    ON osc.option_snapshot_id = os.id
LEFT JOIN dbo.UnderlyingSnapshot us
    ON us.underlying = oi.underlying
   AND us.trade_date = CAST(os.snapshot_time AS DATE)
WHERE oi.instrument_token = ?
  AND oi.underlying = ?
  AND (
      LOWER(COALESCE(os.snapshot_label, '')) = LOWER(?)
      OR (LOWER(?) = 'close' AND UPPER(COALESCE(os.snapshot_label, '')) = 'CLOSE_1515')
      OR (os.snapshot_label IS NULL AND CAST(os.snapshot_time AS TIME) = '15:15:00')
  )
  AND os.snapshot_time >= ?
  AND os.snapshot_time < DATEADD(DAY, 1, ?)
ORDER BY os.snapshot_time;
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
    tokens_csv: Path,
    output_xlsx: Path,
    start_date: date,
    end_date: date,
    underlying: str = "NIFTY",
    snapshot_label: str = "close",
) -> dict[str, object]:
    tokens = read_tokens(tokens_csv)
    if not tokens:
        raise ValueError(f"No tokens found in {tokens_csv}")

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    sheet_counts: dict[str, int] = {}
    try:
        with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
            used_sheet_names: set[str] = set()
            for instrument_token, tradingsymbol in tokens:
                df = pd.read_sql_query(
                    QUERY,
                    db.conn,
                    params=[instrument_token, underlying, snapshot_label, snapshot_label, start_date, end_date],
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
        "tokens_csv": str(tokens_csv),
        "output_xlsx": str(output_xlsx),
        "underlying": underlying,
        "snapshot_label": snapshot_label,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "tabs": len(tokens),
        "rows_by_tradingsymbol": sheet_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export NIFTY option snapshots for token CSV to Excel.")
    parser.add_argument("--tokens-csv", required=True, help="CSV with instrument_token,tradingsymbol rows")
    parser.add_argument("--output", required=True, help="Output .xlsx path")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying filter, default NIFTY")
    parser.add_argument("--snapshot-label", default="close", help="Snapshot label to export, default close")
    args = parser.parse_args()

    result = export_option_snapshots(
        tokens_csv=Path(args.tokens_csv),
        output_xlsx=Path(args.output),
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        underlying=args.underlying.upper(),
        snapshot_label=args.snapshot_label,
    )
    print(result)


if __name__ == "__main__":
    main()
