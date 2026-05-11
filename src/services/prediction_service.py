from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from src.technical_analysis.aggregator.underlying_aggregator import (
    PredictionOutput,
    add_aggregate_decision_column,
    get_underlying_strategy_predictions,
    run_underlying_prediction,
)
from src.technical_analysis.aggregator.option_aggregator import apply_option_selection
from src.data_manager.option_history_reader import fetch_index_options_eod
from src.data_manager.underlying_history_reader import (
    fetch_index_daily,
    get_db_connection,
)
from src.technical_analysis.selection.option_registry import (
    load_option_selection_strategies,
)
from src.technical_analysis.prediction.underlying_registry import (
    DEFAULT_LOOKBACK_DAYS,
    load_underlying_prediction_strategies,
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

        return run_underlying_prediction(
            instrument=instrument.upper(),
            window=window,
            as_of=as_of,
            strategies=strategies,
        )

    def run_reference_date_predictions(
        self,
        instrument: str,
        reference_date: date,
        strategies: list[str] | None = None,
        save_to_disk: bool = True,
    ) -> dict[str, object]:
        """
        Run all selected strategies for one underlying on one reference date.

        Output shape:
          date,underlying,<strategy columns>,aggregate_decision
        """
        output_df = self.generate_reference_date_prediction(
            instrument=instrument,
            reference_date=reference_date,
            strategies=strategies,
        )
        output_file = (
            self.save_reference_prediction_file(instrument, reference_date, output_df)
            if save_to_disk
            else None
        )
        return {
            "reference_date": reference_date.isoformat(),
            "underlying": instrument.upper(),
            "output_file": output_file,
            "strategies": self.get_selected_strategy_names(strategies),
            "records": output_df.to_dict(orient="records"),
        }

    def generate_reference_date_prediction(
        self,
        instrument: str,
        reference_date: date,
        strategies: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Generate one prediction row for one underlying.

        The row is labeled with reference_date, but the strategy window uses the
        latest available DB row up to that date. This supports morning predictions
        when the most recent persisted market data is still N-1.
        """
        instrument = instrument.upper()
        selected = self.get_selected_strategy_names(strategies)
        warmup_start = reference_date - timedelta(days=max(self.default_lookback * 3, 45))

        conn = get_db_connection()
        try:
            df_daily = fetch_index_daily(
                conn=conn,
                underlying=instrument,
                start_date=warmup_start.isoformat(),
                end_date=reference_date.isoformat(),
                join_activity=True,
            )
        finally:
            conn.close()

        if df_daily.empty:
            raise ValueError(f"[{instrument}] fetched 0 rows from DB for daily data up to {reference_date}.")

        df_daily = df_daily.copy()
        df_daily["trade_date"] = pd.to_datetime(df_daily["trade_date"])
        df_daily = df_daily.sort_values("trade_date").reset_index(drop=True)
        if len(df_daily) < self.default_lookback:
            raise ValueError(f"Not enough rows to generate predictions for {instrument}.")

        window_df = df_daily.tail(self.default_lookback).copy()
        predictions = get_underlying_strategy_predictions(
            window=window_df,
            strategies=selected,
        )
        row: dict[str, object] = {
            "date": reference_date.isoformat(),
            "underlying": instrument,
        }
        for strategy in selected:
            row[strategy] = predictions.get(strategy, "NO_POSITION")

        return add_aggregate_decision_column(
            pd.DataFrame([row]),
            strategy_columns=selected,
        )

    def run_reference_date_predictions_for_symbols(
        self,
        instruments: list[str],
        reference_date: date,
        strategies: list[str] | None = None,
        save_to_disk: bool = True,
    ) -> dict[str, object]:
        strategy_registry = load_underlying_prediction_strategies()
        selected = strategies or sorted(strategy_registry.keys())
        unknown_strategies = [name for name in selected if name not in strategy_registry]
        if unknown_strategies:
            available = ", ".join(sorted(strategy_registry.keys()))
            unknown = ", ".join(unknown_strategies)
            raise ValueError(f"Unknown prediction strategy '{unknown}'. Available strategies: {available}")

        as_of = datetime.combine(reference_date, time(hour=15, minute=15))
        records: list[dict[str, object]] = []

        for instrument in instruments:
            row: dict[str, object] = {
                "reference_date": reference_date.isoformat(),
                "instrument": instrument.upper(),
                "status": "ok",
                "error": "",
            }
            try:
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
                    raise ValueError(f"No underlying data available for {instrument} up to {reference_date}")

                predictions = get_underlying_strategy_predictions(
                    window=window,
                    strategies=selected,
                )
                for strategy in selected:
                    row[strategy] = predictions.get(strategy, "NO_POSITION")
            except Exception as exc:
                row["status"] = "error"
                row["error"] = str(exc)
                for strategy in selected:
                    row[strategy] = "NO_POSITION"
            records.append(row)

        output_df = add_aggregate_decision_column(
            pd.DataFrame(records),
            strategy_columns=selected,
        )
        records = output_df.to_dict(orient="records")
        output_file = self.save_reference_prediction_matrix(reference_date, output_df) if save_to_disk else None

        return {
            "reference_date": reference_date.isoformat(),
            "output_file": output_file,
            "strategies": selected,
            "records": records,
        }

    def save_reference_prediction_matrix(
        self,
        reference_date: date,
        predictions_df: pd.DataFrame,
    ) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{reference_date.isoformat()}.csv"
        output_path = self.output_dir / filename
        predictions_df.to_csv(output_path, index=False)
        return filename

    def save_reference_prediction_file(
        self,
        instrument: str,
        reference_date: date,
        predictions_df: pd.DataFrame,
    ) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{instrument.upper()}_prediction_{reference_date.isoformat()}.csv"
        output_path = self.output_dir / filename
        predictions_df.to_csv(output_path, index=False)
        return filename

    def get_selected_strategy_names(self, strategies: list[str] | None = None) -> list[str]:
        strategy_registry = load_underlying_prediction_strategies()
        selected = strategies or sorted(strategy_registry.keys())
        unknown_strategies = [name for name in selected if name not in strategy_registry]
        if unknown_strategies:
            available = ", ".join(sorted(strategy_registry.keys()))
            unknown = ", ".join(unknown_strategies)
            raise ValueError(f"Unknown prediction strategy '{unknown}'. Available strategies: {available}")
        return selected

    def generate_consolidated_predictions(
        self,
        instrument: str,
        start_date: date,
        end_date: date,
        strategies: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Generate one consolidated prediction table for one underlying.

        Output shape:
          date,underlying,<strategy columns>,aggregate_decision
        """
        instrument = instrument.upper()
        selected = self.get_selected_strategy_names(strategies)
        warmup_start = start_date - timedelta(days=max(self.default_lookback * 3, 45))

        conn = get_db_connection()
        try:
            df_daily = fetch_index_daily(
                conn=conn,
                underlying=instrument,
                start_date=warmup_start.isoformat(),
                end_date=end_date.isoformat(),
                join_activity=True,
            )
        finally:
            conn.close()

        if df_daily.empty:
            raise ValueError(f"[{instrument}] fetched 0 rows from DB for daily data.")

        df_daily = df_daily.copy()
        df_daily["trade_date"] = pd.to_datetime(df_daily["trade_date"])
        df_daily = df_daily.sort_values("trade_date").reset_index(drop=True)
        if len(df_daily) < self.default_lookback:
            raise ValueError(f"Not enough rows to generate predictions for {instrument}.")

        records: list[dict[str, object]] = []
        for i in range(self.default_lookback - 1, len(df_daily)):
            decision_date = pd.to_datetime(df_daily.loc[i, "trade_date"]).date()
            if decision_date < start_date or decision_date > end_date:
                continue

            window_df = df_daily.loc[i - self.default_lookback + 1 : i].copy()
            predictions = get_underlying_strategy_predictions(
                window=window_df,
                strategies=selected,
            )
            row: dict[str, object] = {
                "date": decision_date.isoformat(),
                "underlying": instrument,
            }
            for strategy in selected:
                row[strategy] = predictions.get(strategy, "NO_POSITION")
            records.append(row)

        if not records:
            return pd.DataFrame(columns=["date", "underlying", *selected, "aggregate_decision"])

        return add_aggregate_decision_column(
            pd.DataFrame(records),
            strategy_columns=selected,
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
        strategy_registry = load_underlying_prediction_strategies()
        if strategy not in strategy_registry:
            available = ", ".join(strategy_registry.keys())
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
        ta_fn = strategy_registry[strategy]

        for i in range(lookback_days - 1, len(df_daily)):
            window_df = df_daily.loc[i - lookback_days + 1 : i].copy()
            decision_date = pd.to_datetime(df_daily.loc[i, "trade_date"])

            if use_agentic:
                output = run_underlying_prediction(
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

    def generate_predictions_all_strategies(
        self,
        instrument: str,
        use_agentic: bool,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        instrument = instrument.upper()
        strategy_registry = load_underlying_prediction_strategies()
        strategy_names = sorted(strategy_registry.keys())
        out: dict[str, pd.DataFrame] = {}

        for strategy in strategy_names:
            out[strategy] = self.generate_predictions_for_strategy(
                instrument=instrument,
                strategy=strategy,
                use_agentic=use_agentic,
                start_date=start_date,
                end_date=end_date,
            )

        if not out:
            return out

        combined = None
        for strategy_name in strategy_names:
            df = out[strategy_name].copy()
            df = df[["date", "prediction"]].rename(columns={"prediction": strategy_name})
            combined = df if combined is None else combined.merge(df, on="date", how="outer")

        if combined is None or combined.empty:
            return out

        def vote(row: pd.Series) -> str:
            vals = [row[name] for name in strategy_names if row.get(name) in ("CALL", "PUT")]
            if not vals:
                return "NO_POSITION"
            calls = vals.count("CALL")
            puts = vals.count("PUT")
            if calls > puts:
                return "CALL"
            if puts > calls:
                return "PUT"
            return "NO_POSITION"

        combined["prediction"] = combined.apply(vote, axis=1)
        out["combined"] = combined[["date", "prediction"]].sort_values("date").reset_index(drop=True)
        return out

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

        selection_registry = load_option_selection_strategies()
        if selector_strategy not in selection_registry:
            available = ", ".join(selection_registry.keys())
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

        selection_func = selection_registry[selector_strategy]
        selected_df = apply_option_selection(
            preds=preds,
            options_df=options_df,
            selection_func=selection_func,
        )
        selected_df.to_csv(strategy_path, index=False)

        if delete_intermediate and base_path.exists():
            base_path.unlink()

        return strategy_filename

