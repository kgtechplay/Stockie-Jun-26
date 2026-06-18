"""
NIFTY option selection — unit tests + E2E CSV generator.

Unittest mode (pytest):
    pytest backtest/test_optionselection_e2e.py

Script mode — read NIFTY_prediction.csv, run option selection + P&L backtest,
write output/backtest/NIFTY_optionSelection.csv:
    python backtest/test_optionselection_e2e.py
    python backtest/test_optionselection_e2e.py --input output/backtest/NIFTY_prediction.csv
    python backtest/test_optionselection_e2e.py --input output/backtest/NIFTY_prediction.csv \\
                                                 --output output/backtest/NIFTY_optionSelection.csv

P&L methodology:
    - as_of_time = signal_date 15:15:00 (EOD chain used for option selection)
    - Entry price = first OptionSnapshot price on the NEXT trading day
    - Exit scan = subsequent intraday snapshots; exit when +2% profit OR -1% stop loss
    - Entry/exit uses the primary BUY leg of the selected strategy
    - Rows with is_option_eligible=False skip option selection (tagged NO_TRADE)
"""

from __future__ import annotations

import argparse
import sys
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
from dotenv import load_dotenv

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")

# ── unit test imports ─────────────────────────────────────────────────────────
from src.technical_analysis.optionselection.candidate_filter import (
    filter_long_call_candidates,
    filter_long_put_candidates,
)
from src.technical_analysis.optionselection.option_features import (
    compute_option_features_for_chain,
    compute_spread_pct,
    compute_theta_burn_pct_per_day,
)
from src.technical_analysis.optionselection.option_selector import select_option_strategy
from src.technical_analysis.optionselection.risk import calculate_strategy_risk
from src.technical_analysis.optionselection.schema import OptionContract, OptionSelectionResult
from src.technical_analysis.optionselection.scoring import score_option_candidate
from src.technical_analysis.optionselection.strategy_builder import build_strategy_candidates
from src.technical_analysis.optionselection.strategy_rules import choose_option_strategy_type
from src.technical_analysis.optionselection.underlying_view_strength import derive_option_bias
from src.technical_analysis.prediction.schema import UnderlyingView

# ── pipeline imports ──────────────────────────────────────────────────────────
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client


# ─────────────────────────────────────────────────────────────────────────────
# Unit test helpers  (preserved from original test_optionselection.py)
# ─────────────────────────────────────────────────────────────────────────────

def view(
    raw_signal: str = "CALL",
    direction: str = "BULLISH",
    score: float = 85,
    stock_regime: str = "TREND_UP",
) -> UnderlyingView:
    return UnderlyingView(
        symbol="NIFTY",
        trade_date="2026-05-15",
        raw_signal=raw_signal,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        stock_regime=stock_regime,  # type: ignore[arg-type]
        sector_regime="TREND_UP",
        benchmark_regime="TREND_UP",
        primary_strategy="MaTrend_001",
        setup_type="TREND_UP_PULLBACK_LONG",
        strength_score=score,
        confidence="HIGH" if score >= 80 else "MEDIUM",
        expected_move_pct=0.012,
        expected_move_abs=120,
        expected_holding_days=3,
        atr14=100,
        volatility_20d=0.02,
        volume_ratio=1.2,
        relative_strength_vs_sector=0.02,
        relative_strength_vs_benchmark=0.02,
        stock_technical_score=25,
        sector_confirmation_score=15,
        benchmark_confirmation_score=10,
        relative_strength_score=15,
        volume_confirmation_score=7,
        risk_quality_score=10,
        regime_quality_score=15,
        strategy_signals=[],
        reasons=[],
        warnings=[],
        is_option_eligible=True,
        option_bias="BULLISH_STRONG" if score >= 80 else "BULLISH_MODERATE",
    )


def contract(
    symbol: str,
    strike: float,
    option_type: str,
    delta: float,
    expiry: str = "2026-05-29",
    bid: float = 99,
    ask: float = 101,
    last_price: float = 100,
    theta: float = -4,
    iv: float = 0.22,
) -> OptionContract:
    return OptionContract(
        instrument_token=1,
        tradingsymbol=symbol,
        underlying="NIFTY",
        expiry=expiry,
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        last_price=last_price,
        bid=bid,
        ask=ask,
        volume=1000,
        open_interest=5000,
        iv=iv,
        delta=delta,
        gamma=0.001,
        theta=theta,
        vega=5,
    )


class OptionSelectionTests(unittest.TestCase):
    def test_feature_calculations(self) -> None:
        self.assertAlmostEqual(compute_spread_pct(99, 101) or 0, 0.02)
        self.assertAlmostEqual(compute_theta_burn_pct_per_day(-4, 100) or 0, 0.04)

    def test_bias_and_strategy_rules(self) -> None:
        self.assertEqual(derive_option_bias(view()), "BULLISH_STRONG")
        self.assertEqual(derive_option_bias(view(score=60)), "NEUTRAL")
        self.assertEqual(derive_option_bias(view(stock_regime="CHOPPY")), "NEUTRAL")
        self.assertEqual(
            choose_option_strategy_type("BULLISH_STRONG", 40, None, 0.01, 3, 10),
            "LONG_CALL",
        )
        self.assertEqual(
            choose_option_strategy_type("BULLISH_STRONG", 75, None, 0.01, 3, 10),
            "BULL_CALL_SPREAD",
        )
        self.assertEqual(
            choose_option_strategy_type("BEARISH_MODERATE", 40, None, 0.01, 3, 10),
            "BEAR_PUT_SPREAD",
        )

    def test_long_candidate_filters(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10000PE", 10000, "PE", -0.52),
            contract("NIFTY26MAY10500CE", 10500, "CE", 0.10),
        ]
        features = compute_option_features_for_chain(contracts, 10000, "2026-05-15", [0.15, 0.20, 0.25])
        self.assertEqual(len(filter_long_call_candidates(contracts, features)), 1)
        self.assertEqual(len(filter_long_put_candidates(contracts, features)), 1)

    def test_strategy_build_risk_and_score(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10200CE", 10200, "CE", 0.30, bid=39, ask=41, last_price=40),
        ]
        features = compute_option_features_for_chain(contracts, 10000, "2026-05-15", [0.15, 0.20, 0.25])
        candidates = build_strategy_candidates("BULL_CALL_SPREAD", contracts, features, view(), 10000)
        self.assertEqual(len(candidates), 1)
        risked = calculate_strategy_risk(candidates[0])
        self.assertGreater(risked.total_delta or 0, 0)
        scored = score_option_candidate(risked, view(), features)
        self.assertGreaterEqual(scored.score, 65)

    def test_selector_no_trade_for_weak_underlying(self) -> None:
        result = select_option_strategy(object(), view(score=60), 10000)
        self.assertEqual(result.selected_strategy.strategy_type, "NO_TRADE")
        self.assertEqual(result.no_trade_reason, "Underlying signal score below threshold")

    def test_selector_selects_long_call(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10000PE", 10000, "PE", -0.52),
        ]
        with patch(
            "src.technical_analysis.optionselection.option_selector.load_option_chain_with_calcs",
            return_value=contracts,
        ):
            result = select_option_strategy(object(), view(), 10000, atm_iv_history_90d=[0.10, 0.20, 0.40])
        self.assertEqual(result.selected_strategy.strategy_type, "LONG_CALL")
        self.assertIsNone(result.no_trade_reason)


# ─────────────────────────────────────────────────────────────────────────────
# E2E pipeline helpers
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_INPUT = Path("output") / "backtest" / "NIFTY_prediction.csv"
DEFAULT_OUTPUT = Path("output") / "backtest" / "NIFTY_optionSelection.csv"

_PROFIT_TARGET_PCT = 0.02
_PNL_SCAN_DAYS = 5


def _f(val: Any) -> float | None:
    try:
        if val is None or pd.isna(val):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _reconstruct_view(row: dict[str, Any]) -> UnderlyingView:
    return UnderlyingView(
        symbol="NIFTY",
        trade_date=str(row["date"]),
        raw_signal=str(row.get("raw_signal") or "NO_POSITION"),  # type: ignore[arg-type]
        direction=str(row.get("direction") or "NEUTRAL"),  # type: ignore[arg-type]
        stock_regime=str(row.get("stock_regime") or "UNKNOWN"),  # type: ignore[arg-type]
        sector_regime=None,
        benchmark_regime=None,
        primary_strategy=str(row.get("primary_strategy") or "") or None,
        setup_type=str(row.get("setup_type") or ""),
        strength_score=float(row.get("strength_score") or 0),
        confidence=str(row.get("confidence") or "LOW"),  # type: ignore[arg-type]
        expected_move_pct=_f(row.get("expected_move_pct")),
        expected_move_abs=_f(row.get("expected_move_abs")),
        expected_holding_days=int(row.get("expected_holding_days") or 1),
        atr14=_f(row.get("atr14")),
        volatility_20d=_f(row.get("volatility_20d")),
        volume_ratio=None,
        relative_strength_vs_sector=_f(row.get("relative_strength_vs_sector")),
        relative_strength_vs_benchmark=None,
        stock_technical_score=float(row.get("stock_technical_score") or 0),
        sector_confirmation_score=float(row.get("sector_confirmation_score") or 0),
        benchmark_confirmation_score=float(row.get("benchmark_confirmation_score") or 0),
        relative_strength_score=float(row.get("relative_strength_score") or 0),
        volume_confirmation_score=float(row.get("volume_confirmation_score") or 0),
        risk_quality_score=float(row.get("risk_quality_score") or 0),
        regime_quality_score=float(row.get("regime_quality_score") or 0),
        strategy_signals=[],
        reasons=[],
        warnings=[],
        is_option_eligible=str(row.get("is_option_eligible") or "").lower() in ("true", "1"),
        option_bias=str(row.get("option_bias") or "NEUTRAL"),  # type: ignore[arg-type]
    )


def _fetch_atm_iv_history(conn, underlying: str, spot_price: float, as_of_date: date) -> list[float]:
    sql = """
        WITH ranked AS (
            SELECT
                os.trade_date,
                calc.implied_volatility,
                ROW_NUMBER() OVER (
                    PARTITION BY os.trade_date
                    ORDER BY ABS(oi.strike - %s), os.snapshot_time DESC, os.id DESC
                ) AS rn
            FROM "OptionSnapshot" os
            JOIN "OptionInstrument" oi ON oi.id = os.option_instrument_id
            JOIN "OptionSnapshotCalc" calc ON calc.option_snapshot_id = os.id
            WHERE UPPER(oi.underlying) = %s
              AND os.trade_date <= %s
              AND calc.implied_volatility IS NOT NULL
              AND calc.implied_volatility > 0
        )
        SELECT implied_volatility FROM ranked WHERE rn = 1
        ORDER BY trade_date DESC
        LIMIT 90
    """
    with conn.cursor() as cur:
        cur.execute(sql, (spot_price, underlying.upper(), as_of_date))
        rows = cur.fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def _to_date(val: Any) -> date:
    return val if isinstance(val, date) else val.date() if hasattr(val, "date") else val


def _fetch_next_n_trading_days(conn, underlying: str, after_date: date, n: int = _PNL_SCAN_DAYS) -> list[date]:
    sql = """
        SELECT trade_date FROM "UnderlyingSnapshot"
        WHERE underlying = %s AND trade_date > %s
        ORDER BY trade_date
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (underlying.upper(), after_date, n))
        rows = cur.fetchall()
    return [_to_date(r[0]) for r in rows]


def _fetch_option_snapshots_for_dates(
    conn, instrument_token: int, trade_dates: list[date]
) -> pd.DataFrame:
    if not trade_dates:
        return pd.DataFrame(columns=["trade_date", "snapshot_time", "option_price", "lot_size"])
    sql = """
        SELECT os.trade_date, os.snapshot_time, os.last_price AS option_price, oi.lot_size
        FROM "OptionSnapshot" os
        JOIN "OptionInstrument" oi ON oi.id = os.option_instrument_id
        WHERE oi.instrument_token = %s
          AND os.trade_date IN %s
          AND os.last_price IS NOT NULL
          AND os.last_price > 0
        ORDER BY os.snapshot_time
    """
    with conn.cursor() as cur:
        cur.execute(sql, (instrument_token, tuple(trade_dates)))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
    if not rows:
        return pd.DataFrame(columns=["trade_date", "snapshot_time", "option_price", "lot_size"])
    return pd.DataFrame(rows, columns=cols)


def _calculate_pnl(all_snaps: pd.DataFrame, next_day: date) -> dict[str, Any]:
    """
    Entry  = first snapshot on next_day (open proxy).
    Exit   = last  snapshot on next_day (EOD close proxy).
    P&L    = exit − entry.
    5d scan: first time across all_snaps where price >= entry * 1.02.
    """
    empty: dict[str, Any] = {
        "entry_price": None, "entry_time": None,
        "exit_price": None, "exit_time": None,
        "lot_size": None, "pnl_per_unit": None, "pnl_per_lot": None,
        "return_pct": None, "option_result": None,
        "first_2pct_profit_datetime": None,
    }
    if all_snaps.empty:
        return empty

    all_snaps = all_snaps.sort_values("snapshot_time").reset_index(drop=True)

    next_day_snaps = all_snaps[all_snaps["trade_date"].apply(
        lambda v: _to_date(v) == next_day
    )]
    if next_day_snaps.empty:
        return empty

    entry_row = next_day_snaps.iloc[0]
    exit_row = next_day_snaps.iloc[-1]
    entry_price = float(entry_row["option_price"])
    if entry_price <= 0:
        return empty

    lot_size_raw = entry_row.get("lot_size")
    lot_size = int(lot_size_raw) if lot_size_raw is not None and not pd.isna(lot_size_raw) else None

    exit_price = float(exit_row["option_price"])
    pnl_per_unit = exit_price - entry_price
    pnl_per_lot = pnl_per_unit * lot_size if lot_size else None
    return_pct = pnl_per_unit / entry_price

    # 5-day scan for first 2% profit
    profit_target = entry_price * (1 + _PROFIT_TARGET_PCT)
    first_2pct: str | None = None
    for _, row in all_snaps.iterrows():
        if float(row["option_price"]) >= profit_target:
            first_2pct = str(row["snapshot_time"])[:19]
            break

    return {
        "entry_time": str(entry_row["snapshot_time"])[:19],
        "entry_price": round(entry_price, 2),
        "exit_time": str(exit_row["snapshot_time"])[:19],
        "exit_price": round(exit_price, 2),
        "lot_size": lot_size,
        "pnl_per_unit": round(pnl_per_unit, 2),
        "pnl_per_lot": round(pnl_per_lot, 2) if pnl_per_lot is not None else None,
        "return_pct": round(return_pct * 100, 2),
        "option_result": "PROFIT" if pnl_per_unit > 0 else ("LOSS" if pnl_per_unit < 0 else "BREAKEVEN"),
        "first_2pct_profit_datetime": first_2pct,
    }


def _flatten_result(result: OptionSelectionResult) -> dict[str, Any]:
    cand = result.selected_strategy
    legs_summary = "; ".join(
        f"{leg.side} {leg.contract.tradingsymbol} @{leg.contract.last_price}"
        for leg in cand.legs
    ) if cand.legs else ""
    first_buy = next((leg for leg in cand.legs if leg.side == "BUY"), None)
    return {
        "selected_strategy": cand.strategy_type,
        "option_bias_selected": str(result.option_bias),
        "no_trade_reason": result.no_trade_reason or "",
        "evaluated_candidate_count": result.evaluated_candidate_count,
        "strategy_direction": cand.direction,
        "entry_debit_or_credit": cand.entry_debit_or_credit,
        "max_profit": cand.max_profit,
        "max_loss": cand.max_loss,
        "breakeven": cand.breakeven,
        "reward_risk": round(cand.reward_risk, 2) if cand.reward_risk else None,
        "selection_score": cand.score,
        "selection_confidence": str(cand.confidence),
        "total_delta": cand.total_delta,
        "total_theta": cand.total_theta,
        "total_vega": cand.total_vega,
        "legs_summary": legs_summary,
        "primary_buy_token": first_buy.contract.instrument_token if first_buy else None,
        "primary_buy_symbol": first_buy.contract.tradingsymbol if first_buy else None,
        "primary_buy_strike": first_buy.contract.strike if first_buy else None,
        "primary_buy_expiry": first_buy.contract.expiry if first_buy else None,
        "primary_buy_option_type": first_buy.contract.option_type if first_buy else None,
        "primary_buy_entry_price_signal": first_buy.contract.last_price if first_buy else None,
        "primary_buy_iv": first_buy.contract.iv if first_buy else None,
        "primary_buy_delta": first_buy.contract.delta if first_buy else None,
        "selection_reasons": " | ".join(cand.reasons),
        "selection_warnings": " | ".join(cand.warnings),
    }


def generate_option_selection_csv(
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_OUTPUT,
    underlying: str = "NIFTY",
) -> dict[str, Any]:
    if not input_path.exists():
        print(f"Prediction CSV missing at {input_path}; running underlying prediction first...")
        from backtest.test_underlying_prediction import generate_prediction_csv

        prediction_result = generate_prediction_csv(
            underlying=underlying.upper(),
            output_path=input_path,
            regime_comparison_path=input_path.with_name(
                f"{underlying.upper()}_regime_experiment_comparison.csv"
            ),
        )
        if int(prediction_result.get("rows") or 0) == 0 or not input_path.exists():
            raise FileNotFoundError(
                f"Prediction CSV not found at {input_path} and automatic generation produced no rows."
            )

    pred_df = pd.read_csv(input_path)
    if pred_df.empty:
        print("Prediction CSV is empty — nothing to process.")
        return {"rows": 0, "path": str(output_path)}

    print(f"Loaded {len(pred_df)} prediction rows from {input_path}")

    settings = get_settings()
    db = get_database_client(settings)
    db.connect()

    output_rows: list[dict[str, Any]] = []
    try:
        for _, pred_row in pred_df.iterrows():
            base = pred_row.to_dict()
            signal_date_str = str(base["date"])
            signal_date = date.fromisoformat(signal_date_str)
            is_eligible = str(base.get("is_option_eligible") or "").lower() in ("true", "1")

            if not is_eligible:
                base.update({
                    "selected_strategy": "NO_TRADE",
                    "option_bias_selected": base.get("option_bias", "NEUTRAL"),
                    "no_trade_reason": "Not option eligible",
                    "evaluated_candidate_count": 0,
                    "strategy_direction": "", "entry_debit_or_credit": None,
                    "max_profit": None, "max_loss": None, "breakeven": None,
                    "reward_risk": None, "selection_score": 0, "selection_confidence": "",
                    "total_delta": None, "total_theta": None, "total_vega": None,
                    "legs_summary": "", "primary_buy_token": None, "primary_buy_symbol": None,
                    "primary_buy_strike": None, "primary_buy_expiry": None,
                    "primary_buy_option_type": None, "primary_buy_entry_price_signal": None,
                    "primary_buy_iv": None, "primary_buy_delta": None,
                    "selection_reasons": "", "selection_warnings": "",
                    "next_trading_day": None,
                    "entry_time": None, "entry_price": None,
                    "exit_time": None, "exit_price": None,
                    "lot_size": None, "pnl_per_unit": None, "pnl_per_lot": None,
                    "return_pct": None, "option_result": None,
                    "first_2pct_profit_datetime": None,
                })
                output_rows.append(base)
                continue

            underlying_view = _reconstruct_view(base)
            spot_price = _f(base.get("close")) or 0.0
            as_of_time = f"{signal_date_str} 15:15:00"

            iv_history: list[float] = []
            if spot_price > 0:
                try:
                    iv_history = _fetch_atm_iv_history(db.conn, underlying, spot_price, signal_date)
                except Exception as exc:
                    print(f"  {signal_date_str}: IV history fetch failed — {exc}")

            try:
                result = select_option_strategy(db, underlying_view, spot_price, as_of_time, iv_history or None)
            except Exception as exc:
                print(f"  {signal_date_str}: option selection failed — {exc}")
                result = None

            flat: dict[str, Any] = {}
            if result is not None:
                flat = _flatten_result(result)
            else:
                flat = {
                    "selected_strategy": "NO_TRADE", "option_bias_selected": "",
                    "no_trade_reason": "Selection error", "evaluated_candidate_count": 0,
                    "strategy_direction": "", "entry_debit_or_credit": None,
                    "max_profit": None, "max_loss": None, "breakeven": None,
                    "reward_risk": None, "selection_score": 0, "selection_confidence": "",
                    "total_delta": None, "total_theta": None, "total_vega": None,
                    "legs_summary": "", "primary_buy_token": None, "primary_buy_symbol": None,
                    "primary_buy_strike": None, "primary_buy_expiry": None,
                    "primary_buy_option_type": None, "primary_buy_entry_price_signal": None,
                    "primary_buy_iv": None, "primary_buy_delta": None,
                    "selection_reasons": "", "selection_warnings": "",
                }

            # P&L: only if a real trade was selected
            pnl: dict[str, Any] = {
                "next_trading_day": None,
                "entry_time": None, "entry_price": None,
                "exit_time": None, "exit_price": None,
                "lot_size": None, "pnl_per_unit": None, "pnl_per_lot": None,
                "return_pct": None, "option_result": None,
                "first_2pct_profit_datetime": None,
            }
            buy_token = flat.get("primary_buy_token")
            if flat.get("selected_strategy") not in (None, "", "NO_TRADE") and buy_token:
                try:
                    trading_days = _fetch_next_n_trading_days(db.conn, underlying, signal_date)
                    next_day = trading_days[0] if trading_days else None
                    pnl["next_trading_day"] = str(next_day) if next_day else None
                    if next_day and trading_days:
                        snaps = _fetch_option_snapshots_for_dates(db.conn, int(buy_token), trading_days)
                        pnl.update(_calculate_pnl(snaps, next_day))
                except Exception as exc:
                    print(f"  {signal_date_str}: P&L scan failed — {exc}")

            base.update(flat)
            base.update(pnl)
            output_rows.append(base)
            strategy = flat.get("selected_strategy", "NO_TRADE")
            target_hit = pnl.get("first_2pct_profit_datetime") or ""
            print(f"  {signal_date_str}: {strategy} — pnl={pnl.get('pnl_per_unit')!s:>8}  2%@{target_hit or 'N/A'}")

    finally:
        db.close()

    if not output_rows:
        print("No rows produced.")
        return {"rows": 0, "path": str(output_path)}

    out_df = pd.DataFrame(output_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    print(f"\nWrote {len(out_df)} rows → {output_path}")

    eligible = out_df[out_df["is_option_eligible"].astype(str).str.lower().isin(("true", "1"))]
    traded = eligible[eligible["selected_strategy"].notna() & (eligible["selected_strategy"] != "NO_TRADE")]
    if not traded.empty:
        wins = (traded["option_result"] == "PROFIT").sum()
        losses = (traded["option_result"] == "LOSS").sum()
        avg_ret = traded["return_pct"].mean()
        print(f"Backtest summary: {len(traded)} trades — W:{wins} L:{losses} — avg return: {avg_ret:.2f}%")

    return {"rows": len(out_df), "path": str(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run NIFTY option selection E2E and compute P&L backtest."
    )
    parser.add_argument(
        "--input", default=str(DEFAULT_INPUT),
        help=f"Prediction CSV from test_underlying_prediction.py. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    args = parser.parse_args()

    result = generate_option_selection_csv(
        input_path=Path(args.input),
        output_path=Path(args.output),
        underlying=args.underlying.upper(),
    )
    print(result)


if __name__ == "__main__":
    main()
