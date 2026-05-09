from __future__ import annotations

from typing import Optional, Dict
import pandas as pd

from .option_selection_common import _common_filter, build_selection_output

STRATEGY_NAME = "highestDeltaPriceRatio"


def select(chain_df: pd.DataFrame, prediction: str, trade_date: pd.Timestamp) -> Optional[Dict]:
    """Highest |delta| / option_price, with longer expiry as tiebreaker."""
    df = _common_filter(chain_df, prediction, trade_date)
    if df.empty or "delta" not in df.columns:
        return None

    df = df[df["delta"].notna()]
    if df.empty:
        return None

    df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
    df = df[df["delta"].notna()]
    if df.empty:
        return None

    df["delta_abs"] = df["delta"].abs()
    df["delta_price_ratio"] = df["delta_abs"] / df["option_price"]
    df = df.sort_values(["delta_price_ratio", "days_to_expiry"], ascending=[False, False])

    row = df.iloc[0]
    return build_selection_output(row, prediction, row["_trade_date_norm"])
