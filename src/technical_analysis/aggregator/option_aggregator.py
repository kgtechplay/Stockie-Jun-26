from __future__ import annotations

import pandas as pd

from src.technical_analysis.selection.option_registry import load_option_selection_strategies

OPTION_COLUMNS = [
    "option_trade_date",
    "option_instrument_token",
    "option_tradingsymbol",
    "option_strike",
    "option_expiry",
    "option_type",
    "selection_option_price_1515",
]


def apply_option_selection(
    preds: pd.DataFrame,
    options_df: pd.DataFrame,
    selection_func,
) -> pd.DataFrame:
    out = preds.copy()
    out = _ensure_option_columns(out)
    out = _clear_option_columns(out)

    if options_df.empty:
        return out.sort_values("date").reset_index(drop=True)

    options_df = options_df.copy()
    options_df["trade_date"] = pd.to_datetime(options_df["trade_date"]).dt.normalize()
    options_by_date = {d: g for d, g in options_df.groupby("trade_date")}

    for idx, row in out.iterrows():
        pred = row["prediction"]
        if pred not in ("CALL", "PUT"):
            continue

        trade_date = pd.to_datetime(row["date"]).normalize()
        chain_df = options_by_date.get(trade_date)
        if chain_df is None or chain_df.empty:
            continue

        best = selection_func(chain_df, pred, trade_date)
        if not best:
            continue

        for col, val in best.items():
            out.at[idx, col] = val

    return out.sort_values("date").reset_index(drop=True)


def apply_all_option_selection_strategies(
    preds: pd.DataFrame,
    options_df: pd.DataFrame,
    strategies: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    registry = load_option_selection_strategies()
    selected = strategies or sorted(registry.keys())
    outputs: dict[str, pd.DataFrame] = {}
    for name in selected:
        fn = registry.get(name)
        if fn is None:
            continue
        outputs[name] = apply_option_selection(preds=preds, options_df=options_df, selection_func=fn)
    return outputs


def _ensure_option_columns(preds: pd.DataFrame) -> pd.DataFrame:
    for col in OPTION_COLUMNS:
        if col not in preds.columns:
            preds[col] = pd.NA
    return preds


def _clear_option_columns(preds: pd.DataFrame) -> pd.DataFrame:
    for col in OPTION_COLUMNS:
        if col in preds.columns:
            preds[col] = pd.NA
    return preds
