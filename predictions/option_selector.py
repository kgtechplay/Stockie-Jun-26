# option_selector.py
import os
import sys
import argparse
from typing import Callable, Dict, Optional
import pandas as pd

from underlying_data import get_db_connection
from options_data import fetch_index_options_eod

PRED_DIR = "predictions/output"
PRED_FILE_TEMPLATE = "{underlying}_{predictor_strategy}_predicted.csv"
STRATEGY_FILE_TEMPLATE = "{underlying}_{predictor_strategy}_{selector_strategy}.csv"

# Adjust these if your actual view names differ
DEFAULT_OPTIONS_VIEWS = {
    "NIFTY": "dbo.vw_NiftySnapshotWithUnderlying",
    "BANKNIFTY": "dbo.vw_BankNiftySnapshotWithUnderlying",
}

# Type definition for selection functions
# Input: chain_df (pd.DataFrame), prediction (str), trade_date (pd.Timestamp)
# Output: dict with option details or None
SelectionFunction = Callable[[pd.DataFrame, str, pd.Timestamp], Optional[Dict]]


def _ensure_option_columns(preds: pd.DataFrame) -> pd.DataFrame:
    required_cols = [
        "option_trade_date",
        "option_instrument_token",
        "option_tradingsymbol",
        "option_strike",
        "option_expiry",
        "option_type",
        "selection_option_price_1515",
    ]
    for col in required_cols:
        if col not in preds.columns:
            preds[col] = pd.NA
    return preds


def _clear_option_columns(preds: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "option_trade_date",
        "option_instrument_token",
        "option_tradingsymbol",
        "option_strike",
        "option_expiry",
        "option_type",
        "selection_option_price_1515",
    ]
    for col in cols:
        if col in preds.columns:
            preds[col] = pd.NA
    return preds


# ============================================================================
# SELECTION STRATEGIES
# ============================================================================
# Each strategy function follows the same signature:
#   Input: chain_df (pd.DataFrame), prediction (str), trade_date (pd.Timestamp)
#   Output: dict with option details or None

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
    "nearestExpiryHighOI": select_nearest_expiry_high_oi,
    "highestDeltaPriceRatio": select_highest_delta_price_ratio,
}


def main(underlying: str, 
         selector_strategy: str,
         predictor_strategy: str = "trendFollowing",
         regenerate_all: bool = True, 
         options_view: str | None = None):
    """
    Main function to select options using a specified strategy.
    
    Args:
        underlying: NIFTY or BANKNIFTY
        selector_strategy: Name of the selection strategy (must be in SELECTION_STRATEGIES)
        predictor_strategy: Name of the prediction strategy used (default: "trendFollowing")
        regenerate_all: If True, recompute all option selections
        options_view: Override the default options view name
    """
    underlying = underlying.upper()
    
    # Validate selector strategy
    if selector_strategy not in SELECTION_STRATEGIES:
        available = ", ".join(SELECTION_STRATEGIES.keys())
        raise ValueError(
            f"Unknown selector strategy '{selector_strategy}'. Available strategies: {available}"
        )
    
    selection_func = SELECTION_STRATEGIES[selector_strategy]
    
    # Read base predictions file (with predictor strategy)
    base_filename = PRED_FILE_TEMPLATE.format(
        underlying=underlying,
        predictor_strategy=predictor_strategy
    )
    base_path = os.path.join(PRED_DIR, base_filename)

    if not os.path.isfile(base_path):
        raise FileNotFoundError(
            f"{base_path} not found. Run index_predictor.py -u {underlying} -s {predictor_strategy} first."
        )

    # Output file with both predictor and selector strategy
    strategy_filename = STRATEGY_FILE_TEMPLATE.format(
        underlying=underlying,
        predictor_strategy=predictor_strategy,
        selector_strategy=selector_strategy
    )
    strategy_path = os.path.join(PRED_DIR, strategy_filename)

    if options_view is None:
        options_view = DEFAULT_OPTIONS_VIEWS.get(
            underlying, "dbo.vw_BankNIftysnapshotWithUnderlying"
        )

    # Read base predictions
    preds = pd.read_csv(base_path, parse_dates=["date"])
    preds["date"] = pd.to_datetime(preds["date"]).dt.normalize()
    preds = _ensure_option_columns(preds)

    # If strategy file exists, load it; otherwise start fresh
    if os.path.isfile(strategy_path) and not regenerate_all:
        strategy_preds = pd.read_csv(strategy_path, parse_dates=["date"])
        strategy_preds["date"] = pd.to_datetime(strategy_preds["date"]).dt.normalize()
        # Merge option columns from strategy file
        option_cols = [
            "option_trade_date",
            "option_instrument_token",
            "option_tradingsymbol",
            "option_strike",
            "option_expiry",
            "option_type",
            "selection_option_price_1515",
        ]
        for col in option_cols:
            if col in strategy_preds.columns:
                preds[col] = strategy_preds.set_index("date")[col].reindex(
                    preds.set_index("date").index
                ).values
    else:
        preds = _clear_option_columns(preds)

    # Always process all CALL/PUT predictions, regardless of regenerate_all flag
    needed_dates = set(
        preds.loc[preds["prediction"].isin(["CALL", "PUT"]), "date"]
    )

    if not needed_dates:
        print(f"[{underlying}] [{predictor_strategy}] [{selector_strategy}] no predictions need option selection.")
        # Still save the file even if no updates needed
        preds = preds.sort_values("date").reset_index(drop=True)
        os.makedirs(PRED_DIR, exist_ok=True)
        preds.to_csv(strategy_path, index=False)
        # Delete the intermediate prediction file after successful creation
        if os.path.isfile(strategy_path) and os.path.isfile(base_path):
            try:
                os.remove(base_path)
                print(f"[{underlying}] Deleted intermediate file: {base_filename}")
            except OSError as e:
                print(f"[{underlying}] Warning: Could not delete {base_filename}: {e}")
        return

    start_date = min(needed_dates).date()
    end_date = max(needed_dates).date()

    conn = get_db_connection()
    try:
        options_df = fetch_index_options_eod(
            conn,
            start_date=start_date,
            end_date=end_date,
            view_name=options_view,
            underlying_like=f"{underlying}%",
        )
    finally:
        conn.close()

    if options_df.empty:
        print(f"[{underlying}] [{predictor_strategy}] [{selector_strategy}] no option data found for requested dates.")
        # Still save the file even if no data found
        preds = preds.sort_values("date").reset_index(drop=True)
        os.makedirs(PRED_DIR, exist_ok=True)
        preds.to_csv(strategy_path, index=False)
        # Delete the intermediate prediction file after successful creation
        if os.path.isfile(strategy_path) and os.path.isfile(base_path):
            try:
                os.remove(base_path)
                print(f"[{underlying}] Deleted intermediate file: {base_filename}")
            except OSError as e:
                print(f"[{underlying}] Warning: Could not delete {base_filename}: {e}")
        return

    options_df["trade_date"] = pd.to_datetime(options_df["trade_date"]).dt.normalize()
    options_by_date = {d: g for d, g in options_df.groupby("trade_date")}

    for idx, row in preds.iterrows():
        pred = row["prediction"]
        if pred not in ("CALL", "PUT"):
            # Clear option columns for non-CALL/PUT predictions
            preds.at[idx, "option_instrument_token"] = pd.NA
            preds.at[idx, "option_trade_date"] = pd.NA
            preds.at[idx, "option_tradingsymbol"] = pd.NA
            preds.at[idx, "option_strike"] = pd.NA
            preds.at[idx, "option_expiry"] = pd.NA
            preds.at[idx, "option_type"] = pd.NA
            preds.at[idx, "selection_option_price_1515"] = pd.NA
            continue

        # Always recompute option selection for all CALL/PUT predictions
        trade_date = row["date"]
        chain_df = options_by_date.get(trade_date)
        if chain_df is None or chain_df.empty:
            # Clear option columns if no data available
            preds.at[idx, "option_instrument_token"] = pd.NA
            preds.at[idx, "option_trade_date"] = pd.NA
            preds.at[idx, "option_tradingsymbol"] = pd.NA
            preds.at[idx, "option_strike"] = pd.NA
            preds.at[idx, "option_expiry"] = pd.NA
            preds.at[idx, "option_type"] = pd.NA
            preds.at[idx, "selection_option_price_1515"] = pd.NA
            continue

        # Use the selected strategy function
        best = selection_func(chain_df, pred, trade_date)
        if not best:
            # Clear option columns if selection function returns None
            preds.at[idx, "option_instrument_token"] = pd.NA
            preds.at[idx, "option_trade_date"] = pd.NA
            preds.at[idx, "option_tradingsymbol"] = pd.NA
            preds.at[idx, "option_strike"] = pd.NA
            preds.at[idx, "option_expiry"] = pd.NA
            preds.at[idx, "option_type"] = pd.NA
            preds.at[idx, "selection_option_price_1515"] = pd.NA
            continue

        for col, val in best.items():
            preds.at[idx, col] = val

    preds = preds.sort_values("date").reset_index(drop=True)
    os.makedirs(PRED_DIR, exist_ok=True)
    preds.to_csv(strategy_path, index=False)
    print(f"[{underlying}] [{predictor_strategy}] [{selector_strategy}] option selection updated in {strategy_path}")
    print(preds.tail())
    
    # Delete the intermediate prediction file after successful creation of combined strategy file
    # Only delete if the strategy file was successfully created and the base file exists
    if os.path.isfile(strategy_path) and os.path.isfile(base_path):
        try:
            os.remove(base_path)
            print(f"[{underlying}] Deleted intermediate file: {base_filename}")
        except OSError as e:
            print(f"[{underlying}] Warning: Could not delete {base_filename}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Select best option instrument for NIFTY/BANKNIFTY predictions using different strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available selector strategies:
  nearestExpiryATM              - Nearest expiry, ATM strike, highest volume/OI (default)
  nearestExpiryHighOI          - Nearest expiry, highest open interest
  highestDeltaPriceRatio       - Highest delta/price ratio with long time to expiry

Output files are saved as: {{underlying}}_{{predictor_strategy}}_{{selector_strategy}}.csv
Example: NIFTY_trendFollowing_nearestExpiryATM.csv
        """
    )
    parser.add_argument(
        "-u", "--underlying",
        default="NIFTY",
        choices=["NIFTY", "BANKNIFTY"],
        help="Underlying index"
    )
    parser.add_argument(
        "-ps", "--predictor-strategy",
        default="trendFollowing",
        help="Prediction strategy used (must match the predictor strategy used in index_predictor.py)"
    )
    parser.add_argument(
        "-ss", "--selector-strategy",
        default="nearestExpiryATM",
        choices=list(SELECTION_STRATEGIES.keys()),
        help="Selection strategy to use"
    )
    parser.add_argument(
        "--no-regenerate",
        action="store_false",
        dest="regenerate_all",
        default=True,
        help="Only compute options for missing entries (default: recompute all)"
    )
    parser.add_argument(
        "--options-view",
        default=None,
        help="Override options snapshot view name (defaults depend on underlying)"
    )
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List all available selector strategies and exit"
    )
    
    args = parser.parse_args()
    
    if args.list_strategies:
        print("Available selection strategies:")
        for name, func in SELECTION_STRATEGIES.items():
            doc = func.__doc__.strip().split('\n')[0] if func.__doc__ else "No description"
            print(f"  {name:25s} - {doc}")
        sys.exit(0)
    
    main(
        underlying=args.underlying,
        predictor_strategy=args.predictor_strategy,
        selector_strategy=args.selector_strategy,
        regenerate_all=args.regenerate_all,
        options_view=args.options_view
    )
