# scripts/check_option_snapshot_coverage.py
"""
Diagnostic: verify OptionSnapshot has 2 entries per option instrument per day.

Usage:
    python scripts/check_option_snapshot_coverage.py
    python scripts/check_option_snapshot_coverage.py --underlying NIFTY
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.database_client import DatabaseClient

load_dotenv()


def run_check(underlyings=None):
    settings = get_settings()
    db = DatabaseClient(settings)
    db.connect()
    cursor = db.conn.cursor()

    targets = underlyings or ["NIFTY", "BANKNIFTY"]
    ph = ",".join("?" for _ in targets)

    # --- 1. Overall counts ---
    cursor.execute(
        f"""
        SELECT
            oi.underlying,
            COUNT(DISTINCT os.option_instrument_id)         AS instruments_with_data,
            COUNT(DISTINCT CAST(os.snapshot_time AS DATE))  AS trading_days,
            COUNT(*)                                         AS total_snapshots
        FROM dbo.OptionSnapshot os
        INNER JOIN dbo.OptionInstrument oi ON oi.id = os.option_instrument_id
        WHERE oi.underlying IN ({ph})
        GROUP BY oi.underlying
        """,
        targets,
    )
    print("=== Overall counts ===")
    print(f"{'Underlying':<14} {'Instruments':>12} {'Trading Days':>13} {'Total Snaps':>12}")
    for r in cursor.fetchall():
        print(f"{r[0]:<14} {r[1]:>12,} {r[2]:>13,} {r[3]:>12,}")

    # --- 2. Snapshot time distribution (should be 09:15 and 15:15 only) ---
    cursor.execute(
        f"""
        SELECT DISTINCT CAST(os.snapshot_time AS TIME) AS snap_time, COUNT(*) AS cnt
        FROM dbo.OptionSnapshot os
        INNER JOIN dbo.OptionInstrument oi ON oi.id = os.option_instrument_id
        WHERE oi.underlying IN ({ph})
        GROUP BY CAST(os.snapshot_time AS TIME)
        ORDER BY snap_time
        """,
        targets,
    )
    print("\n=== Snapshot times (should be 09:15 and 15:15 only) ===")
    for r in cursor.fetchall():
        print(f"  {str(r[0]):<12}  {r[1]:,} rows")

    # --- 3. Days with != 2 snapshots per instrument (problem rows) ---
    cursor.execute(
        f"""
        SELECT
            snap_count,
            COUNT(*) AS instrument_days
        FROM (
            SELECT
                os.option_instrument_id,
                CAST(os.snapshot_time AS DATE) AS trade_date,
                COUNT(*) AS snap_count
            FROM dbo.OptionSnapshot os
            INNER JOIN dbo.OptionInstrument oi ON oi.id = os.option_instrument_id
            WHERE oi.underlying IN ({ph})
            GROUP BY os.option_instrument_id, CAST(os.snapshot_time AS DATE)
        ) sub
        GROUP BY snap_count
        ORDER BY snap_count
        """,
        targets,
    )
    print("\n=== Snapshots-per-instrument-per-day distribution ===")
    print(f"  {'Snaps/day':>10}  {'Instrument-days':>16}")
    all_good = True
    for r in cursor.fetchall():
        flag = "" if r[0] == 2 else "  <-- PROBLEM"
        print(f"  {r[0]:>10}  {r[1]:>16,}{flag}")
        if r[0] != 2:
            all_good = False

    if all_good:
        print("\n  All instrument-days have exactly 2 snapshots.")

    # --- 4. Instruments with NO snapshot data ---
    cursor.execute(
        f"""
        SELECT
            oi.underlying,
            COUNT(*)                                            AS total_instruments,
            SUM(CASE WHEN os.cnt IS NULL THEN 1 ELSE 0 END)    AS no_data,
            SUM(CASE WHEN os.cnt IS NOT NULL THEN 1 ELSE 0 END) AS has_data
        FROM dbo.OptionInstrument oi
        LEFT JOIN (
            SELECT option_instrument_id, COUNT(*) AS cnt
            FROM dbo.OptionSnapshot
            GROUP BY option_instrument_id
        ) os ON os.option_instrument_id = oi.id
        WHERE oi.underlying IN ({ph})
        GROUP BY oi.underlying
        """,
        targets,
    )
    print("\n=== Instruments with no snapshot data ===")
    print(f"  {'Underlying':<14} {'Total':>8} {'Has Data':>10} {'No Data':>10}")
    for r in cursor.fetchall():
        print(f"  {r[0]:<14} {r[1]:>8,} {r[3]:>10,} {r[2]:>10,}")

    # --- 5. No-data breakdown: expiry buckets ---
    cursor.execute(
        f"""
        SELECT
            oi.underlying,
            CASE
                WHEN oi.expiry < CAST(GETDATE() AS DATE) THEN 'Expired'
                ELSE 'Active (future expiry)'
            END AS expiry_status,
            COUNT(*) AS instrument_count,
            MIN(oi.expiry) AS earliest_expiry,
            MAX(oi.expiry) AS latest_expiry
        FROM dbo.OptionInstrument oi
        WHERE oi.underlying IN ({ph})
          AND NOT EXISTS (
              SELECT 1 FROM dbo.OptionSnapshot os WHERE os.option_instrument_id = oi.id
          )
        GROUP BY oi.underlying,
                 CASE WHEN oi.expiry < CAST(GETDATE() AS DATE) THEN 'Expired' ELSE 'Active (future expiry)' END
        ORDER BY oi.underlying, expiry_status
        """,
        targets,
    )
    rows = cursor.fetchall()
    if rows:
        print("\n=== No-data instruments: expiry breakdown ===")
        print(f"  {'Underlying':<14} {'Status':<24} {'Count':>7} {'Earliest Expiry':>16} {'Latest Expiry':>14}")
        for r in rows:
            print(f"  {r[0]:<14} {r[1]:<24} {r[2]:>7,} {str(r[3])[:10]:>16} {str(r[4])[:10]:>14}")
    else:
        print("\n  All instruments have at least one snapshot.")

    # --- 6. Show the specific instruments with no data (when count is small) ---
    cursor.execute(
        f"""
        SELECT oi.id, oi.underlying, oi.tradingsymbol, oi.strike, oi.expiry, oi.instrument_type
        FROM dbo.OptionInstrument oi
        WHERE oi.underlying IN ({ph})
          AND NOT EXISTS (
              SELECT 1 FROM dbo.OptionSnapshot os WHERE os.option_instrument_id = oi.id
          )
        ORDER BY oi.underlying, oi.expiry, oi.tradingsymbol
        """,
        targets,
    )
    no_data_rows = cursor.fetchall()
    if no_data_rows:
        print(f"\n=== Instruments with no snapshot data ({len(no_data_rows)} total) ===")
        print(f"  {'ID':>6}  {'Underlying':<14} {'TradingSymbol':<30} {'Strike':>8} {'Expiry':<12} {'Type'}")
        for r in no_data_rows:
            print(f"  {r[0]:>6}  {r[1]:<14} {r[2]:<30} {float(r[3]):>8.0f} {str(r[4])[:10]:<12} {r[5]}")

    # --- 7. Date range in DB ---
    cursor.execute(
        f"""
        SELECT
            oi.underlying,
            CAST(MIN(os.snapshot_time) AS DATE) AS first_date,
            CAST(MAX(os.snapshot_time) AS DATE) AS last_date
        FROM dbo.OptionSnapshot os
        INNER JOIN dbo.OptionInstrument oi ON oi.id = os.option_instrument_id
        WHERE oi.underlying IN ({ph})
        GROUP BY oi.underlying
        """,
        targets,
    )
    print("\n=== Date range in OptionSnapshot ===")
    for r in cursor.fetchall():
        print(f"  {r[0]:<14}  {r[1]}  ->  {r[2]}")

    cursor.close()
    db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--underlying", default=None, help="Comma-separated (e.g. NIFTY,BANKNIFTY)")
    args = parser.parse_args()
    underlyings = [u.strip().upper() for u in args.underlying.split(",")] if args.underlying else None
    run_check(underlyings)
