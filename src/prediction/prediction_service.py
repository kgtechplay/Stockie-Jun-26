from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.prediction.aggregator.index_aggregator import run_index_prediction
from src.prediction.aggregator.option_aggregator import apply_option_selection
from src.prediction.contracts import PredictionOutput
from src.prediction.providers.options_data_provider import fetch_index_options_eod
from src.prediction.providers.underlying_data_provider import (
    fetch_index_daily,
    get_db_connection,
)
from src.prediction.technical.option_selection_strategies import SELECTION_STRATEGIES
from src.prediction.technical.strategies import (
    DEFAULT_LOOKBACK_DAYS,
    PREDICTION_STRATEGIES,
)


@dataclass
class PredictionService:
    output_dir: Path
    default_lookback: int = DEFAULT_LOOKBACK_DAYS

    @classmethod
    def from_project_root(cls, project_root: Path) -> "PredictionService":
        return cls(output_dir=project_root / "output")

    def get_underlying_window(
        self,
        db_conn,
        instrument: str,
        as_of: datetime,
        lookback: int,
    ) -> pd.DataFrame:
        df = fetch_index_daily(
            db_conn,
            underlying=instrument.upper(),
            end_date=as_of.date().isoformat(),
            join_activity=True,
        )
        if df.empty:
            return df
        return df.tail(lookback).reset_index(drop=True)

    def run_prediction(
        self,
        instrument: str,
        strategies: list[str] | None,
        as_of: datetime,
    ) -> PredictionOutput:
        conn = get_db_connection()
        try:
            window = self.get_underlying_window(
                db_conn=conn,
                instrument=instrument,
                as_of=as_of,
                lookback=self.default_lookback,
            )
        finally:
            conn.close()
        if window.empty:
            raise ValueError(f"No underlying data available for {instrument} up to {as_of.date()}")

        return run_index_prediction(
            instrument=instrument.upper(),
            window=window,
            as_of=as_of,
            strategies=strategies,
        )

    def generate_predictions_for_strategy(
        self,
        instrument: str,
        strategy: str,
        use_agentic: bool,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        instrument = instrument.upper()
        if strategy not in PREDICTION_STRATEGIES:
            available = ", ".join(PREDICTION_STRATEGIES.keys())
            raise ValueError(f"Unknown prediction strategy '{strategy}'. Available strategies: {available}")

        conn = get_db_connection()
        try:
            df_daily = fetch_index_daily(
                conn=conn,
                underlying=instrument,
                start_date=start_date,
                end_date=end_date,
                join_activity=True,
            )
        finally:
            conn.close()

        if df_daily.empty:
            raise ValueError(f"[{instrument}] fetched 0 rows from DB for daily data.")

        df_daily = df_daily.copy()
        df_daily["trade_date"] = pd.to_datetime(df_daily["trade_date"])
        df_daily = df_daily.sort_values("trade_date").reset_index(drop=True)

        lookback_days = self.default_lookback
        if len(df_daily) < lookback_days:
            raise ValueError(f"Not enough rows to generate predictions for {instrument}.")

        records: list[dict[str, object]] = []
        ta_fn = PREDICTION_STRATEGIES[strategy]

        for i in range(lookback_days - 1, len(df_daily)):
            window_df = df_daily.loc[i - lookback_days + 1 : i].copy()
            decision_date = pd.to_datetime(df_daily.loc[i, "trade_date"])

            if use_agentic:
                output = run_index_prediction(
                    instrument=instrument,
                    window=window_df,
                    as_of=decision_date.to_pydatetime().replace(
                        hour=15, minute=15, second=0, microsecond=0
                    ),
                    strategies=[strategy],
                )
                records.append(
                    {
                        "date": decision_date,
                        "prediction": output.final_decision,
                        "regime": output.regime,
                        "confidence": output.confidence,
                        "reasons": " | ".join(output.reasons[:5]),
                    }
                )
            else:
                pred = ta_fn(window_df)
                records.append({"date": decision_date, "prediction": pred})

        return pd.DataFrame(records).sort_values("date").reset_index(drop=True)

    def save_predictions(
        self,
        instrument: str,
        strategy: str,
        predictions_df: pd.DataFrame,
    ) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{instrument.upper()}_{strategy}_predicted.csv"
        output_path = self.output_dir / filename
        out_df = predictions_df.copy()
        out_df["date"] = pd.to_datetime(out_df["date"])
        out_df.to_csv(output_path, index=False)
        return filename

    def generate_option_selection_for_strategy(
        self,
        underlying: str,
        predictor_strategy: str,
        selector_strategy: str,
        regenerate_all: bool = True,  # kept for CLI/API compatibility
        delete_intermediate: bool = True,
    ) -> str:
        _ = regenerate_all  # Current behavior recomputes all CALL/PUT rows each run.
        underlying = underlying.upper()

        if selector_strategy not in SELECTION_STRATEGIES:
            available = ", ".join(SELECTION_STRATEGIES.keys())
            raise ValueError(
                f"Unknown selector strategy '{selector_strategy}'. Available strategies: {available}"
            )

        base_filename = f"{underlying}_{predictor_strategy}_predicted.csv"
        base_path = self.output_dir / base_filename
        if not base_path.exists():
            raise FileNotFoundError(
                f"{base_path} not found. Run index predictions first for strategy '{predictor_strategy}'."
            )

        preds = pd.read_csv(base_path, parse_dates=["date"])
        preds["date"] = pd.to_datetime(preds["date"]).dt.normalize()

        strategy_filename = f"{underlying}_{predictor_strategy}_{selector_strategy}.csv"
        strategy_path = self.output_dir / strategy_filename

        needed = preds[preds["prediction"].isin(["CALL", "PUT"])]
        if needed.empty:
            preds.sort_values("date").reset_index(drop=True).to_csv(strategy_path, index=False)
            if delete_intermediate and base_path.exists():
                base_path.unlink()
            return strategy_filename

        conn = get_db_connection()
        try:
            options_df = fetch_index_options_eod(
                conn,
                start_date=pd.Timestamp("2025-01-01").date(),
                end_date=pd.Timestamp("2025-12-31").date(),
                underlying_like=f"{underlying}%",
            )
        finally:
            conn.close()

        selection_func = SELECTION_STRATEGIES[selector_strategy]
        selected_df = apply_option_selection(
            preds=preds,
            options_df=options_df,
            selection_func=selection_func,
        )
        selected_df.to_csv(strategy_path, index=False)

        if delete_intermediate and base_path.exists():
            base_path.unlink()

        return strategy_filename
