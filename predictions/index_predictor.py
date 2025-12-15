# nifty_predictor.py (now supports NIFTY and BANKNIFTY)
import os
import sys
import argparse
from typing import Callable, Dict
import pandas as pd

from underlying_data import get_db_connection, fetch_index_daily
from option_selector import main as run_option_selection, SELECTION_STRATEGIES

LOOKBACK_DAYS = 10
TREND_THRESH = 0.003          # 0.3% move over last 10 days to call trend
PRED_DIR = "predictions/output"
PRED_FILE_TEMPLATE = "{underlying}_{strategy}_predicted.csv"   # e.g. NIFTY_trendFollowing_predicted.csv

# Type definition for prediction functions
# Input: window_closes (pd.Series), optional parameters
# Output: str ("CALL", "PUT", or "NO_POSITION")
PredictionFunction = Callable[[pd.Series], str]


def predict_trend_following(window_closes: pd.Series,
                            trend_thresh: float = TREND_THRESH) -> str:
    """
    Strategy 1: Trend Following
    Use last LOOKBACK_DAYS closes to decide:
      - "CALL" (expect up), "PUT" (expect down), or "NO_POSITION".
    Original strategy - looks for trend direction and mean comparison.
    """
    first_close = float(window_closes.iloc[0])
    last_close = float(window_closes.iloc[-1])
    mean_close = float(window_closes.mean())

    trend_pct = (last_close - first_close) / first_close if first_close != 0 else 0.0

    if trend_pct > trend_thresh and last_close > mean_close:
        return "CALL"
    elif trend_pct < -trend_thresh and last_close < mean_close:
        return "PUT"
    else:
        return "NO_POSITION"


def predict_momentum(window_closes: pd.Series,
                     momentum_thresh: float = 0.005) -> str:
    """
    Strategy 2: Momentum
    Uses recent momentum (last 3 days vs previous 3 days) to predict direction.
    More sensitive to short-term momentum changes.
    """
    if len(window_closes) < 6:
        return "NO_POSITION"
    
    recent_avg = float(window_closes.iloc[-3:].mean())
    previous_avg = float(window_closes.iloc[-6:-3].mean()) if len(window_closes) >= 6 else float(window_closes.iloc[0])
    
    momentum_pct = (recent_avg - previous_avg) / previous_avg if previous_avg != 0 else 0.0
    
    if momentum_pct > momentum_thresh:
        return "CALL"
    elif momentum_pct < -momentum_thresh:
        return "PUT"
    else:
        return "NO_POSITION"


def predict_mean_reversion(window_closes: pd.Series,
                           deviation_thresh: float = 0.01) -> str:
    """
    Strategy 3: Mean Reversion
    Predicts opposite direction when price deviates significantly from mean.
    Expects price to revert to mean.
    """
    current_close = float(window_closes.iloc[-1])
    mean_close = float(window_closes.mean())
    std_close = float(window_closes.std())
    
    if std_close == 0:
        return "NO_POSITION"
    
    z_score = (current_close - mean_close) / std_close
    
    if z_score > 1.0:  # Price is significantly above mean, expect reversion down
        return "PUT"
    elif z_score < -1.0:  # Price is significantly below mean, expect reversion up
        return "CALL"
    else:
        return "NO_POSITION"


# Registry of available prediction strategies
PREDICTION_STRATEGIES: Dict[str, PredictionFunction] = {
    "trendFollowing": predict_trend_following,
    "momentum": predict_momentum,
    "meanReversion": predict_mean_reversion,
}


def generate_index_predictions(df_daily: pd.DataFrame,
                               prediction_func: PredictionFunction,
                               lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    From daily index data with columns:
      trade_date, open_915, close_1515

    Generate one prediction per date where we have at least lookback_days history.
    Each row's 'date' = decision date D (15:15 close known),
    and prediction is for direction of D+1 open.

    Args:
        df_daily: DataFrame with daily index data
        prediction_func: Function to generate predictions from window_closes
        lookback_days: Number of days to look back for prediction

    Returns DataFrame: [date, prediction]
    """
    df = df_daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    n = len(df)
    if n < lookback_days:
        raise ValueError("Not enough rows to generate predictions.")

    records = []
    for i in range(lookback_days - 1, n):
        window_start = i - lookback_days + 1
        window_end = i
        window_closes = df.loc[window_start:window_end, "close_1515"]

        pred = prediction_func(window_closes)
        date = df.loc[i, "trade_date"]

        records.append({
            "date": date,
            "prediction": pred,
        })

    preds = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return preds


def append_predictions_to_csv(new_preds: pd.DataFrame,
                              underlying: str,
                              strategy: str,
                              folder: str = PRED_DIR,
                              regenerate_all: bool = True) -> pd.DataFrame:
    """
    Create or overwrite predictions/output/{UNDERLYING}_{strategy}_predicted.csv.

    Always creates/overwrites the file with new predictions (regenerate_all=True by default).
    The file is completely replaced with the new predictions. Backtest columns will be
    recomputed by the backtest script.
    """
    underlying = underlying.upper()
    filename = PRED_FILE_TEMPLATE.format(underlying=underlying, strategy=strategy)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)

    new_preds = new_preds.copy()
    new_preds["date"] = pd.to_datetime(new_preds["date"])
    new_preds = new_preds.sort_values("date").reset_index(drop=True)
    
    # Always overwrite the file completely with new predictions
    new_preds.to_csv(path, index=False)
    return new_preds


def main(underlying: str, predictor_strategy: str, regenerate_all: bool = True, run_option_selection_auto: bool = False):
    """
    Main function to generate index predictions using a specified strategy.
    
    Args:
        underlying: NIFTY or BANKNIFTY
        predictor_strategy: Name of the prediction strategy (must be in PREDICTION_STRATEGIES)
        regenerate_all: If True, regenerate all predictions
        run_option_selection_auto: If True, automatically run option selection for all selector strategies
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
        df_daily = fetch_index_daily(conn, underlying=underlying)
    finally:
        conn.close()

    print(f"[{underlying}] [{predictor_strategy}] fetched {len(df_daily)} days of data")
    print(f"Date range: {df_daily['trade_date'].min()} to {df_daily['trade_date'].max()}")

    new_preds = generate_index_predictions(df_daily, prediction_func)
    print(f"[{underlying}] [{predictor_strategy}] generated {len(new_preds)} predictions")

    # Always create/overwrite the file with new predictions
    combined = append_predictions_to_csv(new_preds, underlying=underlying, strategy=predictor_strategy, regenerate_all=regenerate_all)

    print(f"[{underlying}] [{predictor_strategy}] created/overwritten file with {len(combined)} predictions")
    print(f"\n[{underlying}] [{predictor_strategy}] predictions saved to {path}")
    print("\nFirst 5 predictions:")
    print(combined.head())
    print("\nLast 10 predictions:")
    print(combined.tail(10))
    
    # Optionally run option selection for all strategies
    if run_option_selection_auto:
        print(f"\n{'='*60}")
        print(f"Running option selection for all selector strategies...")
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
        print(f"Option selection completed for all selector strategies")
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
        default="trendFollowing",
        choices=list(PREDICTION_STRATEGIES.keys()),
        help="Prediction strategy to use"
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
    
    args = parser.parse_args()
    
    if args.list_strategies:
        print("Available prediction strategies:")
        for name, func in PREDICTION_STRATEGIES.items():
            doc = func.__doc__.strip().split('\n')[0] if func.__doc__ else "No description"
            print(f"  {name:25s} - {doc}")
        sys.exit(0)
    
    main(
        underlying=args.underlying,
        predictor_strategy=args.strategy,
        regenerate_all=args.regenerate_all,
        run_option_selection_auto=args.auto_option_selection
    )
