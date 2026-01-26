# index_predictor.py (supports NIFTY and BANKNIFTY) - UPDATED
#
# Key updates:
# - generate_index_predictions now passes a *window DataFrame* (not just closes)
#   so future strategies can use OHLC, volume/OI proxies, etc.
# - prediction_logic.py is backward-compatible and still works with closes-only logic.
# - fetch_index_daily is expected to return at least:
#     trade_date, close_price
#   and can include open/high/low + fut_* proxy columns (enriched)
#
# Output file format remains the same:
#   predictions/output/{UNDERLYING}_{strategy}_predicted.csv  with columns: date, prediction

import os
import sys
import argparse
import pandas as pd

from underlying_data import get_db_connection, fetch_index_daily
from option_selector import main as run_option_selection
from selection_logic import SELECTION_STRATEGIES
from prediction_logic import (
    PREDICTION_STRATEGIES,
    PredictionFunction,
    DEFAULT_LOOKBACK_DAYS as LOOKBACK_DAYS
)

PRED_DIR = "predictions/output"
PRED_FILE_TEMPLATE = "{underlying}_{strategy}_predicted.csv"   # e.g. NIFTY_trendFollowing.csv


def generate_index_predictions(
    df_daily: pd.DataFrame,
    prediction_func: PredictionFunction,
    lookback_days: int = LOOKBACK_DAYS
) -> pd.DataFrame:
    """
    From daily index data with columns:
      trade_date, close_price, ... (optional additional features like OHLC, fut_* proxies)

    Generate one prediction per date where we have at least lookback_days history.
    Each row's 'date' = decision date D (close_price for D known),
    and prediction is intended for direction of D+1 open (same intention as before).

    IMPORTANT:
    - We now pass the full window DataFrame to prediction_func.
    - Existing strategies still use close_price only.
    """
    df = df_daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    if "close_price" not in df.columns:
        raise ValueError("df_daily must contain 'close_price' column.")

    n = len(df)
    if n < lookback_days:
        raise ValueError("Not enough rows to generate predictions.")

    records = []
    for i in range(lookback_days - 1, n):
        window_start = i - lookback_days + 1
        window_end = i

        # NEW: pass full window dataframe (includes closes and any other feature columns)
        window_df = df.loc[window_start:window_end].copy()

        pred = prediction_func(window_df)
        date = df.loc[i, "trade_date"]

        records.append({
            "date": date,
            "prediction": pred,
        })

    preds = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return preds


def append_predictions_to_csv(
    new_preds: pd.DataFrame,
    underlying: str,
    strategy: str,
    folder: str = PRED_DIR,
    regenerate_all: bool = True
) -> pd.DataFrame:
    """
    Create or overwrite predictions/output/{UNDERLYING}_{strategy}_predicted.csv.

    Always overwrites file with new predictions by default.
    """
    underlying = underlying.upper()
    filename = PRED_FILE_TEMPLATE.format(underlying=underlying, strategy=strategy)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)

    new_preds = new_preds.copy()
    new_preds["date"] = pd.to_datetime(new_preds["date"])
    new_preds = new_preds.sort_values("date").reset_index(drop=True)

    new_preds.to_csv(path, index=False)
    return new_preds


def main(
    underlying: str,
    predictor_strategy: str,
    regenerate_all: bool = True,
    run_option_selection_auto: bool = False,
    start_date: str | None = None,
    end_date: str | None = None
):
    """
    Main function to generate index predictions using a specified strategy.

    Args:
        underlying: NIFTY or BANKNIFTY
        predictor_strategy: Name of the prediction strategy (must be in PREDICTION_STRATEGIES)
        regenerate_all: If True, regenerate all predictions
        run_option_selection_auto: If True, automatically run option selection for all selector strategies
        start_date/end_date: optional filters to limit the daily dataset fetched from DB
    """
    underlying = underlying.upper()

    # Validate strategy
    if predictor_strategy not in PREDICTION_STRATEGIES:
        available = ", ".join(PREDICTION_STRATEGIES.keys())
        raise ValueError(
            f"Unknown prediction strategy '{predictor_strategy}'. Available strategies: {available}"
        )

    prediction_func = PREDICTION_STRATEGIES[predictor_strategy]
    filename = PRED_FILE_TEMPLATE.format(underlying=underlying, strategy=predictor_strategy)
    path = os.path.join(PRED_DIR, filename)

    conn = get_db_connection()
    try:
        # Should return daily OHLC plus activity proxies (if your underlying_data fetch joins them)
        df_daily = fetch_index_daily(
            conn,
            underlying=underlying,
            start_date=start_date,
            end_date=end_date,
            join_activity=True
        )
    finally:
        conn.close()

    if df_daily.empty:
        raise ValueError(f"[{underlying}] fetched 0 rows from DB for daily data.")

    print(f"[{underlying}] [{predictor_strategy}] fetched {len(df_daily)} days of data")
    print(f"Date range: {df_daily['trade_date'].min()} to {df_daily['trade_date'].max()}")

    new_preds = generate_index_predictions(df_daily, prediction_func)
    print(f"[{underlying}] [{predictor_strategy}] generated {len(new_preds)} predictions")

    combined = append_predictions_to_csv(
        new_preds,
        underlying=underlying,
        strategy=predictor_strategy,
        regenerate_all=regenerate_all
    )

    print(f"[{underlying}] [{predictor_strategy}] created/overwritten file with {len(combined)} predictions")
    print(f"\n[{underlying}] [{predictor_strategy}] predictions saved to {path}")
    print("\nFirst 5 predictions:")
    print(combined.head())
    print("\nLast 10 predictions:")
    print(combined.tail(10))

    # Optionally run option selection for all strategies
    if run_option_selection_auto:
        print(f"\n{'='*60}")
        print("Running option selection for all selector strategies...")
        print(f"{'='*60}\n")

        for selector_strategy_name in SELECTION_STRATEGIES.keys():
            try:
                print(f"\n--- Running selector strategy: {selector_strategy_name} ---")
                run_option_selection(
                    underlying=underlying,
                    predictor_strategy=predictor_strategy,
                    selector_strategy=selector_strategy_name,
                    regenerate_all=regenerate_all,
                    options_view=None
                )
            except Exception as e:
                print(f"Error running selector strategy '{selector_strategy_name}': {e}")
                import traceback
                traceback.print_exc()

        print(f"\n{'='*60}")
        print("Option selection completed for all selector strategies")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate index predictions (NIFTY/BANKNIFTY) using different strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available prediction strategies:
  trendFollowing  - Original trend following strategy (default)
  momentum        - Short-term momentum based strategy
  meanReversion   - Mean reversion strategy

Output files are saved as: {{underlying}}_{{strategy}}_predicted.csv
Example: NIFTY_trendFollowing_predicted.csv
        """
    )
    parser.add_argument(
        "-u", "--underlying",
        default="NIFTY",
        choices=["NIFTY", "BANKNIFTY"],
        help="Underlying index"
    )
    parser.add_argument(
        "-s", "--strategy",
        default=None,
        help="Prediction strategy to use (if not provided, runs for all strategies). Available: "
             + ", ".join(PREDICTION_STRATEGIES.keys())
    )
    parser.add_argument(
        "--no-regenerate",
        action="store_false",
        dest="regenerate_all",
        default=True,
        help="Append only new predictions (default: regenerate all)"
    )
    parser.add_argument(
        "--auto-option-selection",
        action="store_true",
        help="Automatically run option selection for all selector strategies"
    )
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List all available prediction strategies and exit"
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Filter daily data from this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Filter daily data up to this date (YYYY-MM-DD)"
    )

    args = parser.parse_args()

    if args.list_strategies:
        print("Available prediction strategies:")
        for name, func in PREDICTION_STRATEGIES.items():
            doc = func.__doc__.strip().split('\n')[0] if func.__doc__ else "No description"
            print(f"  {name:25s} - {doc}")
        sys.exit(0)

    # If no strategy specified, run for all strategies
    if args.strategy is None:
        print(f"[{args.underlying}] No strategy specified. Running for all prediction strategies...")
        print(f"{'='*60}\n")

        for strategy_name in PREDICTION_STRATEGIES.keys():
            try:
                print(f"\n{'='*60}")
                print(f"Running prediction strategy: {strategy_name}")
                print(f"{'='*60}\n")
                main(
                    underlying=args.underlying,
                    predictor_strategy=strategy_name,
                    regenerate_all=args.regenerate_all,
                    run_option_selection_auto=args.auto_option_selection,
                    start_date=args.start_date,
                    end_date=args.end_date
                )
            except Exception as e:
                print(f"Error running prediction strategy '{strategy_name}': {e}")
                import traceback
                traceback.print_exc()

        print(f"\n{'='*60}")
        print(f"Completed running all prediction strategies for {args.underlying}")
        print(f"{'='*60}\n")
    else:
        # Validate strategy if provided
        if args.strategy not in PREDICTION_STRATEGIES:
            available = ", ".join(PREDICTION_STRATEGIES.keys())
            print(f"Error: Unknown prediction strategy '{args.strategy}'.")
            print(f"Available strategies: {available}")
            sys.exit(1)

        main(
            underlying=args.underlying,
            predictor_strategy=args.strategy,
            regenerate_all=args.regenerate_all,
            run_option_selection_auto=args.auto_option_selection,
            start_date=args.start_date,
            end_date=args.end_date
        )
