# option_selector.py
import os
import sys
import argparse
import pandas as pd

from src.prediction.providers.options_data_provider import fetch_index_options_eod
from src.prediction.providers.underlying_data_provider import get_db_connection
from option_selection_logic import SELECTION_STRATEGIES

PRED_DIR = "predictions/output"
PRED_FILE_TEMPLATE = "{underlying}_{predictor_strategy}_predicted.csv"
STRATEGY_FILE_TEMPLATE = "{underlying}_{predictor_strategy}_{selector_strategy}.csv"

# Prediction strategy names (to avoid circular import from index_predictor)
PREDICTION_STRATEGY_NAMES = ["trendFollowing", "momentum", "meanReversion"]

# Adjust these if your actual view names differ
DEFAULT_OPTIONS_VIEWS = {
    "NIFTY": "dbo.vw_NiftySnapshotWithUnderlying",
    "BANKNIFTY": "dbo.vw_BankNiftySnapshotWithUnderlying",
}


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




def main(underlying: str, 
         selector_strategy: str,
         predictor_strategy: str = "trendFollowing",
         regenerate_all: bool = True, 
         options_view: str | None = None,
         delete_intermediate: bool = True):
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
        if delete_intermediate and os.path.isfile(strategy_path) and os.path.isfile(base_path):
            try:
                os.remove(base_path)
                print(f"[{underlying}] Deleted intermediate file: {base_filename}")
            except OSError as e:
                print(f"[{underlying}] Warning: Could not delete {base_filename}: {e}")
        return

    # Always fetch full-year 2025 to ensure option chains are available
    start_date = pd.Timestamp("2025-01-01").date()
    end_date = pd.Timestamp("2025-12-31").date()

    conn = get_db_connection()
    try:
        options_df = fetch_index_options_eod(
            conn,
            start_date=start_date,
            end_date=end_date,
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
        if delete_intermediate and os.path.isfile(strategy_path) and os.path.isfile(base_path):
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
    # Only delete if delete_intermediate is True, the strategy file was successfully created and the base file exists
    if delete_intermediate and os.path.isfile(strategy_path) and os.path.isfile(base_path):
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
        default=None,
        help="Prediction strategy used (if not provided, runs for all prediction strategies). Available: " + ", ".join(PREDICTION_STRATEGY_NAMES)
    )
    parser.add_argument(
        "-ss", "--selector-strategy",
        default=None,
        help="Selection strategy to use (if not provided, runs for all selection strategies). Available: " + ", ".join(SELECTION_STRATEGIES.keys())
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
    
    # Determine which prediction strategies to run
    if args.predictor_strategy is None:
        predictor_strategies = PREDICTION_STRATEGY_NAMES
    else:
        if args.predictor_strategy not in PREDICTION_STRATEGY_NAMES:
            available = ", ".join(PREDICTION_STRATEGY_NAMES)
            raise ValueError(
                f"Unknown prediction strategy '{args.predictor_strategy}'. Available strategies: {available}"
            )
        predictor_strategies = [args.predictor_strategy]
    
    # Determine which selection strategies to run
    if args.selector_strategy is None:
        selector_strategies = list(SELECTION_STRATEGIES.keys())
    else:
        if args.selector_strategy not in SELECTION_STRATEGIES:
            available = ", ".join(SELECTION_STRATEGIES.keys())
            raise ValueError(
                f"Unknown selector strategy '{args.selector_strategy}'. Available strategies: {available}"
            )
        selector_strategies = [args.selector_strategy]
    
    # Run for all combinations
    if len(predictor_strategies) > 1 or len(selector_strategies) > 1:
        print(f"[{args.underlying}] Running for all strategy combinations...")
        print(f"  Prediction strategies: {', '.join(predictor_strategies)}")
        print(f"  Selection strategies: {', '.join(selector_strategies)}")
        print(f"  Total combinations: {len(predictor_strategies) * len(selector_strategies)}")
        print(f"{'='*60}\n")
        
        for pred_strategy in predictor_strategies:
            for sel_strategy in selector_strategies:
                try:
                    print(f"\n{'='*60}")
                    print(f"Running: {pred_strategy} + {sel_strategy}")
                    print(f"{'='*60}\n")
                    # Don't delete intermediate files when running all combinations
                    # Only delete after the last selector strategy for each predictor strategy
                    is_last_selector = (sel_strategy == selector_strategies[-1])
                    main(
                        underlying=args.underlying,
                        predictor_strategy=pred_strategy,
                        selector_strategy=sel_strategy,
                        regenerate_all=args.regenerate_all,
                        options_view=args.options_view,
                        delete_intermediate=is_last_selector
                    )
                except Exception as e:
                    print(f"Error running combination '{pred_strategy}' + '{sel_strategy}': {e}")
                    import traceback
                    traceback.print_exc()
        
        print(f"\n{'='*60}")
        print(f"Completed running all strategy combinations for {args.underlying}")
        print(f"{'='*60}\n")
    else:
        # Single combination - delete intermediate file after creation
        main(
            underlying=args.underlying,
            predictor_strategy=predictor_strategies[0],
            selector_strategy=selector_strategies[0],
            regenerate_all=args.regenerate_all,
            options_view=args.options_view,
            delete_intermediate=True
        )
