"""Refresh cached NIFTY50 sector weights from NSE's official constituent CSV.

Run:
    python scripts/daily_NIFTY/refresh_nifty50_sector_weights.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.news_sentiment.config import NIFTY50_SECTOR_WEIGHTS_STORE
from src.news_sentiment.sector_weights import refresh_nifty50_sector_weights


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh NIFTY50 sector weights from NSE.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds. Default: 20")
    args = parser.parse_args()

    weights = refresh_nifty50_sector_weights(timeout_seconds=args.timeout)
    print(weights.to_string(index=False))
    print(f"Wrote {len(weights)} sector row(s) to {NIFTY50_SECTOR_WEIGHTS_STORE}")


if __name__ == "__main__":
    main()