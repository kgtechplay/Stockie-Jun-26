from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.Common.index_data_db import (  # noqa: E402
    create_index_data_table,
    create_postgres_engine,
    resolve_incremental_start_date,
    upsert_index_data,
)
from scripts.Common.index_data_loader import get_index_ohlc_data  # noqa: E402


INITIAL_LOOKBACK_DAYS = 7


def load_incremental_index_data() -> int:
    engine = create_postgres_engine()
    create_index_data_table(engine)

    today = date.today()
    fallback_start_date = today - timedelta(days=INITIAL_LOOKBACK_DAYS)
    start_date = resolve_incremental_start_date(engine, fallback_start_date)
    end_date = today + timedelta(days=1)

    print(
        "Loading index data from "
        f"{start_date.isoformat()} to {end_date.isoformat()} "
        f"(exclusive end date from yfinance)."
    )

    index_df = get_index_ohlc_data(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )

    if index_df.empty:
        print("No index data returned from source.")
        return 0

    rows_loaded = upsert_index_data(engine, index_df)
    print(f"Loaded {rows_loaded} index rows.")
    return rows_loaded


if __name__ == "__main__":
    load_incremental_index_data()
