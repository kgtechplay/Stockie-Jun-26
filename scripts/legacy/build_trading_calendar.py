# scripts/build_trading_calendar.py
"""
Populate dbo.TradingCalendar for NSE using the exchange_calendars library,
which ships with the full NSE holiday schedule (proactive, not reactive).

Install dependency if needed:
    pip install exchange-calendars

Weekly expiry flags:
  - Thursday  -> NIFTY weekly expiry   (is_weekly_expiry = 1)
  - Wednesday -> BANKNIFTY weekly expiry (is_weekly_expiry = 1)

Monthly expiry flag:
  - Last Thursday of each month that is also a trading day -> is_monthly_expiry = 1
  - If last Thursday is a holiday, uses the preceding Thursday.

Usage:
    python scripts/build_trading_calendar.py
    python scripts/build_trading_calendar.py --start 2025-01-01 --end 2026-12-31
"""

import sys
from pathlib import Path
from datetime import date, timedelta
from typing import Set

from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

load_dotenv()

EXCHANGE = "NSE"


def _get_nse_trading_days(start: date, end: date) -> Set[date]:
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XBOM")
        sessions = cal.sessions_in_range(
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
        return {d.date() for d in sessions}
    except ImportError:
        raise SystemExit(
            "[ERROR] exchange-calendars is not installed.\n"
            "Run: pip install exchange-calendars"
        )


def _last_thursday_of_month(year: int, month: int, trading_days: Set[date]) -> date | None:
    """Last Thursday of the month that is a trading day; steps back weekly if needed."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    d = last_day
    while d.month == month:
        if d.weekday() == 3 and d in trading_days:  # Thursday
            return d
        d -= timedelta(days=1)
    return None


def build_calendar(start: date, end: date) -> None:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()

    print(f"Fetching NSE trading days ({start} to {end}) from exchange_calendars...")
    trading_days = _get_nse_trading_days(start, end)
    print(f"  {len(trading_days)} trading days found (holidays already excluded)")

    # Build monthly expiry set (last Thursday of each month that is a trading day)
    monthly_expiry_thursdays: Set[date] = set()
    for year in range(start.year, end.year + 1):
        for month in range(1, 13):
            d = _last_thursday_of_month(year, month, trading_days)
            if d and start <= d <= end:
                monthly_expiry_thursdays.add(d)

    # Generate all weekdays in range
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri only
            is_trading = d in trading_days
            # Wed (2) = BANKNIFTY expiry, Thu (3) = NIFTY expiry
            is_weekly = is_trading and d.weekday() in (2, 3)
            is_monthly = is_trading and d in monthly_expiry_thursdays
            rows.append({
                "calendar_date": d,
                "exchange": EXCHANGE,
                "is_trading_day": is_trading,
                "is_weekly_expiry": is_weekly,
                "is_monthly_expiry": is_monthly,
                "is_special_session": False,
                "notes": None,
            })
        d += timedelta(days=1)

    print(f"Upserting {len(rows)} calendar rows...")
    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        db.upsert_trading_calendar(rows[i:i + batch_size])

    db.close()

    trading = sum(1 for r in rows if r["is_trading_day"])
    holidays = sum(1 for r in rows if not r["is_trading_day"])
    weekly = sum(1 for r in rows if r["is_weekly_expiry"])
    monthly = sum(1 for r in rows if r["is_monthly_expiry"])
    print("Done.")
    print(f"  Trading days : {trading}")
    print(f"  Holidays     : {holidays}  (NSE published holiday list)")
    print(f"  Weekly expiry: {weekly}  (Wed + Thu trading days)")
    print(f"  Monthly expiry:{monthly} (last Thu of each month)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Populate TradingCalendar from NSE exchange calendar")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-12-31")
    args = parser.parse_args()
    build_calendar(date.fromisoformat(args.start), date.fromisoformat(args.end))
