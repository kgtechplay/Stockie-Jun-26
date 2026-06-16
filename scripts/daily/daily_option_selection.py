from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.technical_analysis.optionselection.option_selector import select_option_strategy
from src.technical_analysis.prediction.schema import StrategySignal, UnderlyingView


def load_prediction_view(
    db,
    underlying: str,
    selection_date: date,
    prediction_date: date | None = None,
) -> tuple[UnderlyingView, int, date]:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = '"UnderlyingPredictionDaily"' if is_postgres else "dbo.UnderlyingPredictionDaily"
    ph = "%s" if is_postgres else "?"

    if prediction_date is None:
        sql = f"""
            SELECT *
            FROM {table}
            WHERE UPPER(symbol) = {ph}
              AND trade_date <= {ph}
            ORDER BY trade_date DESC
            LIMIT 1
        """ if is_postgres else f"""
            SELECT TOP 1 *
            FROM {table}
            WHERE UPPER(symbol) = {ph}
              AND trade_date <= {ph}
            ORDER BY trade_date DESC
        """
        params = (underlying.upper(), selection_date)
    else:
        sql = f"""
            SELECT *
            FROM {table}
            WHERE UPPER(symbol) = {ph}
              AND trade_date = {ph}
        """
        params = (underlying.upper(), prediction_date)

    with db.conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description] if cur.description else []

    if row is None:
        target = prediction_date.isoformat() if prediction_date else f"latest <= {selection_date}"
        raise ValueError(f"No {underlying.upper()} prediction found for {target}")

    data = _row_to_dict(row, columns)
    source_prediction_date = _as_date(data["trade_date"])
    return _prediction_row_to_view(data, selection_date), int(data.get("prediction_id") or 0), source_prediction_date


def fetch_spot_price(db, underlying: str, as_of_date: date) -> float:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    table = '"UnderlyingSnapshot"' if is_postgres else "dbo.UnderlyingSnapshot"
    ph = "%s" if is_postgres else "?"
    sql = f"""
        SELECT close_price
        FROM {table}
        WHERE UPPER(underlying) = {ph}
          AND trade_date <= {ph}
          AND close_price IS NOT NULL
        ORDER BY trade_date DESC
        LIMIT 1
    """ if is_postgres else f"""
        SELECT TOP 1 close_price
        FROM {table}
        WHERE UPPER(underlying) = {ph}
          AND trade_date <= {ph}
          AND close_price IS NOT NULL
        ORDER BY trade_date DESC
    """
    with db.conn.cursor() as cur:
        cur.execute(sql, (underlying.upper(), as_of_date))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"No spot close found for {underlying.upper()} on or before {as_of_date}")
    return float(row[0])


def fetch_atm_iv_history(
    db,
    underlying: str,
    spot_price: float,
    as_of_date: date,
    limit: int = 90,
) -> list[float]:
    is_postgres = getattr(db, "db_kind", "") == "postgres"
    option_snapshot = '"OptionSnapshot"' if is_postgres else "dbo.OptionSnapshot"
    option_instrument = '"OptionInstrument"' if is_postgres else "dbo.OptionInstrument"
    option_calc = '"OptionSnapshotCalc"' if is_postgres else "dbo.OptionSnapshotCalc"
    ph = "%s" if is_postgres else "?"
    sql = f"""
        WITH ranked AS (
            SELECT
                os.trade_date,
                calc.implied_volatility,
                ROW_NUMBER() OVER (
                    PARTITION BY os.trade_date
                    ORDER BY ABS(oi.strike - {ph}), os.snapshot_time DESC, os.id DESC
                ) AS rn
            FROM {option_snapshot} os
            INNER JOIN {option_instrument} oi
                ON oi.id = os.option_instrument_id
            INNER JOIN {option_calc} calc
                ON calc.option_snapshot_id = os.id
            WHERE UPPER(oi.underlying) = {ph}
              AND os.trade_date <= {ph}
              AND calc.implied_volatility IS NOT NULL
        )
        SELECT implied_volatility
        FROM ranked
        WHERE rn = 1
        ORDER BY trade_date DESC
        LIMIT {int(limit)}
    """ if is_postgres else f"""
        WITH ranked AS (
            SELECT
                CAST(os.snapshot_time AS date) AS trade_date,
                calc.implied_volatility,
                ROW_NUMBER() OVER (
                    PARTITION BY CAST(os.snapshot_time AS date)
                    ORDER BY ABS(oi.strike - {ph}), os.snapshot_time DESC, os.id DESC
                ) AS rn
            FROM {option_snapshot} os
            INNER JOIN {option_instrument} oi
                ON oi.id = os.option_instrument_id
            INNER JOIN {option_calc} calc
                ON calc.option_snapshot_id = os.id
            WHERE UPPER(oi.underlying) = {ph}
              AND CAST(os.snapshot_time AS date) <= {ph}
              AND calc.implied_volatility IS NOT NULL
        )
        SELECT TOP {int(limit)} implied_volatility
        FROM ranked
        WHERE rn = 1
        ORDER BY trade_date DESC
    """
    with db.conn.cursor() as cur:
        cur.execute(sql, (spot_price, underlying.upper(), as_of_date))
        return [float(row[0]) for row in cur.fetchall() if row[0] is not None]


def run_daily_option_selection(
    underlying: str,
    selection_date: date,
    prediction_date: date | None,
    as_of_time: str | None,
    output_dir: Path,
) -> dict[str, Any]:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        view, prediction_id, source_prediction_date = load_prediction_view(
            db=db,
            underlying=underlying,
            selection_date=selection_date,
            prediction_date=prediction_date,
        )
        spot_price = fetch_spot_price(db, underlying, source_prediction_date)
        iv_history = fetch_atm_iv_history(db, underlying, spot_price, source_prediction_date)
        effective_as_of_time = as_of_time or f"{source_prediction_date.isoformat()} 15:15:00"
        result = select_option_strategy(
            db_client=db,
            underlying_view=view,
            spot_price=spot_price,
            as_of_time=effective_as_of_time,
            atm_iv_history_90d=iv_history,
        )
    finally:
        db.close()

    payload = {
        "underlying": underlying.upper(),
        "selection_date": selection_date.isoformat(),
        "source_prediction_date": source_prediction_date.isoformat(),
        "prediction_id": prediction_id,
        "spot_price": spot_price,
        "as_of_time": effective_as_of_time,
        "atm_iv_history_count": len(iv_history),
        "underlying_signal": {
            "raw_signal": view.raw_signal,
            "direction": view.direction,
            "regime": view.stock_regime,
            "strength_score": view.strength_score,
            "confidence": view.confidence,
            "option_bias": view.option_bias,
            "expected_move_pct": view.expected_move_pct,
            "expected_holding_days": view.expected_holding_days,
        },
        "selection": _plain(result),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{underlying.upper()}_option_selection_{selection_date.isoformat()}"
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _write_summary_csv(csv_path, payload)
    payload["json_path"] = str(json_path)
    payload["csv_path"] = str(csv_path)
    return payload


def _prediction_row_to_view(row: dict[str, Any], selection_date: date) -> UnderlyingView:
    return UnderlyingView(
        symbol=str(row["symbol"]).upper(),
        trade_date=selection_date.isoformat(),
        raw_signal=(row.get("raw_signal") or "NO_POSITION"),
        direction=(row.get("direction") or "NEUTRAL"),
        stock_regime=(row.get("stock_regime") or "UNKNOWN"),
        sector_regime=row.get("sector_regime"),
        benchmark_regime=row.get("benchmark_regime"),
        primary_strategy=row.get("primary_strategy"),
        setup_type=(row.get("setup_type") or "NO_SETUP"),
        strength_score=float(row.get("strength_score") or 0),
        confidence=(row.get("confidence") or "LOW"),
        expected_move_pct=_float_or_none(row.get("expected_move_pct")),
        expected_move_abs=_float_or_none(row.get("expected_move_abs")),
        expected_holding_days=int(row.get("expected_holding_days") or 1),
        atr14=_float_or_none(row.get("atr14")),
        volatility_20d=_float_or_none(row.get("volatility_20d")),
        volume_ratio=_float_or_none(row.get("volume_ratio")),
        relative_strength_vs_sector=_float_or_none(row.get("relative_strength_vs_sector")),
        relative_strength_vs_benchmark=_float_or_none(row.get("relative_strength_vs_benchmark")),
        stock_technical_score=float(row.get("stock_technical_score") or 0),
        sector_confirmation_score=float(row.get("sector_confirmation_score") or 0),
        benchmark_confirmation_score=float(row.get("benchmark_confirmation_score") or 0),
        relative_strength_score=float(row.get("relative_strength_score") or 0),
        volume_confirmation_score=float(row.get("volume_confirmation_score") or 0),
        risk_quality_score=float(row.get("risk_quality_score") or 0),
        regime_quality_score=float(row.get("regime_quality_score") or 0),
        strategy_signals=_strategy_signals(row.get("strategy_signals_json")),
        reasons=_json_list(row.get("reasons_json")),
        warnings=_json_list(row.get("warnings_json")),
        is_option_eligible=bool(row.get("is_option_eligible")),
        option_bias=(row.get("option_bias") or "NEUTRAL"),
    )


def _strategy_signals(value: Any) -> list[StrategySignal]:
    output: list[StrategySignal] = []
    for item in _json_list(value):
        if isinstance(item, dict):
            allowed = {field: item.get(field) for field in StrategySignal.__dataclass_fields__}
            try:
                output.append(StrategySignal(**allowed))
            except TypeError:
                continue
    return output


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {columns[i]: row[i] for i in range(len(columns))}


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _write_summary_csv(path: Path, payload: dict[str, Any]) -> None:
    selection = payload["selection"]
    candidate = selection["selected_strategy"]
    first_leg = (candidate.get("legs") or [{}])[0]
    contract = first_leg.get("contract", {})
    features = first_leg.get("features", {})
    row = {
        "selection_date": payload["selection_date"],
        "source_prediction_date": payload["source_prediction_date"],
        "underlying": payload["underlying"],
        "raw_signal": payload["underlying_signal"]["raw_signal"],
        "direction": payload["underlying_signal"]["direction"],
        "regime": payload["underlying_signal"]["regime"],
        "underlying_strength_score": payload["underlying_signal"]["strength_score"],
        "option_bias": selection["option_bias"],
        "decision": "NO_TRADE" if selection["no_trade_reason"] else "TRADE",
        "no_trade_reason": selection["no_trade_reason"],
        "strategy_type": candidate["strategy_type"],
        "candidate_score": candidate["score"],
        "confidence": candidate["confidence"],
        "tradingsymbol": contract.get("tradingsymbol"),
        "expiry": contract.get("expiry"),
        "strike": contract.get("strike"),
        "option_type": contract.get("option_type"),
        "entry_debit_or_credit": candidate["entry_debit_or_credit"],
        "max_loss": candidate["max_loss"],
        "reward_risk": candidate["reward_risk"],
        "liquidity_score": features.get("liquidity_score"),
        "spread_pct": features.get("spread_pct"),
        "theta_burn_pct_per_day": features.get("theta_burn_pct_per_day"),
        "iv_rank_90d": features.get("iv_rank_90d"),
        "iv_percentile_90d": features.get("iv_percentile_90d"),
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select an option strategy from stored UnderlyingPredictionDaily and option snapshot/calculation rows."
    )
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--selection-date", default=date.today().isoformat(), help="N+1 trade date, default today")
    parser.add_argument("--prediction-date", default=None, help="Prediction row date. Defaults to latest <= selection date")
    parser.add_argument("--as-of-time", default=None, help="Option chain cutoff timestamp. Defaults to prediction date 15:15")
    parser.add_argument("--output-dir", default="output/option_selection")
    args = parser.parse_args()

    result = run_daily_option_selection(
        underlying=args.underlying,
        selection_date=date.fromisoformat(args.selection_date),
        prediction_date=date.fromisoformat(args.prediction_date) if args.prediction_date else None,
        as_of_time=args.as_of_time,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps({
        "underlying": result["underlying"],
        "selection_date": result["selection_date"],
        "source_prediction_date": result["source_prediction_date"],
        "decision": "NO_TRADE" if result["selection"]["no_trade_reason"] else "TRADE",
        "strategy_type": result["selection"]["selected_strategy"]["strategy_type"],
        "candidate_score": result["selection"]["selected_strategy"]["score"],
        "json_path": result["json_path"],
        "csv_path": result["csv_path"],
    }, indent=2))


if __name__ == "__main__":
    main()
