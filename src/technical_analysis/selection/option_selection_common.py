from __future__ import annotations

from typing import Callable, Dict, Optional

import pandas as pd

OptionSelectionFunction = Callable[[pd.DataFrame, str, pd.Timestamp], Optional[Dict]]


def _common_filter(chain_df: pd.DataFrame, prediction: str, trade_date: pd.Timestamp) -> pd.DataFrame:
    if prediction not in ("CALL", "PUT") or chain_df.empty:
        return pd.DataFrame()
    df = chain_df.copy()
    df = df[df["option_side"] == prediction]
    if df.empty:
        return df
    trade_date_norm = pd.to_datetime(trade_date).normalize()
    df = df[df["expiry"] > trade_date_norm]
    if df.empty:
        return df
    df = df[df["option_price"] > 0]
    if df.empty:
        return df
    df["days_to_expiry"] = (df["expiry"] - trade_date_norm).dt.days
    df["_trade_date_norm"] = trade_date_norm
    return df


def build_selection_output(row: pd.Series, prediction: str, trade_date_norm: pd.Timestamp) -> Dict:
    return {
        "option_trade_date": trade_date_norm,
        "option_instrument_token": int(row["instrument_token"]),
        "option_tradingsymbol": row["tradingsymbol"],
        "option_strike": float(row["strike"]),
        "option_expiry": row["expiry"],
        "option_type": prediction,
        "selection_option_price_1515": float(row["option_price"]),
    }
