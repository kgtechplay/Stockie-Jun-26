# selection_logic.py
"""
Option selection strategy functions and registry for selecting optimal option contracts.

This module contains all selection strategy implementations and the strategy registry.
"""

from typing import Callable, Dict, Optional
import pandas as pd

# Type definition for selection functions
# Input: chain_df (pd.DataFrame), prediction (str), trade_date (pd.Timestamp)
# Output: dict with option details or None
SelectionFunction = Callable[[pd.DataFrame, str, pd.Timestamp], Optional[Dict]]


def select_nearest_expiry_atm(chain_df: pd.DataFrame,
                               prediction: str,
                               trade_date: pd.Timestamp) -> Optional[Dict]:
    """
    Strategy 1: Nearest expiry, ATM strike, highest volume/OI.
    Original strategy - selects option with:
    - Nearest expiry date
    - Closest to ATM (At The Money)
    - Highest volume and open interest
    """
    if prediction not in ("CALL", "PUT") or chain_df.empty:
        return None

    df = chain_df.copy()
    df = df[df["option_side"] == prediction]
    if df.empty:
        return None

    trade_date_norm = pd.to_datetime(trade_date).normalize()

    df = df[df["expiry"] > trade_date_norm]
    if df.empty:
        return None

    df = df[df["option_price"] > 0]
    if df.empty:
        return None

    df["days_to_expiry"] = (df["expiry"] - trade_date_norm).dt.days
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

    return {
        "option_trade_date": trade_date_norm,
        "option_instrument_token": int(row["instrument_token"]),
        "option_tradingsymbol": row["tradingsymbol"],
        "option_strike": float(row["strike"]),
        "option_expiry": row["expiry"],
        "option_type": prediction,
        "selection_option_price_1515": float(row["option_price"]),
    }


def select_nearest_expiry_high_oi(chain_df: pd.DataFrame,
                                   prediction: str,
                                   trade_date: pd.Timestamp) -> Optional[Dict]:
    """
    Strategy 2: Nearest expiry, highest open interest (ignores moneyness).
    Selects option with:
    - Nearest expiry date
    - Highest open interest (regardless of strike)
    """
    if prediction not in ("CALL", "PUT") or chain_df.empty:
        return None

    df = chain_df.copy()
    df = df[df["option_side"] == prediction]
    if df.empty:
        return None

    trade_date_norm = pd.to_datetime(trade_date).normalize()

    df = df[df["expiry"] > trade_date_norm]
    if df.empty:
        return None

    df = df[df["option_price"] > 0]
    if df.empty:
        return None

    df["days_to_expiry"] = (df["expiry"] - trade_date_norm).dt.days
    min_days = df["days_to_expiry"].min()
    df = df[df["days_to_expiry"] == min_days]

    if "open_interest" in df.columns:
        df = df.sort_values("open_interest", ascending=False)
    elif "option_volume" in df.columns:
        df = df.sort_values("option_volume", ascending=False)
    else:
        df = df.sort_values("strike")

    row = df.iloc[0]

    return {
        "option_trade_date": trade_date_norm,
        "option_instrument_token": int(row["instrument_token"]),
        "option_tradingsymbol": row["tradingsymbol"],
        "option_strike": float(row["strike"]),
        "option_expiry": row["expiry"],
        "option_type": prediction,
        "selection_option_price_1515": float(row["option_price"]),
    }


def select_highest_delta_price_ratio(chain_df: pd.DataFrame,
                             prediction: str,
                             trade_date: pd.Timestamp) -> Optional[Dict]:
    """
    Strategy: Highest delta/price ratio with long time to expiry.
    Selects option with:
    - Highest delta/option_price ratio
    - Prefers options with longer time to expiry (as tiebreaker)
    - Only considers options with valid delta and price > 0
    """
    if prediction not in ("CALL", "PUT") or chain_df.empty:
        return None

    df = chain_df.copy()
    df = df[df["option_side"] == prediction]
    if df.empty:
        return None

    trade_date_norm = pd.to_datetime(trade_date).normalize()

    df = df[df["expiry"] > trade_date_norm]
    if df.empty:
        return None

    # Filter for options with valid price and delta
    df = df[df["option_price"] > 0]
    if df.empty:
        return None

    # Check if delta column exists
    if "delta" not in df.columns:
        return None

    # Filter for options with valid (non-null) delta
    df = df[df["delta"].notna()]
    if df.empty:
        return None

    # Convert delta to numeric (handle any string representations)
    df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
    df = df[df["delta"].notna()]
    if df.empty:
        return None

    # Calculate days to expiry
    df["days_to_expiry"] = (df["expiry"] - trade_date_norm).dt.days

    # Calculate delta/price ratio
    # For PUT options, delta is negative, so we use absolute value
    df["delta_abs"] = df["delta"].abs()
    df["delta_price_ratio"] = df["delta_abs"] / df["option_price"]

    # Sort by: 1) Highest delta/price ratio, 2) Longest time to expiry (as tiebreaker)
    df = df.sort_values(
        ["delta_price_ratio", "days_to_expiry"],
        ascending=[False, False]  # Highest ratio first, longest expiry first
    )

    row = df.iloc[0]

    return {
        "option_trade_date": trade_date_norm,
        "option_instrument_token": int(row["instrument_token"]),
        "option_tradingsymbol": row["tradingsymbol"],
        "option_strike": float(row["strike"]),
        "option_expiry": row["expiry"],
        "option_type": prediction,
        "selection_option_price_1515": float(row["option_price"]),
    }


# Registry of available selection strategies
SELECTION_STRATEGIES: Dict[str, SelectionFunction] = {
    "nearestExpiryATM": select_nearest_expiry_atm,
    # "nearestExpiryHighOI": select_nearest_expiry_high_oi,
    # "highestDeltaPriceRatio": select_highest_delta_price_ratio,
}

