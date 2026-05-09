from __future__ import annotations

from typing import Optional, Dict
import pandas as pd

from .option_selection_common import _common_filter, build_selection_output

STRATEGY_NAME = "nearestExpiryHighOI"


def select(chain_df: pd.DataFrame, prediction: str, trade_date: pd.Timestamp) -> Optional[Dict]:
    """Nearest-expiry option with highest OI."""
    df = _common_filter(chain_df, prediction, trade_date)
    if df.empty:
        return None

    min_days = df["days_to_expiry"].min()
    df = df[df["days_to_expiry"] == min_days]

    if "open_interest" in df.columns:
        df = df.sort_values("open_interest", ascending=False)
    elif "option_volume" in df.columns:
        df = df.sort_values("option_volume", ascending=False)
    else:
        df = df.sort_values("strike")

    row = df.iloc[0]
    return build_selection_output(row, prediction, row["_trade_date_norm"])
