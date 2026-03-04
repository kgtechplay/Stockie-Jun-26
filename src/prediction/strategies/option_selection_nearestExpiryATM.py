from __future__ import annotations

from typing import Optional, Dict
import pandas as pd

from .option_selection_common import _common_filter, build_selection_output

STRATEGY_NAME = "nearestExpiryATM"


def select(chain_df: pd.DataFrame, prediction: str, trade_date: pd.Timestamp) -> Optional[Dict]:
    """Nearest-expiry ATM with volume/OI tie-breakers."""
    df = _common_filter(chain_df, prediction, trade_date)
    if df.empty:
        return None

    min_days = df["days_to_expiry"].min()
    df = df[df["days_to_expiry"] == min_days]

    underlying_series = df["underlying_price"].dropna()
    if underlying_series.empty:
        return None
    underlying_price = float(underlying_series.iloc[0])

    df["moneyness"] = (df["strike"] - underlying_price).abs()
    min_m = df["moneyness"].min()
    df = df[df["moneyness"] == min_m]

    if "option_volume" in df.columns and "open_interest" in df.columns:
        df = df.sort_values(["option_volume", "open_interest"], ascending=[False, False])
    elif "open_interest" in df.columns:
        df = df.sort_values("open_interest", ascending=False)
    else:
        df = df.sort_values("strike")

    row = df.iloc[0]
    return build_selection_output(row, prediction, row["_trade_date_norm"])

